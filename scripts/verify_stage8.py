from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from a_stock.config import load_config  # noqa: E402
from a_stock.database import save_pipeline_run  # noqa: E402
from a_stock.pipeline import run_full_pipeline  # noqa: E402
from a_stock.snapshot_io import load_real_snapshot  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="用真实行情验收第 8 阶段 SQLite 增量存储")
    parser.add_argument("--snapshot-dir", required=True)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    snapshot = load_real_snapshot(ROOT / args.snapshot_dir)
    config = load_config(ROOT / args.config)
    result = run_full_pipeline(snapshot, config, market_date=snapshot.fetched_at.date(), cutoff=time(15, 0))
    database_path = ROOT / str(config.raw.get("database", {}).get("path", "database/a_stock.sqlite3"))
    first = save_pipeline_run(result, database_path)
    second = save_pipeline_run(result, database_path)
    if first.run_id != second.run_id or first.counts != second.counts:
        raise RuntimeError("同一真实运行重复保存不幂等")
    with sqlite3.connect(database_path) as connection:
        future_daily = connection.execute(
            "SELECT COUNT(*) FROM daily_bars WHERE trade_date > ?", (result.market_date.isoformat(),)
        ).fetchone()[0]
        future_minute = connection.execute(
            "SELECT COUNT(*) FROM minute_bars WHERE trade_date > ?", (result.market_date.isoformat(),)
        ).fetchone()[0]
        flags = connection.execute(
            "SELECT future_data_used, synthetic_or_filled_market_data FROM runs WHERE run_id = ?",
            (first.run_id,),
        ).fetchone()
    if future_daily or future_minute or flags != (0, 0):
        raise RuntimeError("数据库防未来/防模拟数据验收失败")
    print("STAGE8_REAL_DATA_OK")
    print(json.dumps({"run_id": first.run_id, "database": str(first.database_path), "counts": first.counts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
