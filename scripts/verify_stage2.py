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
from a_stock.data_fetcher import Stage1DataFetcher  # noqa: E402
from a_stock.hard_filter import apply_hard_filter  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="使用真实全市场行情验收第 2 阶段硬筛")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--source", choices=("auto", "eastmoney", "tencent", "akshare"), default="auto")
    parser.add_argument("--snapshot-dir", help="读取已保存的真实第一阶段快照目录，不重新联网抓取")
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()

    config = load_config(ROOT / args.config)
    if args.snapshot_dir:
        snapshot_dir = Path(args.snapshot_dir)
        if not snapshot_dir.is_absolute():
            snapshot_dir = ROOT / snapshot_dir
        with (snapshot_dir / "metadata.json").open("r", encoding="utf-8") as handle:
            metadata = json.load(handle)
        if metadata.get("synthetic_or_filled_market_data") is not False:
            raise RuntimeError("快照未声明 synthetic_or_filled_market_data=false，拒绝使用")
        if metadata.get("future_data_used") is not False:
            raise RuntimeError("快照未声明 future_data_used=false，拒绝使用")
        frame = pd.read_csv(snapshot_dir / "basic_quotes.csv", dtype={"code": "string"})
        evaluated_at = datetime.fromisoformat(str(metadata["fetched_at"]))
        source = str(metadata["source"])
    else:
        snapshot = Stage1DataFetcher(config).fetch(args.source)
        frame = snapshot.basic_quotes
        evaluated_at = snapshot.fetched_at
        source = snapshot.source
    result = apply_hard_filter(
        frame,
        config,
        evaluated_at=evaluated_at,
        source=source,
    )
    output = None
    if not args.no_save:
        output = result.save(ROOT / "data" / "stage2")

    print("STAGE2_REAL_DATA_OK")
    print(json.dumps(result.summary, ensure_ascii=False, indent=2))
    if output:
        print(f"结果目录：{output}")
    print("硬条件全部满足的真实标的：")
    columns = [
        "code",
        "name",
        "board",
        "current_price",
        "total_market_cap_cny",
        "change_pct",
        "volume_ratio",
        "turnover_rate_pct",
    ]
    print(result.passed[columns].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
