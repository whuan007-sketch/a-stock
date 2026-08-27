from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time as daytime
from typing import Any

import pandas as pd

from a_stock.config import AppConfig
from a_stock.providers.base import DataSourceError
from a_stock.providers.history import HistoricalDataProvider


@dataclass
class DailyStageResult:
    evaluated: pd.DataFrame
    passed: pd.DataFrame
    eliminated: pd.DataFrame
    daily_frames: dict[str, pd.DataFrame]
    source_failures: list[dict[str, str]]
    metadata: dict[str, Any]


def _swing_points(values: pd.Series, window: int, kind: str) -> list[tuple[int, float]]:
    points: list[tuple[int, float]] = []
    numeric = values.astype(float).reset_index(drop=True)
    for index in range(window, len(numeric) - window):
        center = float(numeric.iloc[index])
        neighborhood = numeric.iloc[index - window : index + window + 1]
        if kind == "high" and center == float(neighborhood.max()) and int((neighborhood == center).sum()) == 1:
            points.append((index, center))
        if kind == "low" and center == float(neighborhood.min()) and int((neighborhood == center).sum()) == 1:
            points.append((index, center))
    return points


def calculate_daily_metrics(
    daily: pd.DataFrame,
    *,
    as_of_date: date,
    config: AppConfig,
    cutoff: daytime = daytime(15, 0),
) -> dict[str, Any]:
    history = daily.loc[daily["date"] <= as_of_date].copy().sort_values("date").reset_index(drop=True)
    if history.empty or history.iloc[-1]["date"] != as_of_date:
        raise DataSourceError(f"日K没有目标交易日 {as_of_date}")

    ma_periods = [int(item) for item in config.raw.get("ma_periods", [5, 10, 20])]
    if ma_periods != [5, 10, 20]:
        raise DataSourceError("当前趋势模块要求 ma_periods 明确为 [5, 10, 20]")
    if len(history) < max(65, max(ma_periods) + 2):
        raise DataSourceError(f"历史日K不足，仅 {len(history)} 根")

    close = history["close"].astype(float)
    volume = history["volume_lot"].astype(float)
    ma_values: dict[int, pd.Series] = {period: close.rolling(period).mean() for period in ma_periods}
    ma_now = {period: float(series.iloc[-1]) for period, series in ma_values.items()}
    ma_previous = {period: float(series.iloc[-2]) for period, series in ma_values.items()}
    ma_slopes = {period: ma_now[period] - ma_previous[period] for period in ma_periods}

    v0, v1, v2 = (float(volume.iloc[-1]), float(volume.iloc[-2]), float(volume.iloc[-3]))
    previous_five_average = float(volume.iloc[-6:-1].mean())
    if cutoff < daytime(15, 0):
        if cutoff <= daytime(11, 30):
            elapsed_minutes = max(1, int((cutoff.hour * 60 + cutoff.minute) - (9 * 60 + 30)))
        else:
            elapsed_minutes = 120 + max(0, int((cutoff.hour * 60 + cutoff.minute) - (13 * 60)))
        market_minutes = int(config.raw.get("intraday", {}).get("market_minutes", 240))
        expected_volume = v0 * market_minutes / elapsed_minutes
        projection_method = f"累计成交量×{market_minutes}/{elapsed_minutes}（仅使用截止时点已成交量）"
    else:
        expected_volume = v0
        projection_method = "收盘实际成交量"
    expansion_ratio = expected_volume / previous_five_average if previous_five_average > 0 else float("nan")
    volume_config = config.raw.get("volume_expansion", {})
    expansion_min = float(volume_config.get("expected_to_ma5_min", 1.05))
    expansion_max = float(volume_config.get("expected_to_ma5_max", 1.50))
    abnormal_multiple = float(volume_config.get("abnormal_multiple", 2.0))

    trend_lookback = int(config.raw.get("trend", {}).get("lookback_days", 60))
    swing_window = int(config.raw.get("swing", {}).get("window", 3))
    trend_slice = history.tail(trend_lookback).reset_index(drop=True)
    highs = _swing_points(trend_slice["high"], swing_window, "high")
    lows = _swing_points(trend_slice["low"], swing_window, "low")
    higher_high = len(highs) >= 2 and highs[-1][1] > highs[-2][1]
    higher_low = len(lows) >= 2 and lows[-1][1] > lows[-2][1]
    bullish_ma = ma_now[5] > ma_now[10] > ma_now[20]
    ma_slopes_up = all(value > 0 for value in ma_slopes.values())
    trend_up = bool(higher_high and higher_low)

    trend_score = (
        (35 if bullish_ma else 0)
        + (25 if ma_slopes_up else 0)
        + (20 if higher_high else 0)
        + (20 if higher_low else 0)
    )
    volume_increasing = expected_volume > v1 > v2
    moderate_volume = expansion_min <= expansion_ratio <= expansion_max
    abnormal_volume = expansion_ratio >= abnormal_multiple
    volume_failures: list[str] = []
    if not volume_increasing:
        volume_failures.append("three_day_volume_not_increasing")
    if abnormal_volume:
        volume_failures.append("abnormal_volume_expansion")
    elif not moderate_volume:
        volume_failures.append("moderate_volume_range_failed")
    trend_failures: list[str] = []
    if not bullish_ma:
        trend_failures.append("ma5_ma10_ma20_bullish_failed")
    if not ma_slopes_up:
        trend_failures.append("ma_slopes_not_all_up")
    if not trend_up:
        trend_failures.append("higher_high_higher_low_failed")
    failures = volume_failures + trend_failures

    return {
        "daily_data_source": str(history.iloc[-1]["data_source"]),
        "daily_adjustment": str(history.iloc[-1]["adjustment"]),
        "daily_last_date": as_of_date.isoformat(),
        "volume_v0_lot": v0,
        "volume_v1_lot": v1,
        "volume_v2_lot": v2,
        "expected_full_day_volume_lot": expected_volume,
        "volume_projection_method": projection_method,
        "previous_5d_average_volume_lot": previous_five_average,
        "volume_expansion_ratio": expansion_ratio,
        "three_day_volume_increasing": volume_increasing,
        "moderate_volume": moderate_volume,
        "abnormal_volume": abnormal_volume,
        "volume_score": max(0.0, 100.0 - abs(expansion_ratio - (expansion_min + expansion_max) / 2) * 100),
        "volume_failures": ";".join(volume_failures),
        "volume_stage_pass": not volume_failures,
        "ma5": ma_now[5],
        "ma10": ma_now[10],
        "ma20": ma_now[20],
        "ma5_slope": ma_slopes[5],
        "ma10_slope": ma_slopes[10],
        "ma20_slope": ma_slopes[20],
        "ma_bullish": bullish_ma,
        "ma_slopes_up": ma_slopes_up,
        "last_swing_high": highs[-1][1] if highs else pd.NA,
        "previous_swing_high": highs[-2][1] if len(highs) >= 2 else pd.NA,
        "last_swing_low": lows[-1][1] if lows else pd.NA,
        "previous_swing_low": lows[-2][1] if len(lows) >= 2 else pd.NA,
        "higher_high": higher_high,
        "higher_low": higher_low,
        "trend_up": trend_up,
        "trend_score": float(trend_score),
        "trend_failures": ";".join(trend_failures),
        "trend_stage_pass": not trend_failures,
        "daily_failures": ";".join(failures),
        "daily_pass": not failures,
    }


