from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time as daytime
from typing import Any

import pandas as pd

from a_stock.config import AppConfig
from a_stock.providers.intraday import IntradayDataProvider


INDEX_BY_BOARD = {
    "sh_main": {"code": "000001", "exchange": "SH", "name": "上证指数"},
    "sz_main": {"code": "399001", "exchange": "SZ", "name": "深证成指"},
    "chinext": {"code": "399006", "exchange": "SZ", "name": "创业板指"},
}


@dataclass
class RelativeStrengthResult:
    evaluated: pd.DataFrame
    passed: pd.DataFrame
    eliminated: pd.DataFrame
    index_minute_frames: dict[str, pd.DataFrame]
    aligned_frames: dict[str, pd.DataFrame]
    source_failures: list[dict[str, str]]
    metadata: dict[str, Any]


def _clip_score(value: float, lower: float, upper: float) -> float:
    if upper <= lower:
        return 0.0
    return max(0.0, min(1.0, (value - lower) / (upper - lower)))


def calculate_relative_strength(stock: pd.DataFrame, index: pd.DataFrame, config: AppConfig) -> tuple[dict[str, Any], pd.DataFrame]:
    stock_frame = stock[["time", "price", "previous_close"]].rename(
        columns={"price": "stock_price", "previous_close": "stock_previous_close"}
    )
    index_frame = index[["time", "price", "previous_close"]].rename(
        columns={"price": "index_price", "previous_close": "index_previous_close"}
    )
    aligned = stock_frame.merge(index_frame, on="time", how="inner").sort_values("time").reset_index(drop=True)
    if len(aligned) < 120:
        raise ValueError(f"个股与指数同步分钟不足，仅 {len(aligned)} 根")
    if aligned[["stock_previous_close", "index_previous_close"]].isna().any().any():
        raise ValueError("个股或指数前收盘价缺失")
    aligned["stock_return_pct"] = (
        aligned["stock_price"] / aligned["stock_previous_close"].astype(float) - 1.0
    ) * 100.0
    aligned["index_return_pct"] = (
        aligned["index_price"] / aligned["index_previous_close"].astype(float) - 1.0
    ) * 100.0
    aligned["excess_return_pct"] = aligned["stock_return_pct"] - aligned["index_return_pct"]
    aligned["stock_delta_pct"] = aligned["stock_return_pct"].diff()
    aligned["index_delta_pct"] = aligned["index_return_pct"].diff()
    aligned["excess_delta_pct"] = aligned["excess_return_pct"].diff()

    index_down = aligned.loc[aligned["index_delta_pct"] < 0]
    index_up = aligned.loc[aligned["index_delta_pct"] > 0]
    downside_defense = float(index_down["excess_delta_pct"].mean()) if not index_down.empty else 0.0
    rebound_strength = float(index_up["excess_delta_pct"].mean()) if not index_up.empty else 0.0
    afternoon_start = pd.to_datetime(
        str(config.raw.get("intraday", {}).get("afternoon_start", "13:00")), format="%H:%M"
    ).time()
    afternoon = aligned.loc[aligned["time"] >= afternoon_start]
    afternoon_positive_ratio = float((afternoon["excess_return_pct"] > 0).mean())
    positive_ratio = float((aligned["excess_return_pct"] > 0).mean())
    final_excess = float(aligned.iloc[-1]["excess_return_pct"])
    relative_drawdown = aligned["excess_return_pct"].cummax() - aligned["excess_return_pct"]
    max_relative_drawdown = float(relative_drawdown.max())

    score = (
        25.0 * _clip_score(final_excess, -2.0, 2.0)
        + 20.0 * positive_ratio
        + 15.0 * _clip_score(downside_defense, -0.10, 0.10)
        + 15.0 * _clip_score(rebound_strength, -0.10, 0.10)
        + 15.0 * afternoon_positive_ratio
        + 10.0 * (1.0 - _clip_score(max_relative_drawdown, 0.0, 4.0))
    )
    relative_config = config.raw.get("relative_strength", {})
    minimum_score = float(relative_config.get("minimum_score", 55.0))
    minimum_excess = float(relative_config.get("minimum_excess_return_pct", 0.0))
    failures: list[str] = []
    if final_excess < minimum_excess:
        failures.append("relative_excess_return_below_min")
    if score < minimum_score:
        failures.append("rs_score_below_min")

    return (
        {
            "relative_strength_intraday": final_excess,
            "relative_positive_minute_ratio": positive_ratio,
            "relative_afternoon_positive_ratio": afternoon_positive_ratio,
            "downside_defense": downside_defense,
            "rebound_strength": rebound_strength,
            "relative_max_drawdown_pct": max_relative_drawdown,
            "rs_score": score,
            "relative_failures": ";".join(failures),
            "relative_pass": not failures,
        },
        aligned,
    )


