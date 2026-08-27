from __future__ import annotations

import argparse
import json
import sys
from datetime import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from a_stock.config import load_config  # noqa: E402
from a_stock.pipeline import run_full_pipeline  # noqa: E402
from a_stock.report import save_pipeline_report  # noqa: E402
from a_stock.snapshot_io import load_real_snapshot  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="用真实行情验收第 7 阶段完整评分与报告")
    parser.add_argument("--snapshot-dir", required=True)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    snapshot_dir = Path(args.snapshot_dir)
    if not snapshot_dir.is_absolute():
        snapshot_dir = ROOT / snapshot_dir
    snapshot = load_real_snapshot(snapshot_dir)
    config = load_config(ROOT / args.config)
    result = run_full_pipeline(
        snapshot,
        config,
        market_date=snapshot.fetched_at.date(),
        cutoff=time(15, 0),
    )
    output = save_pipeline_report(result, ROOT / str(config.raw.get("reporting", {}).get("output_dir", "results")))
    print("STAGE7_REAL_DATA_OK")
    print(json.dumps(result.metadata, ensure_ascii=False, indent=2))
    print(f"REPORT_DIR={output}")
    if result.final_candidates.empty:
        print(f"{result.market_date.isoformat()}：无完全符合标的。")
    else:
        print("FINAL_CANDIDATES")
        print(result.final_candidates[["code", "name", "composite_score", "classification"]].to_string(index=False))
    print("NEAREST_TOP10")
    columns = ["code", "name", "conditions_passed_count", "composite_score", "failure_conditions"]
    print(result.nearest_top10[columns].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
