from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from a_stock.backtest import run_backtest, save_backtest_report  # noqa: E402
from a_stock.config import load_config  # noqa: E402


def main() -> int:
    config = load_config(ROOT / "config.yaml")
    database = ROOT / str(config.raw.get("database", {}).get("path", "database/a_stock.sqlite3"))
    result = run_backtest(
        database,
        config,
        start_date=date(2026, 8, 25),
        end_date=date(2026, 8, 25),
    )
    if len(result.days) != 1:
        raise RuntimeError(f"真实持久化日期回测失败: {result.skipped_dates}")
    day = result.days[0]
    if day.pipeline.metadata["future_data_used"] is not False:
        raise RuntimeError("回测选择阶段使用了未来数据")
    output = save_backtest_report(result, ROOT / "results" / "backtest")
    print("STAGE9_REAL_DATA_OK")
    print(json.dumps({**result.metadata, "funnel": day.pipeline.funnel, "output": str(output)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
