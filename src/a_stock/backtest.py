from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import pandas as pd

from a_stock.config import AppConfig
from a_stock.data_fetcher import Stage1Snapshot
from a_stock.database import initialize_database
from a_stock.pipeline import PipelineResult, run_full_pipeline


@dataclass
class BacktestDay:
    market_date: date
    run_id: str
    pipeline: PipelineResult
    outcomes: pd.DataFrame


@dataclass
class BacktestResult:
    start_date: date
    end_date: date
    days: list[BacktestDay]
    skipped_dates: list[dict[str, str]]
    metadata: dict[str, Any]


def _frame_from_json(rows: list[tuple[str]]) -> pd.DataFrame:
    return pd.DataFrame([json.loads(item[0]) for item in rows])


def _load_snapshot(connection: sqlite3.Connection, run: sqlite3.Row) -> Stage1Snapshot:
    basic = _frame_from_json(
        connection.execute("SELECT quote_json FROM basic_quotes WHERE run_id = ? ORDER BY code", (run["run_id"],)).fetchall()
    )
    excluded = _frame_from_json(
        connection.execute(
            "SELECT exclusion_json FROM excluded_stocks WHERE run_id = ? ORDER BY code", (run["run_id"],)
        ).fetchall()
    )
    if basic.empty:
        raise ValueError(f"run {run['run_id']} 没有基础行情")
    basic["code"] = basic["code"].astype("string").str.zfill(6)
    if not excluded.empty:
        excluded["code"] = excluded["code"].astype("string").str.zfill(6)
    raw = pd.concat([basic, excluded], ignore_index=True, sort=False)
    metadata = json.loads(run["metadata_json"])
    return Stage1Snapshot(
        fetched_at=datetime.fromisoformat(run["quote_fetched_at"]),
        source=str(run["quote_source"]),
        raw_quotes=raw,
        basic_quotes=basic,
        excluded_stocks=excluded,
        metadata=metadata,
    )


def _load_daily_frames(
    connection: sqlite3.Connection, run_id: str, codes: set[str], as_of_date: date
) -> dict[str, pd.DataFrame]:
    rows = connection.execute(
        """SELECT code, trade_date, adjustment, data_source, open, close, high, low,
                  volume_lot, amount_cny, turnover_rate_pct
           FROM run_daily_bars WHERE run_id = ? AND trade_date <= ? ORDER BY code, trade_date""",
        (run_id, as_of_date.isoformat()),
    ).fetchall()
    frame = pd.DataFrame([dict(row) for row in rows])
    if frame.empty:
        return {}
    frame = frame.loc[frame["code"].isin(codes)].copy()
    frame = frame.rename(columns={"trade_date": "date"})
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    return {str(code): group.reset_index(drop=True) for code, group in frame.groupby("code")}


def _load_minute_frames(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    instrument_kind: str,
    target_date: date,
    cutoff: time,
) -> dict[str, pd.DataFrame]:
    rows = connection.execute(
        """SELECT instrument_code, trade_date, trade_time, data_source, price,
                  minute_volume_lot, minute_amount_cny, cumulative_volume_lot,
                  cumulative_amount_cny, previous_close
           FROM run_minute_bars
           WHERE run_id = ? AND instrument_kind = ? AND trade_date = ? AND trade_time <= ?
           ORDER BY instrument_code, trade_time""",
        (run_id, instrument_kind, target_date.isoformat(), cutoff.isoformat()),
    ).fetchall()
    frame = pd.DataFrame([dict(row) for row in rows])
    if frame.empty:
        return {}
    frame = frame.rename(columns={"trade_date": "date", "trade_time": "time"})
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    frame["time"] = pd.to_datetime(frame["time"], format="%H:%M:%S").dt.time
    return {
        str(code): group.drop(columns=["instrument_code"]).reset_index(drop=True)
        for code, group in frame.groupby("instrument_code")
    }


