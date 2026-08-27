from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time as daytime
from typing import Any

import pandas as pd

from a_stock.config import AppConfig
from a_stock.providers.intraday import IntradayDataProvider


@dataclass
class IntradayStageResult:
    evaluated: pd.DataFrame
    passed: pd.DataFrame
    eliminated: pd.DataFrame
    minute_frames: dict[str, pd.DataFrame]
    source_failures: list[dict[str, str]]
    metadata: dict[str, Any]


def parse_clock(value: str) -> daytime:
    return pd.to_datetime(value, format="%H:%M").time()


def calculate_intraday_metrics(minutes: pd.DataFrame, config: AppConfig) -> dict[str, Any]:
    frame = minutes.copy()
    valid = frame.loc[frame["cumulative_volume_lot"] > 0].copy()
    if valid.empty:
        raise ValueError("分钟行情没有有效成交记录")
    valid["vwap"] = valid["cumulative_amount_cny"] / (valid["cumulative_volume_lot"] * 100.0)
    if valid["vwap"].isna().any() or (valid["vwap"] <= 0).any():
        raise ValueError("VWAP 无法由真实成交额/成交量计算")
    valid["above_vwap"] = valid["price"] > valid["vwap"]
    afternoon_start = parse_clock(str(config.raw.get("intraday", {}).get("afternoon_start", "13:00")))
    afternoon = valid.loc[valid["time"] >= afternoon_start]
    if afternoon.empty:
        raise ValueError("目标时点之前没有午后有效分钟")

    above_ratio = float(valid["above_vwap"].mean())
    afternoon_ratio = float(afternoon["above_vwap"].mean())
    last_price = float(valid.iloc[-1]["price"])
    high_price = float(valid["price"].max())
    high_pullback_pct = (high_price - last_price) / high_price * 100.0
    last_30 = valid.tail(30)
    tail_recovery_pct = (last_price / float(last_30["price"].min()) - 1.0) * 100.0
    threshold = float(config.raw.get("above_vwap_ratio_min", 0.70))
    pullback_limit = float(config.raw.get("intraday", {}).get("high_pullback_pct", 2.0))
    morning = valid.loc[valid["time"] < afternoon_start]
    morning_ratio = float(morning["above_vwap"].mean()) if not morning.empty else float("nan")
    morning_strong_afternoon_weak = bool(morning_ratio >= threshold and afternoon_ratio < threshold)
    surge_and_fade = bool(high_pullback_pct >= pullback_limit and last_price < float(valid.iloc[-1]["vwap"]))
    tail_recovery = bool(tail_recovery_pct >= 1.0 and last_price >= float(valid.iloc[-1]["vwap"]))

    failures: list[str] = []
    if above_ratio < threshold:
        failures.append("above_vwap_ratio_below_min")
    if afternoon_ratio < threshold:
        failures.append("afternoon_above_vwap_ratio_below_min")
    if surge_and_fade:
        failures.append("surge_and_fade")

    return {
        "intraday_data_source": str(valid.iloc[-1]["data_source"]),
        "minute_count": int(len(valid)),
        "vwap": float(valid.iloc[-1]["vwap"]),
        "above_vwap_ratio": above_ratio,
        "afternoon_above_vwap_ratio": afternoon_ratio,
        "morning_above_vwap_ratio": morning_ratio,
        "intraday_high_price": high_price,
        "high_pullback_pct": high_pullback_pct,
        "morning_strong_afternoon_weak": morning_strong_afternoon_weak,
        "tail_recovery": tail_recovery,
        "surge_and_fade": surge_and_fade,
        "intraday_failures": ";".join(failures),
        "intraday_pass": not failures,
    }


def analyze_intraday_stage(
    candidates: pd.DataFrame,
    config: AppConfig,
    *,
    target_date: date,
    cutoff: daytime,
    preloaded_frames: dict[str, pd.DataFrame] | None = None,
) -> IntradayStageResult:
    source_order = tuple(config.raw.get("intraday", {}).get("source_order", ["tencent", "eastmoney"]))
    provider = IntradayDataProvider(config.data, source_order)
    records: list[dict[str, Any]] = []
    minute_frames: dict[str, pd.DataFrame] = {}
    source_failures: list[dict[str, str]] = []
    for row in candidates.to_dict("records"):
        base = dict(row)
        code = str(row["code"])
        try:
            if preloaded_frames is not None and code in preloaded_frames:
                minutes = preloaded_frames[code].copy()
                if (minutes["date"] > target_date).any() or (minutes["time"] > cutoff).any():
                    raise ValueError("预载分钟数据超过目标日期或截止时刻")
                attempts: list[dict[str, str]] = []
            else:
                minutes, attempts = provider.fetch_minutes(
                    code,
                    str(row["exchange"]),
                    target_date=target_date,
                    cutoff=cutoff,
                )
            minute_frames[code] = minutes
            for attempt in attempts:
                source_failures.append({"code": code, **attempt})
            base.update(calculate_intraday_metrics(minutes, config))
        except Exception as exc:
            source_failures.append({"code": code, "source": "all", "error": str(exc)})
            base.update({"intraday_failures": "intraday_data_unavailable", "intraday_pass": False})
        records.append(base)
    evaluated = pd.DataFrame(records)
    passed = evaluated.loc[evaluated["intraday_pass"].eq(True)].copy().reset_index(drop=True)
    eliminated = evaluated.loc[~evaluated["intraday_pass"].eq(True)].copy().reset_index(drop=True)
    return IntradayStageResult(
        evaluated=evaluated,
        passed=passed,
        eliminated=eliminated,
        minute_frames=minute_frames,
        source_failures=source_failures,
        metadata={
            "stage": 4,
            "target_date": target_date.isoformat(),
            "cutoff": cutoff.strftime("%H:%M"),
            "input_count": int(len(candidates)),
            "passed_count": int(len(passed)),
            "eliminated_count": int(len(eliminated)),
            "future_data_used": False,
            "synthetic_or_filled_market_data": False,
        },
    )
