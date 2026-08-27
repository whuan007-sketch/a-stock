from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from a_stock.config import AppConfig
from a_stock.providers.base import DataSourceError


HARD_FILTER_ORDER = (
    "total_market_cap_cny",
    "change_pct",
    "volume_ratio",
    "turnover_rate_pct",
)

FAILURE_CODES = {
    "total_market_cap_cny": "market_cap_out_of_range",
    "change_pct": "change_pct_out_of_range",
    "volume_ratio": "volume_ratio_out_of_range",
    "turnover_rate_pct": "turnover_rate_out_of_range",
}


@dataclass
class HardFilterResult:
    evaluated_at: datetime
    source: str
    universe: pd.DataFrame
    passed: pd.DataFrame
    eliminated: pd.DataFrame
    independent_failure_counts: dict[str, int]
    sequential_elimination_counts: dict[str, int]
    unit_validation: dict[str, Any]

    @property
    def summary(self) -> dict[str, Any]:
        return {
            "stage": 2,
            "evaluated_at": self.evaluated_at.isoformat(),
            "source": self.source,
            "universe_count": int(len(self.universe)),
            "independent_failure_counts": self.independent_failure_counts,
            "sequential_elimination_counts": self.sequential_elimination_counts,
            "all_hard_conditions_passed_count": int(len(self.passed)),
            "unit_validation": self.unit_validation,
            "future_data_used": False,
            "synthetic_or_filled_market_data": False,
        }

    def save(self, output_dir: Path) -> Path:
        run_dir = output_dir / self.evaluated_at.strftime("%Y-%m-%d") / self.evaluated_at.strftime("%H%M%S")
        suffix = 1
        while run_dir.exists():
            run_dir = run_dir.with_name(f"{self.evaluated_at.strftime('%H%M%S')}_{suffix}")
            suffix += 1
        run_dir.mkdir(parents=True, exist_ok=False)
        self.passed.to_csv(run_dir / "hard_filter_passed.csv", index=False, encoding="utf-8-sig")
        self.eliminated.to_csv(run_dir / "hard_filter_eliminated.csv", index=False, encoding="utf-8-sig")
        with (run_dir / "hard_filter_summary.json").open("w", encoding="utf-8") as handle:
            json.dump(self.summary, handle, ensure_ascii=False, indent=2)
        return run_dir


def _limits(config: AppConfig, field: str) -> tuple[float, float]:
    section = config.raw.get("hard_filter", {})
    raw = section.get(field, {}) if isinstance(section, dict) else {}
    try:
        minimum = float(raw["min"])
        maximum = float(raw["max"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DataSourceError(f"hard_filter.{field} 必须配置 min/max") from exc
    if minimum > maximum:
        raise DataSourceError(f"hard_filter.{field} 的 min 不能大于 max")
    return minimum, maximum


def validate_hard_filter_units(frame: pd.DataFrame) -> dict[str, Any]:
    required = set(HARD_FILTER_ORDER) | {"current_price", "previous_close", "data_source"}
    missing = required - set(frame.columns)
    if missing:
        raise DataSourceError(f"硬筛所需字段缺失: {sorted(missing)}")
    if frame.empty:
        raise DataSourceError("硬筛输入股票池为空")

    numeric = frame[list(HARD_FILTER_ORDER) + ["current_price", "previous_close"]]
    missing_counts = {column: int(numeric[column].isna().sum()) for column in numeric.columns}
    if any(missing_counts.values()):
        raise DataSourceError(f"硬筛关键字段存在缺失，禁止填充: {missing_counts}")

    cap = frame["total_market_cap_cny"].astype(float)
    cap_median = float(cap.median())
    if not 1_000_000_000 <= cap_median <= 2_000_000_000_000:
        raise DataSourceError(f"总市值数量级异常，中位数={cap_median:.2f}，期望单位为人民币元")

    current = frame["current_price"].astype(float)
    previous = frame["previous_close"].astype(float)
    recomputed = (current / previous - 1.0) * 100.0
    difference = (recomputed - frame["change_pct"].astype(float)).abs()
    p95_difference = float(difference.quantile(0.95))
    max_difference = float(difference.max())
    if p95_difference > 0.15:
        raise DataSourceError(
            f"涨跌幅字段与价格重算偏差异常，95分位误差={p95_difference:.4f} 个百分点"
        )

    volume_ratio = frame["volume_ratio"].astype(float)
    if (volume_ratio < 0).any() or float(volume_ratio.quantile(0.99)) > 30:
        raise DataSourceError("量比范围异常；应为无量纲倍数而不是百分数")

    turnover = frame["turnover_rate_pct"].astype(float)
    if (turnover < 0).any() or float(turnover.max()) > 100:
        raise DataSourceError("换手率范围异常；应为百分数数值 5 表示 5%")

    sources = sorted(str(item) for item in frame["data_source"].dropna().unique())
    return {
        "total_market_cap_cny": {
            "unit": "人民币元",
            "median": cap_median,
            "validation": "全股票池中位数必须处于10亿至2万亿元",
        },
        "change_pct": {
            "unit": "百分点（1.0 表示 1%）",
            "formula": "(current_price / previous_close - 1) * 100",
            "p95_absolute_error_percentage_points": p95_difference,
            "max_absolute_error_percentage_points": max_difference,
        },
        "volume_ratio": {
            "unit": "无量纲倍数",
            "definition": "当前每分钟平均成交量 / 过去5个交易日每分钟平均成交量（数据源原始量比字段）",
            "p99": float(volume_ratio.quantile(0.99)),
        },
        "turnover_rate_pct": {
            "unit": "百分点（5.0 表示 5%）",
            "maximum": float(turnover.max()),
        },
        "sources": sources,
    }


def apply_hard_filter(
    frame: pd.DataFrame,
    config: AppConfig,
    *,
    evaluated_at: datetime,
    source: str,
) -> HardFilterResult:
    unit_validation = validate_hard_filter_units(frame)
    masks: dict[str, pd.Series] = {}
    for field in HARD_FILTER_ORDER:
        minimum, maximum = _limits(config, field)
        masks[field] = frame[field].astype(float).between(minimum, maximum, inclusive="both")

    evaluated = frame.copy()
    failure_lists: list[list[str]] = []
    for index in evaluated.index:
        failure_lists.append(
            [FAILURE_CODES[field] for field in HARD_FILTER_ORDER if not bool(masks[field].loc[index])]
        )
    evaluated["hard_failures"] = [";".join(items) for items in failure_lists]
    evaluated["hard_pass"] = evaluated["hard_failures"].eq("")

    independent = {FAILURE_CODES[field]: int((~masks[field]).sum()) for field in HARD_FILTER_ORDER}
    remaining = pd.Series(True, index=frame.index)
    sequential: dict[str, int] = {}
    for field in HARD_FILTER_ORDER:
        eliminated_here = remaining & ~masks[field]
        sequential[FAILURE_CODES[field]] = int(eliminated_here.sum())
        remaining &= masks[field]

    passed = evaluated.loc[evaluated["hard_pass"]].copy().reset_index(drop=True)
    eliminated = evaluated.loc[~evaluated["hard_pass"]].copy().reset_index(drop=True)
    return HardFilterResult(
        evaluated_at=evaluated_at,
        source=source,
        universe=evaluated,
        passed=passed,
        eliminated=eliminated,
        independent_failure_counts=independent,
        sequential_elimination_counts=sequential,
        unit_validation=unit_validation,
    )