def _calculate_outcomes(
    connection: sqlite3.Connection,
    pipeline: PipelineResult,
    run_id: str,
) -> pd.DataFrame:
    columns = [
        "run_id",
        "code",
        "t1_return_pct",
        "t2_return_pct",
        "t3_return_pct",
        "t5_return_pct",
        "t10_return_pct",
        "mfe_pct",
        "mae_pct",
    ]
    records: list[dict[str, Any]] = []
    for row in pipeline.final_candidates.to_dict("records"):
        code = str(row["code"])
        future = connection.execute(
            """SELECT trade_date, close, high, low FROM daily_bars
               WHERE code = ? AND trade_date > ? ORDER BY trade_date LIMIT 10""",
            (code, pipeline.market_date.isoformat()),
        ).fetchall()
        entry = float(row["current_price"])
        record: dict[str, Any] = {column: None for column in columns}
        record.update({"run_id": run_id, "code": code})
        for horizon in (1, 2, 3, 5, 10):
            if len(future) >= horizon:
                record[f"t{horizon}_return_pct"] = (float(future[horizon - 1]["close"]) / entry - 1.0) * 100.0
        if future:
            record["mfe_pct"] = (max(float(item["high"]) for item in future) / entry - 1.0) * 100.0
            record["mae_pct"] = (min(float(item["low"]) for item in future) / entry - 1.0) * 100.0
        records.append(record)
    return pd.DataFrame(records, columns=columns)


def run_backtest(
    database_path: str | Path,
    config: AppConfig,
    *,
    start_date: date,
    end_date: date,
) -> BacktestResult:
    if start_date > end_date:
        raise ValueError("回测开始日期不能晚于结束日期")
    path = initialize_database(database_path)
    days: list[BacktestDay] = []
    skipped: list[dict[str, str]] = []
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """SELECT * FROM runs WHERE market_date BETWEEN ? AND ?
               ORDER BY market_date, quote_fetched_at DESC""",
            (start_date.isoformat(), end_date.isoformat()),
        ).fetchall()
        selected: dict[str, sqlite3.Row] = {}
        for row in rows:
            selected.setdefault(str(row["market_date"]), row)
        current = start_date
        while current <= end_date:
            run = selected.get(current.isoformat())
            if run is None:
                skipped.append({"date": current.isoformat(), "reason": "no_persisted_real_snapshot"})
                current += pd.Timedelta(days=1).to_pytimedelta()
                continue
            try:
                snapshot = _load_snapshot(connection, run)
                cutoff = time.fromisoformat(str(run["cutoff"]))
                codes = set(str(item) for item in snapshot.basic_quotes["code"])
                daily = _load_daily_frames(connection, str(run["run_id"]), codes, current)
                stock_minutes = _load_minute_frames(
                    connection,
                    run_id=str(run["run_id"]),
                    instrument_kind="stock",
                    target_date=current,
                    cutoff=cutoff,
                )
                index_minutes = _load_minute_frames(
                    connection,
                    run_id=str(run["run_id"]),
                    instrument_kind="index",
                    target_date=current,
                    cutoff=cutoff,
                )
                if any((frame["date"] > current).any() for frame in daily.values()):
                    raise ValueError("回测日K越过目标日期")
                if any((frame["time"] > cutoff).any() for frame in stock_minutes.values()):
                    raise ValueError("回测分钟线越过截止时间")
                pipeline = run_full_pipeline(
                    snapshot,
                    config,
                    market_date=current,
                    cutoff=cutoff,
                    preloaded_daily_frames=daily,
                    preloaded_stock_minutes=stock_minutes,
                    preloaded_index_minutes=index_minutes,
                )
                outcomes = _calculate_outcomes(connection, pipeline, str(run["run_id"]))
                days.append(BacktestDay(current, str(run["run_id"]), pipeline, outcomes))
            except Exception as exc:
                skipped.append({"date": current.isoformat(), "reason": str(exc)})
            current += pd.Timedelta(days=1).to_pytimedelta()
    return BacktestResult(
        start_date=start_date,
        end_date=end_date,
        days=days,
        skipped_dates=skipped,
        metadata={
            "stage": 9,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "processed_dates": len(days),
            "skipped_dates": skipped,
            "selection_uses_future_outcomes": False,
            "future_outcomes_reserved_only": True,
            "synthetic_or_filled_market_data": False,
        },
    )


def save_backtest_report(result: BacktestResult, output_dir: str | Path) -> Path:
    directory = Path(output_dir).resolve() / f"{result.start_date}_{result.end_date}"
    directory.mkdir(parents=True, exist_ok=True)
    summaries = []
    outcomes = []
    for day in result.days:
        summaries.append(
            {
                "market_date": day.market_date.isoformat(),
                "run_id": day.run_id,
                **day.pipeline.funnel,
            }
        )
        if not day.outcomes.empty:
            outcomes.extend(day.outcomes.to_dict("records"))
    pd.DataFrame(summaries).to_csv(directory / "backtest_funnel.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(outcomes).to_csv(directory / "backtest_outcomes.csv", index=False, encoding="utf-8-sig")
    with (directory / "backtest_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(result.metadata, handle, ensure_ascii=False, indent=2)
    return directory
