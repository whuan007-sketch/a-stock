from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from a_stock.backtest import run_backtest, save_backtest_report  # noqa: E402
from a_stock.charts import generate_candidate_charts  # noqa: E402
from a_stock.config import ConfigError, load_config  # noqa: E402
from a_stock.data_fetcher import Stage1DataFetcher  # noqa: E402
from a_stock.database import save_pipeline_run  # noqa: E402
from a_stock.pipeline import run_full_pipeline  # noqa: E402
from a_stock.providers import DataSourceError  # noqa: E402
from a_stock.report import save_pipeline_report  # noqa: E402
from a_stock.snapshot_io import load_real_snapshot  # noqa: E402


CHINA_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A股14:45量化监测与盘后筛选系统")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--source", choices=("auto", "eastmoney", "tencent", "akshare"))
    parser.add_argument("--mode", choices=("1445", "close"), default="close")
    parser.add_argument("--date", help="指定历史交易日 YYYY-MM-DD")
    parser.add_argument("--backtest", nargs=2, metavar=("START", "END"), help="数据库历史回测日期区间")
    parser.add_argument("--snapshot-dir", help="明确指定已保存的真实第一阶段快照")
    parser.add_argument("--no-save", action="store_true")
    return parser.parse_args()


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"日期必须为 YYYY-MM-DD: {value}") from exc


def _find_snapshot(target_date: date) -> Path | None:
    day_dir = ROOT / "data" / "snapshots" / target_date.isoformat()
    if not day_dir.exists():
        return None
    metadata_files = sorted(day_dir.rglob("metadata.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    return metadata_files[0].parent if metadata_files else None


def _ensure_live_time(mode: str, now: datetime) -> time:
    if now.weekday() >= 5:
        raise ValueError("周末不能创建当日实时筛选；请使用已保存交易日快照")
    if mode == "1445":
        if not time(14, 45) <= now.time() <= time(14, 50):
            raise ValueError("14:45模式只允许在14:45～14:50抓取，防止使用更晚行情")
        return time(14, 45)
    if now.time() < time(15, 5):
        raise ValueError("盘后模式需在15:05后运行，当前时点尚未收盘")
    return time(15, 0)


def _print_pipeline(result, output: Path | None, database_result=None) -> None:
    print("FULL_PIPELINE_REAL_DATA_OK")
    print(json.dumps(result.metadata, ensure_ascii=False, indent=2))
    if output:
        print(f"REPORT_DIR={output}")
    if database_result:
        print(f"DATABASE_RUN_ID={database_result.run_id}")
    print("SCREENING_FUNNEL")
    print(json.dumps(result.funnel, ensure_ascii=False, indent=2))
    if result.final_candidates.empty:
        print("今日无符合标的")
    else:
        columns = ["code", "name", "composite_score", "classification", "risk_warnings"]
        print(result.final_candidates[columns].to_string(index=False))
    print("NEAREST_TOP10")
    columns = ["code", "name", "conditions_passed_count", "composite_score", "failure_conditions"]
    print(result.nearest_top10[columns].to_string(index=False))


def _database_has_date(path: Path, target_date: date) -> bool:
    if not path.exists():
        return False
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT 1 FROM runs WHERE market_date = ? LIMIT 1", (target_date.isoformat(),)).fetchone()
    return row is not None


def main() -> int:
    args = parse_args()
    try:
        config_path = Path(args.config)
        if not config_path.is_absolute():
            config_path = ROOT / config_path
        config = load_config(config_path)
        database_path = ROOT / str(config.raw.get("database", {}).get("path", "database/a_stock.sqlite3"))
        reporting_root = ROOT / str(config.raw.get("reporting", {}).get("output_dir", "results"))

        if args.backtest:
            start, end = (_parse_date(item) for item in args.backtest)
            backtest = run_backtest(database_path, config, start_date=start, end_date=end)
            output = save_backtest_report(backtest, reporting_root / "backtest")
            print("BACKTEST_REAL_DATA_OK")
            print(json.dumps({**backtest.metadata, "output": str(output)}, ensure_ascii=False, indent=2))
            return 0 if backtest.days else 3

        now = datetime.now(CHINA_TZ)
        if args.date:
            market_date = _parse_date(args.date)
            snapshot_path = Path(args.snapshot_dir).resolve() if args.snapshot_dir else _find_snapshot(market_date)
            if snapshot_path is None:
                if _database_has_date(database_path, market_date):
                    backtest = run_backtest(database_path, config, start_date=market_date, end_date=market_date)
                    output = save_backtest_report(backtest, reporting_root / "date")
                    if not backtest.days:
                        raise ValueError(f"数据库中 {market_date} 的真实运行无法复现: {backtest.skipped_dates}")
                    _print_pipeline(backtest.days[0].pipeline, output)
                    return 0
                raise ValueError(f"{market_date} 没有已保存的真实基础行情，禁止从当前数据反推")
            snapshot = load_real_snapshot(snapshot_path)
            cutoff = time(14, 45) if args.mode == "1445" else time(15, 0)
            if args.mode == "1445" and snapshot.fetched_at.time() > time(14, 50):
                raise ValueError("指定快照晚于14:50，不能用于历史14:45模式")
        else:
            cutoff = _ensure_live_time(args.mode, now)
            market_date = now.date()
            snapshot = Stage1DataFetcher(config).fetch(args.source)
            if not args.no_save:
                snapshot.save(config.data.output_dir)

        result = run_full_pipeline(snapshot, config, market_date=market_date, cutoff=cutoff)
        output = None
        database_result = None
        if not args.no_save:
            output = save_pipeline_report(result, reporting_root)
            generate_candidate_charts(result, output / "charts")
            database_result = save_pipeline_run(result, database_path)
        _print_pipeline(result, output, database_result)
        return 0
    except (ConfigError, DataSourceError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
