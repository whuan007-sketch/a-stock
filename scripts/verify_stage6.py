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
from a_stock.relative_strength import analyze_relative_strength  # noqa: E402
from a_stock.support_resistance import analyze_support_resistance  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="用真实日K验收第 6 阶段支撑压力")
    parser.add_argument("--snapshot-dir", required=True)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    snapshot_dir = Path(args.snapshot_dir)
    if not snapshot_dir.is_absolute():
        snapshot_dir = ROOT / snapshot_dir
    with (snapshot_dir / "metadata.json").open("r", encoding="utf-8") as handle:
        metadata = json.load(handle)
    frame = pd.read_csv(snapshot_dir / "basic_quotes.csv", dtype={"code": "string"})
    evaluated_at = datetime.fromisoformat(str(metadata["fetched_at"]))
    config = load_config(ROOT / args.config)
    hard = apply_hard_filter(frame, config, evaluated_at=evaluated_at, source=str(metadata["source"]))
    daily = analyze_daily_stage(hard.passed, config, as_of_date=evaluated_at.date())
    intraday = analyze_intraday_stage(daily.passed, config, target_date=evaluated_at.date(), cutoff=time(15, 0))
    relative = analyze_relative_strength(
        intraday.passed,
        intraday.minute_frames,
        config,
        target_date=evaluated_at.date(),
        cutoff=time(15, 0),
    )
    support = analyze_support_resistance(relative.passed, daily.daily_frames, config)
    if support.evaluated.empty:
        raise RuntimeError("没有真实候选可验收支撑压力")
    print("STAGE6_REAL_DATA_OK")
    print(json.dumps(support.metadata, ensure_ascii=False, indent=2))
    columns = [
        "code",
        "name",
        "support_price",
        "resistance_price",
        "resistance_distance",
        "volume_price_center",
        "platform_level_count",
        "gap_support_count",
        "gap_resistance_count",
        "effective_breakout",
        "false_breakout",
        "support_resistance_pass",
        "support_resistance_failures",
    ]
    print(support.evaluated[[column for column in columns if column in support.evaluated]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