def analyze_daily_stage(
    candidates: pd.DataFrame,
    config: AppConfig,
    *,
    as_of_date: date,
    cutoff: daytime = daytime(15, 0),
    preloaded_frames: dict[str, pd.DataFrame] | None = None,
) -> DailyStageResult:
    historical_raw = config.raw.get("historical", {})
    source_order = tuple(historical_raw.get("source_order", ["tencent", "eastmoney", "akshare"]))
    lookback = int(historical_raw.get("daily_lookback_calendar_days", 240))
    minimum_rows = int(historical_raw.get("minimum_daily_rows", 65))
    provider = HistoricalDataProvider(config.data, source_order)
    records: list[dict[str, Any]] = []
    daily_frames: dict[str, pd.DataFrame] = {}
    source_failures: list[dict[str, str]] = []

    for row in candidates.to_dict("records"):
        base = dict(row)
        try:
            code = str(row["code"])
            if preloaded_frames is not None and code in preloaded_frames:
                daily = preloaded_frames[code].copy()
                if (daily["date"] > as_of_date).any():
                    raise ValueError("预载日K包含目标日之后的数据")
                attempts: list[dict[str, str]] = []
            else:
                daily, attempts = provider.fetch_daily(
                    code,
                    str(row["exchange"]),
                    as_of_date=as_of_date,
                    lookback_calendar_days=lookback,
                    minimum_rows=minimum_rows,
                )
            for attempt in attempts:
                source_failures.append({"code": str(row["code"]), **attempt})
            daily_frames[code] = daily
            base.update(calculate_daily_metrics(daily, as_of_date=as_of_date, config=config, cutoff=cutoff))
        except Exception as exc:
            source_failures.append({"code": str(row["code"]), "source": "all", "error": str(exc)})
            base.update({"daily_failures": "daily_data_unavailable", "daily_pass": False})
        records.append(base)

    evaluated = pd.DataFrame(records)
    passed = evaluated.loc[evaluated["daily_pass"].eq(True)].copy().reset_index(drop=True)
    eliminated = evaluated.loc[~evaluated["daily_pass"].eq(True)].copy().reset_index(drop=True)
    return DailyStageResult(
        evaluated=evaluated,
        passed=passed,
        eliminated=eliminated,
        daily_frames=daily_frames,
        source_failures=source_failures,
        metadata={
            "stage": 3,
            "as_of_date": as_of_date.isoformat(),
            "input_count": int(len(candidates)),
            "passed_count": int(len(passed)),
            "eliminated_count": int(len(eliminated)),
            "daily_adjustment": "none",
            "future_data_used": False,
            "synthetic_or_filled_market_data": False,
        },
    )
