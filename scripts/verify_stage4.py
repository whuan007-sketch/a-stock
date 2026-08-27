from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from a_stock.config import load_config  # noqa: E402
from a_stock.daily_analysis import analyze_daily_stage  # noqa: E402
from a_stock.hard_filter import apply_hard_filter  # noqa: E402
from a_stock.intraday_analysis import analyze_intraday_stage  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="使用真实分钟行情验收第 4 阶段")
    parser.add_argument("--snapshot-dir", required=True)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    snapshot_dir = Path(args.snapshot_dir)
    if not snapshot_dir.is_absolute():
        snapshot_dir = ROOT / snapshot_dir
    with (snapshot_dir / "metadata.json").open("r", encoding="utf-8") as handle:
        metadata = json.load(handle)
    if metadata.get("future_data_used") is not False or metadata.get("synthetic_or_filled_market_data") is not False:
        raise RuntimeError("拒绝使用未通过真实性声明的快照")
    frame = pd.read_csv(snapshot_dir / "basic_quotes.csv", dtype={"code": "string"})
    evaluated_at = datetime.fromisoformat(str(metadata["fetched_at"]))
    config = load_config(ROOT / args.config)
    hard = apply_hard_filter(frame, config, evaluated_at=evaluated_at, source=str(metadata["source"]))
    daily = analyze_daily_stage(hard.passed, config, as_of_date=evaluated_at.date())
    intraday = analyze_intraday_stage(
        daily.passed,
        config,
        target_date=evaluated_at.date(),
        cutoff=time(15, 0),
    )
    if not intraday.minute_frames:
        raise RuntimeError("没有候选取得真实分钟行情")
    print("STAGE4_REAL_DATA_OK")
    print(json.dumps(intraday.metadata, ensure_ascii=False, indent=2))
    columns = [
        "code",
        "name",
        "minute_count",
        "vwap",
        "above_vwap_ratio",
        "afternoon_above_vwap_ratio",
        "high_pullback_pct",
        "surge_and_fade",
        "tail_recovery",
        "intraday_pass",
        "intraday_failures",
    ]
    print(intraday.evaluated[[column for column in columns if column in intraday.evaluated]].to_string(index=False))
    if intraday.source_failures:
        print(json.dumps(intraday.source_failures, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