def analyze_relative_strength(
    candidates: pd.DataFrame,
    stock_minute_frames: dict[str, pd.DataFrame],
    config: AppConfig,
    *,
    target_date: date,
    cutoff: daytime,
    preloaded_index_frames: dict[str, pd.DataFrame] | None = None,
) -> RelativeStrengthResult:
    source_order = tuple(config.raw.get("intraday", {}).get("source_order", ["tencent", "eastmoney"]))
    provider = IntradayDataProvider(config.data, source_order)
    index_frames: dict[str, pd.DataFrame] = {}
    aligned_frames: dict[str, pd.DataFrame] = {}
    source_failures: list[dict[str, str]] = []
    records: list[dict[str, Any]] = []

    for board in sorted(set(str(item) for item in candidates.get("board", pd.Series(dtype=str)).unique())):
        index_info = INDEX_BY_BOARD.get(board)
        if not index_info:
            continue
        try:
            if preloaded_index_frames is not None and board in preloaded_index_frames:
                index_frame = preloaded_index_frames[board].copy()
                if (index_frame["date"] > target_date).any() or (index_frame["time"] > cutoff).any():
                    raise ValueError("预载指数分钟数据超过目标日期或截止时刻")
                attempts: list[dict[str, str]] = []
            else:
                index_frame, attempts = provider.fetch_minutes(
                    index_info["code"],
                    index_info["exchange"],
                    target_date=target_date,
                    cutoff=cutoff,
                )
            index_frames[board] = index_frame
            for attempt in attempts:
                source_failures.append({"code": index_info["code"], "kind": "index", **attempt})
        except Exception as exc:
            source_failures.append(
                {"code": index_info["code"], "kind": "index", "source": "all", "error": str(exc)}
            )

    for row in candidates.to_dict("records"):
        base = dict(row)
        code = str(row["code"])
        board = str(row["board"])
        index_info = INDEX_BY_BOARD.get(board)
        base["benchmark_index"] = index_info["name"] if index_info else pd.NA
        try:
            stock = stock_minute_frames[code]
            index = index_frames[board]
            metrics, aligned = calculate_relative_strength(stock, index, config)
            base.update(metrics)
            aligned_frames[code] = aligned
        except Exception as exc:
            source_failures.append({"code": code, "kind": "relative", "source": "all", "error": str(exc)})
            base.update({"relative_failures": "relative_data_unavailable", "relative_pass": False})
        records.append(base)
    evaluated = pd.DataFrame(records)
    if evaluated.empty:
        evaluated = candidates.copy()
        evaluated["relative_failures"] = pd.Series(dtype="string")
        evaluated["relative_pass"] = pd.Series(dtype="bool")
    passed = evaluated.loc[evaluated["relative_pass"].eq(True)].copy().reset_index(drop=True)
    eliminated = evaluated.loc[~evaluated["relative_pass"].eq(True)].copy().reset_index(drop=True)
    return RelativeStrengthResult(
        evaluated=evaluated,
        passed=passed,
        eliminated=eliminated,
        index_minute_frames=index_frames,
        aligned_frames=aligned_frames,
        source_failures=source_failures,
        metadata={
            "stage": 5,
            "target_date": target_date.isoformat(),
            "cutoff": cutoff.strftime("%H:%M"),
            "input_count": int(len(candidates)),
            "passed_count": int(len(passed)),
            "eliminated_count": int(len(eliminated)),
            "future_data_used": False,
            "synthetic_or_filled_market_data": False,
        },
    )
