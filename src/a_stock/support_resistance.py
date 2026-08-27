from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from a_stock.config import AppConfig


@dataclass
class SupportResistanceResult:
    evaluated: pd.DataFrame
    passed: pd.DataFrame
    eliminated: pd.DataFrame
    metadata: dict[str, Any]


def _swing_values(series: pd.Series, window: int, kind: str) -> list[float]:
    values = series.astype(float).reset_index(drop=True)
    points: list[float] = []
    for index in range(window, len(values) - window):
        center = float(values.iloc[index])
        neighborhood = values.iloc[index - window : index + window + 1]
        if kind == "high" and center == float(neighborhood.max()) and int((neighborhood == center).sum()) == 1:
            points.append(center)
        if kind == "low" and center == float(neighborhood.min()) and int((neighborhood == center).sum()) == 1:
            points.append(center)
    return points


def _platform_levels(closes: pd.Series, tolerance: float, minimum_touches: int) -> list[float]:
    values = sorted(float(item) for item in closes.dropna())
    levels: list[float] = []
    used: set[int] = set()
    for index, value in enumerate(values):
        if index in used:
            continue
        cluster = [j for j, other in enumerate(values) if abs(other / value - 1.0) <= tolerance]
        if len(cluster) >= minimum_touches:
            levels.append(sum(values[j] for j in cluster) / len(cluster))
            used.update(cluster)
    return levels


def calculate_support_resistance(
    row: dict[str, Any],
    daily: pd.DataFrame,
    config: AppConfig,
) -> dict[str, Any]:
    support_config = config.raw.get("support_resistance", {})
    lookback = int(support_config.get("lookback_days", config.raw.get("trend", {}).get("lookback_days", 60)))
    window = int(config.raw.get("swing", {}).get("window", 3))
    tolerance = float(support_config.get("platform_tolerance", 0.015))
    minimum_touches = int(support_config.get("platform_minimum_touches", 3))
    history = daily.tail(lookback).copy().reset_index(drop=True)
    if len(history) < 20:
        raise ValueError("支撑压力计算至少需要20根真实日K")
    current_price = float(row["current_price"])
    swing_highs = _swing_values(history["high"], window, "high")
    swing_lows = _swing_values(history["low"], window, "low")
    platforms = _platform_levels(history["close"], tolerance, minimum_touches)
    typical = (history["high"].astype(float) + history["low"].astype(float) + history["close"].astype(float)) / 3.0
    volume = history["volume_lot"].astype(float)
    volume_center = float((typical * volume).sum() / volume.sum()) if float(volume.sum()) > 0 else float("nan")

    gap_supports: list[float] = []
    gap_resistances: list[float] = []
    for index in range(1, len(history)):
        previous_high = float(history.iloc[index - 1]["high"])
        previous_low = float(history.iloc[index - 1]["low"])
        current_high = float(history.iloc[index]["high"])
        current_low = float(history.iloc[index]["low"])
        if current_low > previous_high:
            gap_supports.extend([previous_high, current_low])
        elif current_high < previous_low:
            gap_resistances.extend([current_high, previous_low])

    support_candidates = swing_lows + [level for level in platforms if level <= current_price] + gap_supports
    resistance_candidates = swing_highs + [level for level in platforms if level >= current_price] + gap_resistances
    if pd.notna(volume_center):
        (support_candidates if volume_center <= current_price else resistance_candidates).append(volume_center)
    supports_below = [value for value in support_candidates if value <= current_price]
    resistances_above = [value for value in resistance_candidates if value > current_price]
    support_price = max(supports_below) if supports_below else pd.NA
    resistance_price = min(resistances_above) if resistances_above else pd.NA
    resistance_distance = (
        (float(resistance_price) - current_price) / current_price if pd.notna(resistance_price) else pd.NA
    )
    support_distance = (
        (current_price - float(support_price)) / current_price if pd.notna(support_price) else pd.NA
    )

    prior = history.iloc[:-1] if len(history) > 1 else history
    prior_high = float(prior["high"].max())
    current_high = float(history.iloc[-1]["high"])
    current_close = float(history.iloc[-1]["close"])
    broke_prior_high = current_high > prior_high
    effective_breakout = bool(
        current_close > prior_high
        and bool(row.get("moderate_volume", False))
        and not bool(row.get("surge_and_fade", False))
    )
    false_breakout = bool(broke_prior_high and current_close <= prior_high)
    minimum_distance = float(config.raw.get("min_resistance_distance", 0.03))
    distance_ok = bool(pd.isna(resistance_distance) or float(resistance_distance) >= minimum_distance)
    passed = bool((distance_ok or effective_breakout) and not false_breakout)
    failures: list[str] = []
    if not distance_ok and not effective_breakout:
        failures.append("resistance_too_close")
    if false_breakout:
        failures.append("false_breakout")

    distance_score = 100.0 if pd.isna(resistance_distance) else min(100.0, float(resistance_distance) / minimum_distance * 100.0)
    structure_score = 50.0 + (30.0 if effective_breakout else 0.0) + (10.0 if pd.notna(support_price) else 0.0)
    if false_breakout:
        structure_score -= 40.0
    return {
        "support_price": support_price,
        "resistance_price": resistance_price,
        "support_distance": support_distance,
        "resistance_distance": resistance_distance,
        "last_lookback_high": prior_high,
        "volume_price_center": volume_center,
        "platform_level_count": int(len(platforms)),
        "gap_support_count": int(len(gap_supports) // 2),
        "gap_resistance_count": int(len(gap_resistances) // 2),
        "effective_breakout": effective_breakout,
        "false_breakout": false_breakout,
        "support_resistance_score": distance_score,
        "price_structure_score": max(0.0, min(100.0, structure_score)),
        "support_resistance_failures": ";".join(failures),
        "support_resistance_pass": passed,
    }


def analyze_support_resistance(
    candidates: pd.DataFrame,
    daily_frames: dict[str, pd.DataFrame],
    config: AppConfig,
) -> SupportResistanceResult:
    records: list[dict[str, Any]] = []
    for row in candidates.to_dict("records"):
        base = dict(row)
        code = str(row["code"])
        try:
            base.update(calculate_support_resistance(base, daily_frames[code], config))
        except Exception as exc:
            base.update(
                {
                    "support_resistance_failures": f"support_resistance_data_unavailable:{exc}",
                    "support_resistance_pass": False,
                }
            )
        records.append(base)
    evaluated = pd.DataFrame(records)
    if evaluated.empty:
        evaluated = candidates.copy()
        evaluated["support_resistance_failures"] = pd.Series(dtype="string")
        evaluated["support_resistance_pass"] = pd.Series(dtype="bool")
    passed = evaluated.loc[evaluated["support_resistance_pass"].eq(True)].copy().reset_index(drop=True)
    eliminated = evaluated.loc[~evaluated["support_resistance_pass"].eq(True)].copy().reset_index(drop=True)
    return SupportResistanceResult(
        evaluated=evaluated,
        passed=passed,
        eliminated=eliminated,
        metadata={
            "stage": 6,
            "input_count": int(len(candidates)),
            "passed_count": int(len(passed)),
            "eliminated_count": int(len(eliminated)),
            "future_data_used": False,
            "synthetic_or_filled_market_data": False,
        },
    )
