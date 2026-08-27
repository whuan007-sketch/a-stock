from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time as daytime
from typing import Any

import pandas as pd

from a_stock.config import AppConfig
from a_stock.daily_analysis import DailyStageResult, analyze_daily_stage
from a_stock.data_fetcher import Stage1Snapshot
from a_stock.hard_filter import HARD_FILTER_ORDER, HardFilterResult, apply_hard_filter
from a_stock.intraday_analysis import IntradayStageResult, analyze_intraday_stage
from a_stock.relative_strength import RelativeStrengthResult, analyze_relative_strength
from a_stock.scoring import score_candidates
from a_stock.support_resistance import SupportResistanceResult, analyze_support_resistance


@dataclass
class PipelineResult:
    snapshot: Stage1Snapshot
    market_date: date
    cutoff: daytime
    hard: HardFilterResult
    daily: DailyStageResult
    intraday: IntradayStageResult
    relative: RelativeStrengthResult
    support_resistance: SupportResistanceResult
    scored: pd.DataFrame
    final_candidates: pd.DataFrame
    nearest_top10: pd.DataFrame
    elimination_log: pd.DataFrame
    funnel: dict[str, int]
    metadata: dict[str, Any]


def _distance(value: float, minimum: float, maximum: float) -> float:
    width = maximum - minimum
    if minimum <= value <= maximum:
        return 0.0
    if value < minimum:
        return (minimum - value) / width
    return (value - maximum) / width


def select_analysis_pool(hard: HardFilterResult, config: AppConfig) -> pd.DataFrame:
    frame = hard.universe.copy()
    distances = pd.Series(0.0, index=frame.index)
    for field in HARD_FILTER_ORDER:
        limits = config.raw["hard_filter"][field]
        distances += frame[field].astype(float).map(
            lambda value: _distance(value, float(limits["min"]), float(limits["max"]))
        )
    frame["hard_distance"] = distances
    frame["hard_failure_count"] = frame["hard_failures"].map(
        lambda value: 0 if not value else len(str(value).split(";"))
    )
    maximum = int(config.raw.get("reporting", {}).get("nearest_analysis_pool_size", 50))
    hard_passed = frame.loc[frame["hard_pass"]].copy()
    near = frame.loc[~frame["hard_pass"]].sort_values(
        ["hard_failure_count", "hard_distance"], ascending=[True, True]
    ).head(maximum)
    return pd.concat([hard_passed, near], ignore_index=True).drop_duplicates("code", keep="first")


def _truth_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(False, index=frame.index)
    return frame[column].eq(True)


def _append_reasons(records: list[dict[str, Any]], frame: pd.DataFrame, stage: str, column: str) -> None:
    if column not in frame:
        return
    for row in frame.loc[frame[column].astype("string").ne("")].to_dict("records"):
        for reason in str(row[column]).split(";"):
            if reason:
                records.append(
                    {
                        "code": str(row.get("code", "")),
                        "name": row.get("name", ""),
                        "stage": stage,
                        "reason": reason,
                    }
                )


def build_elimination_log(snapshot: Stage1Snapshot, hard: HardFilterResult, scored: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    _append_reasons(records, snapshot.excluded_stocks, "universe", "exclusion_reason")
    _append_reasons(records, hard.universe, "hard_filter", "hard_failures")
    _append_reasons(records, scored, "volume", "volume_failures")
    _append_reasons(records, scored, "trend", "trend_failures")
    _append_reasons(records, scored, "intraday", "intraday_failures")
    _append_reasons(records, scored, "relative_strength", "relative_failures")
    _append_reasons(records, scored, "support_resistance", "support_resistance_failures")
    return pd.DataFrame(records, columns=["code", "name", "stage", "reason"])


def run_full_pipeline(
    snapshot: Stage1Snapshot,
    config: AppConfig,
    *,
    market_date: date,
    cutoff: daytime,
    preloaded_daily_frames: dict[str, pd.DataFrame] | None = None,
    preloaded_stock_minutes: dict[str, pd.DataFrame] | None = None,
    preloaded_index_minutes: dict[str, pd.DataFrame] | None = None,
) -> PipelineResult:
    hard = apply_hard_filter(
        snapshot.basic_quotes,
        config,
        evaluated_at=snapshot.fetched_at,
        source=snapshot.source,
    )
    analysis_pool = select_analysis_pool(hard, config)
    daily = analyze_daily_stage(
        analysis_pool,
        config,
        as_of_date=market_date,
        cutoff=cutoff,
        preloaded_frames=preloaded_daily_frames,
    )
    intraday = analyze_intraday_stage(
        daily.evaluated,
        config,
        target_date=market_date,
        cutoff=cutoff,
        preloaded_frames=preloaded_stock_minutes,
    )
    relative = analyze_relative_strength(
        intraday.evaluated,
        intraday.minute_frames,
        config,
        target_date=market_date,
        cutoff=cutoff,
        preloaded_index_frames=preloaded_index_minutes,
    )
    support = analyze_support_resistance(relative.evaluated, daily.daily_frames, config)
    scored = score_candidates(support.evaluated, config)
    final_candidates = scored.loc[
        scored.get("all_12_conditions_passed", pd.Series(False, index=scored.index)).eq(True)
        & scored.get("classification", pd.Series("", index=scored.index)).isin(["A_优先观察", "B_次级观察"])
    ].copy().reset_index(drop=True)
    nearest_top10 = scored.head(10).copy().reset_index(drop=True)

    strict = scored["hard_pass"].eq(True)
    volume = strict & _truth_series(scored, "volume_stage_pass")
    trend = volume & _truth_series(scored, "trend_stage_pass")
    intraday_mask = trend & _truth_series(scored, "intraday_pass")
    relative_mask = intraday_mask & _truth_series(scored, "relative_pass")
    support_mask = relative_mask & _truth_series(scored, "support_resistance_pass")
    final_mask = support_mask & _truth_series(scored, "all_12_conditions_passed") & scored["classification"].isin(
        ["A_优先观察", "B_次级观察"]
    )
    funnel = {
        "raw_market": int(len(snapshot.raw_quotes)),
        "target_universe": int(len(snapshot.basic_quotes)),
        "hard_filter": int(strict.sum()),
        "volume": int(volume.sum()),
        "trend": int(trend.sum()),
        "intraday": int(intraday_mask.sum()),
        "relative_strength": int(relative_mask.sum()),
        "support_resistance": int(support_mask.sum()),
        "final_candidates": int(final_mask.sum()),
    }
    elimination_log = build_elimination_log(snapshot, hard, scored)
    metadata = {
        "stages_completed": list(range(1, 8)),
        "market_date": market_date.isoformat(),
        "cutoff": cutoff.strftime("%H:%M"),
        "quote_fetched_at": snapshot.fetched_at.isoformat(),
        "quote_source": snapshot.source,
        "analysis_pool_count": int(len(analysis_pool)),
        "funnel": funnel,
        "future_data_used": False,
        "synthetic_or_filled_market_data": False,
    }
    return PipelineResult(
        snapshot=snapshot,
        market_date=market_date,
        cutoff=cutoff,
        hard=hard,
        daily=daily,
        intraday=intraday,
        relative=relative,
        support_resistance=support,
        scored=scored,
        final_candidates=final_candidates,
        nearest_top10=nearest_top10,
        elimination_log=elimination_log,
        funnel=funnel,
        metadata=metadata,
    )
