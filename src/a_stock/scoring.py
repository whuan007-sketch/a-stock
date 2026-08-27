from __future__ import annotations

from typing import Any

import pandas as pd

from a_stock.config import AppConfig


CONDITION_COLUMNS = {
    "01_market_cap": "condition_01_market_cap",
    "02_change_pct": "condition_02_change_pct",
    "03_volume_ratio": "condition_03_volume_ratio",
    "04_turnover_rate": "condition_04_turnover_rate",
    "05_three_day_volume": "condition_05_three_day_volume",
    "06_moderate_volume": "condition_06_moderate_volume",
    "07_ma_bullish": "condition_07_ma_bullish",
    "08_ma_slope_and_trend": "condition_08_ma_slope_and_trend",
    "09_above_vwap": "condition_09_above_vwap",
    "10_afternoon_strength": "condition_10_afternoon_strength",
    "11_relative_strength": "condition_11_relative_strength",
    "12_support_resistance": "condition_12_support_resistance",
}


def _truth(value: Any) -> bool:
    return bool(pd.notna(value) and value)


def _between(value: Any, minimum: float, maximum: float) -> bool:
    return bool(pd.notna(value) and minimum <= float(value) <= maximum)


def _score_row(row: pd.Series, config: AppConfig) -> dict[str, Any]:
    hard = config.raw["hard_filter"]
    threshold = float(config.raw.get("above_vwap_ratio_min", 0.70))
    conditions = {
        "01_market_cap": _between(
            row.get("total_market_cap_cny"),
            float(hard["total_market_cap_cny"]["min"]),
            float(hard["total_market_cap_cny"]["max"]),
        ),
        "02_change_pct": _between(
            row.get("change_pct"), float(hard["change_pct"]["min"]), float(hard["change_pct"]["max"])
        ),
        "03_volume_ratio": _between(
            row.get("volume_ratio"), float(hard["volume_ratio"]["min"]), float(hard["volume_ratio"]["max"])
        ),
        "04_turnover_rate": _between(
            row.get("turnover_rate_pct"),
            float(hard["turnover_rate_pct"]["min"]),
            float(hard["turnover_rate_pct"]["max"]),
        ),
        "05_three_day_volume": _truth(row.get("three_day_volume_increasing")),
        "06_moderate_volume": _truth(row.get("moderate_volume")) and not _truth(row.get("abnormal_volume")),
        "07_ma_bullish": _truth(row.get("ma_bullish")),
        "08_ma_slope_and_trend": _truth(row.get("ma_slopes_up")) and _truth(row.get("trend_up")),
        "09_above_vwap": bool(
            pd.notna(row.get("above_vwap_ratio")) and float(row.get("above_vwap_ratio")) >= threshold
        ),
        "10_afternoon_strength": bool(
            pd.notna(row.get("afternoon_above_vwap_ratio"))
            and float(row.get("afternoon_above_vwap_ratio")) >= threshold
            and not _truth(row.get("surge_and_fade"))
        ),
        "11_relative_strength": _truth(row.get("relative_pass")),
        "12_support_resistance": _truth(row.get("support_resistance_pass")),
    }
    component_values: dict[str, Any] = {
        "trend": row.get("trend_score"),
        "volume": row.get("volume_score"),
        "vwap": (
            min(
                100.0,
                ((float(row.get("above_vwap_ratio")) + float(row.get("afternoon_above_vwap_ratio"))) / 2.0)
                / threshold
                * 100.0,
            )
            if pd.notna(row.get("above_vwap_ratio")) and pd.notna(row.get("afternoon_above_vwap_ratio"))
            else pd.NA
        ),
        "relative_strength": row.get("rs_score"),
        "support_resistance": row.get("support_resistance_score"),
        "price_structure": row.get("price_structure_score"),
    }
    missing_components = [name for name, value in component_values.items() if pd.isna(value)]
    weights = {str(key): float(value) for key, value in config.raw["scoring_weights"].items()}
    composite_score: Any = pd.NA
    if not missing_components:
        composite_score = sum(float(component_values[name]) * weights[name] / 100.0 for name in weights)

    failed = [name for name, passed in conditions.items() if not passed]
    all_passed = not failed
    a_min = float(config.raw.get("classification", {}).get("a_min_score", 80))
    b_min = float(config.raw.get("classification", {}).get("b_min_score", 65))
    if all_passed and pd.notna(composite_score) and float(composite_score) >= a_min:
        classification = "A_优先观察"
    elif all_passed and pd.notna(composite_score) and float(composite_score) >= b_min:
        classification = "B_次级观察"
    else:
        classification = "C_淘汰"

    risks: list[str] = []
    if _truth(row.get("abnormal_volume")):
        risks.append("异常爆量")
    if _truth(row.get("surge_and_fade")):
        risks.append("冲高回落")
    if _truth(row.get("morning_strong_afternoon_weak")):
        risks.append("上午强午后弱")
    if _truth(row.get("false_breakout")):
        risks.append("假突破")
    if "12_support_resistance" in failed:
        risks.append("压力位风险")
    if missing_components:
        risks.append("必要真实数据缺失")

    result: dict[str, Any] = {
        CONDITION_COLUMNS[name]: passed for name, passed in conditions.items()
    }
    result.update(
        {
            "conditions_passed_count": int(sum(conditions.values())),
            "all_12_conditions_passed": all_passed,
            "failure_conditions": ";".join(failed),
            "missing_score_components": ";".join(missing_components),
            "composite_score": composite_score,
            "classification": classification,
            "risk_warnings": ";".join(risks),
        }
    )
    return result


def score_candidates(frame: pd.DataFrame, config: AppConfig) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        base = row.to_dict()
        base.update(_score_row(row, config))
        records.append(base)
    scored = pd.DataFrame(records)
    if scored.empty:
        return frame.copy()
    scored = scored.sort_values(
        ["conditions_passed_count", "composite_score"],
        ascending=[False, False],
        na_position="last",
    ).reset_index(drop=True)
    return scored
