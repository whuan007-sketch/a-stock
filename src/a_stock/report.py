from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from a_stock.pipeline import PipelineResult


REPORT_COLUMNS = [
    "code",
    "name",
    "exchange",
    "board",
    "current_price",
    "total_market_cap_cny",
    "change_pct",
    "volume_ratio",
    "turnover_rate_pct",
    "volume_v0_lot",
    "volume_v1_lot",
    "volume_v2_lot",
    "expected_full_day_volume_lot",
    "previous_5d_average_volume_lot",
    "volume_score",
    "ma5",
    "ma10",
    "ma20",
    "ma5_slope",
    "ma10_slope",
    "ma20_slope",
    "trend_score",
    "vwap",
    "above_vwap_ratio",
    "afternoon_above_vwap_ratio",
    "benchmark_index",
    "relative_strength_intraday",
    "rs_score",
    "support_price",
    "resistance_price",
    "resistance_distance",
    "composite_score",
    "classification",
    "risk_warnings",
    "conditions_passed_count",
    "failure_conditions",
]


def _columns(frame: pd.DataFrame) -> list[str]:
    return [column for column in REPORT_COLUMNS if column in frame.columns]


def save_pipeline_report(result: PipelineResult, output_root: Path) -> Path:
    run_dir = output_root / result.market_date.isoformat() / result.cutoff.strftime("%H%M")
    suffix = 1
    original = run_dir
    while run_dir.exists():
        run_dir = original.with_name(f"{original.name}_{suffix}")
        suffix += 1
    run_dir.mkdir(parents=True, exist_ok=False)
    result.final_candidates[_columns(result.final_candidates)].to_csv(
        run_dir / "final_candidates.csv", index=False, encoding="utf-8-sig"
    )
    result.nearest_top10[_columns(result.nearest_top10)].to_csv(
        run_dir / "nearest_top10.csv", index=False, encoding="utf-8-sig"
    )
    result.scored.to_csv(run_dir / "all_analyzed_metrics.csv", index=False, encoding="utf-8-sig")
    result.elimination_log.to_csv(run_dir / "elimination_log.csv", index=False, encoding="utf-8-sig")
    result.hard.eliminated.to_csv(run_dir / "hard_filter_eliminated.csv", index=False, encoding="utf-8-sig")
    with (run_dir / "funnel.json").open("w", encoding="utf-8") as handle:
        json.dump(result.funnel, handle, ensure_ascii=False, indent=2)
    with (run_dir / "metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(result.metadata, handle, ensure_ascii=False, indent=2, default=str)

    try:
        with pd.ExcelWriter(run_dir / "screening_report.xlsx", engine="openpyxl") as writer:
            result.final_candidates[_columns(result.final_candidates)].to_excel(
                writer, sheet_name="final_candidates", index=False
            )
            result.nearest_top10[_columns(result.nearest_top10)].to_excel(writer, sheet_name="nearest_top10", index=False)
            result.scored.to_excel(writer, sheet_name="all_metrics", index=False)
            result.elimination_log.to_excel(writer, sheet_name="elimination_log", index=False)
            pd.DataFrame([result.funnel]).to_excel(writer, sheet_name="funnel", index=False)
    except ImportError as exc:
        raise RuntimeError("生成 Excel 报告需要安装 openpyxl，未生成伪造或替代文件") from exc
    return run_dir
