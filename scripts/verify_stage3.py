from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from a_stock.config import load_config  # noqa: E402
from a_stock.daily_analysis import analyze_daily_stage  # noqa: E402
from a_stock.hard_filter import apply_hard_filter  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="使用真实日K验收第 3 阶段")
    parser.add_argument("--snapshot-dir", required=True)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    snapshot_dir = Path(args.snapshot_dir)
    if not snapshot_dir.is_absolute():
        snapshot_dir = ROOT / snapshot_dir
    with (snapshot_dir / "metadata.json").open("r", encoding="utf-8") as handle:
        snapshot_metadata = json.load(handle)
    if snapshot_metadata.get("synthetic_or_filled_market_data") is not False:
        raise RuntimeError("拒绝使用未声明为真实数据的快照")
    frame = pd.read_csv(snapshot_dir / "basic_quotes.csv", dtype={"code": "string"})
    evaluated_at = datetime.fromisoformat(str(snapshot_metadata["fetched_at"]))
    config = load_config(ROOT / args.config)
    hard = apply_hard_filter(
        frame,
        config,
        evaluated_at=evaluated_at,
        source=str(snapshot_metadata["source"]),
    )
    daily = analyze_daily_stage(hard.passed, config, as_of_date=evaluated_at.date())
    available = daily.evaluated["daily_data_source"].notna().sum() if "daily_data_source" in daily.evaluated else 0
    if available == 0:
        raise RuntimeError("所有候选的真实日K均不可得")

    print("STAGE3_REAL_DATA_OK")
    print(json.dumps({**daily.metadata, "daily_data_available_count": int(available)}, ensure_ascii=False, indent=2))
    columns = [
        "code",
        "name",
        "volume_v0_lot",
        "volume_v1_lot",
        "volume_v2_lot",
        "volume_expansion_ratio",
        "ma5",
        "ma10",
        "ma20",
        "trend_score",
        "daily_pass",
        "daily_failures",
    ]
    print(daily.evaluated[[column for column in columns if column in daily.evaluated]].to_string(index=False))
    if daily.source_failures:
        print("DATA_SOURCE_FAILURES")
        print(json.dumps(daily.source_failures, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
