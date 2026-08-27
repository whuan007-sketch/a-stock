from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from a_stock.pipeline import PipelineResult


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    market_date TEXT NOT NULL,
    cutoff TEXT NOT NULL,
    quote_fetched_at TEXT NOT NULL,
    quote_source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    future_data_used INTEGER NOT NULL CHECK (future_data_used = 0),
    synthetic_or_filled_market_data INTEGER NOT NULL CHECK (synthetic_or_filled_market_data = 0),
    metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS basic_quotes (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    quote_json TEXT NOT NULL,
    PRIMARY KEY (run_id, code)
);
CREATE TABLE IF NOT EXISTS excluded_stocks (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    exclusion_json TEXT NOT NULL,
    PRIMARY KEY (run_id, code)
);
CREATE TABLE IF NOT EXISTS daily_bars (
    code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    adjustment TEXT NOT NULL,
    data_source TEXT NOT NULL,
    open REAL NOT NULL,
    close REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    volume_lot REAL NOT NULL,
    amount_cny REAL,
    turnover_rate_pct REAL,
    PRIMARY KEY (code, trade_date, adjustment, data_source)
);
CREATE TABLE IF NOT EXISTS minute_bars (
    instrument_code TEXT NOT NULL,
    instrument_kind TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    trade_time TEXT NOT NULL,
    data_source TEXT NOT NULL,
    price REAL NOT NULL,
    minute_volume_lot REAL NOT NULL,
    minute_amount_cny REAL NOT NULL,
    cumulative_volume_lot REAL NOT NULL,
    cumulative_amount_cny REAL NOT NULL,
    previous_close REAL,
    PRIMARY KEY (instrument_code, instrument_kind, trade_date, trade_time, data_source)
);
CREATE TABLE IF NOT EXISTS run_daily_bars (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    adjustment TEXT NOT NULL,
    data_source TEXT NOT NULL,
    open REAL NOT NULL,
    close REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    volume_lot REAL NOT NULL,
    amount_cny REAL,
    turnover_rate_pct REAL,
    PRIMARY KEY (run_id, code, trade_date, adjustment, data_source)
);
CREATE TABLE IF NOT EXISTS run_minute_bars (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    instrument_code TEXT NOT NULL,
    instrument_kind TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    trade_time TEXT NOT NULL,
    data_source TEXT NOT NULL,
    price REAL NOT NULL,
    minute_volume_lot REAL NOT NULL,
    minute_amount_cny REAL NOT NULL,
    cumulative_volume_lot REAL NOT NULL,
    cumulative_amount_cny REAL NOT NULL,
    previous_close REAL,
    PRIMARY KEY (run_id, instrument_code, instrument_kind, trade_date, trade_time, data_source)
);
CREATE TABLE IF NOT EXISTS screening_results (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    name TEXT,
    conditions_passed_count INTEGER NOT NULL,
    all_12_conditions_passed INTEGER NOT NULL,
    composite_score REAL,
    classification TEXT NOT NULL,
    failure_conditions TEXT NOT NULL,
    risk_warnings TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    PRIMARY KEY (run_id, code)
);
CREATE TABLE IF NOT EXISTS elimination_log (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    name TEXT,
    stage TEXT NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (run_id, code, stage, reason)
);
CREATE TABLE IF NOT EXISTS funnel (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    remaining_count INTEGER NOT NULL,
    PRIMARY KEY (run_id, stage)
);
CREATE TABLE IF NOT EXISTS backtest_outcomes (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    t1_return_pct REAL,
    t2_return_pct REAL,
    t3_return_pct REAL,
    t5_return_pct REAL,
    t10_return_pct REAL,
    mfe_pct REAL,
    mae_pct REAL,
    PRIMARY KEY (run_id, code)
);
CREATE INDEX IF NOT EXISTS idx_daily_bars_date ON daily_bars(trade_date);
CREATE INDEX IF NOT EXISTS idx_minute_bars_date ON minute_bars(trade_date);
CREATE INDEX IF NOT EXISTS idx_screening_market ON runs(market_date, cutoff);
"""


@dataclass(frozen=True)
class DatabaseSaveResult:
    run_id: str
    database_path: Path
    counts: dict[str, int]


def _scalar(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


def _record_json(record: dict[str, Any]) -> str:
    clean = {str(key): _scalar(value) for key, value in record.items()}
    return json.dumps(clean, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _run_id(result: PipelineResult) -> str:
    identity = "|".join(
        [
            result.market_date.isoformat(),
            result.cutoff.strftime("%H:%M"),
            result.snapshot.fetched_at.isoformat(),
            result.snapshot.source,
        ]
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def initialize_database(path: str | Path) -> Path:
    database_path = Path(path).resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path) as connection:
        connection.executescript(SCHEMA)
    return database_path


def _daily_rows(result: PipelineResult) -> Iterable[tuple[Any, ...]]:
    for code, frame in result.daily.daily_frames.items():
        for row in frame.to_dict("records"):
            yield (
                code,
                _scalar(row["date"]),
                str(row["adjustment"]),
                str(row["data_source"]),
                float(row["open"]),
                float(row["close"]),
                float(row["high"]),
                float(row["low"]),
                float(row["volume_lot"]),
                _scalar(row.get("amount_cny")),
                _scalar(row.get("turnover_rate_pct")),
            )


def _minute_rows(
    frames: dict[str, pd.DataFrame], instrument_kind: str
) -> Iterable[tuple[Any, ...]]:
    for code, frame in frames.items():
        for row in frame.to_dict("records"):
            yield (
                code,
                instrument_kind,
                _scalar(row["date"]),
                _scalar(row["time"]),
                str(row["data_source"]),
                float(row["price"]),
                float(row["minute_volume_lot"]),
                float(row["minute_amount_cny"]),
                float(row["cumulative_volume_lot"]),
                float(row["cumulative_amount_cny"]),
                _scalar(row.get("previous_close")),
            )


def save_pipeline_run(result: PipelineResult, path: str | Path) -> DatabaseSaveResult:
    if result.metadata.get("future_data_used") is not False:
        raise ValueError("拒绝保存未明确声明 future_data_used=false 的结果")
    if result.metadata.get("synthetic_or_filled_market_data") is not False:
        raise ValueError("拒绝保存未明确声明 synthetic_or_filled_market_data=false 的结果")
    database_path = initialize_database(path)
    run_id = _run_id(result)
    now = datetime.now().astimezone().isoformat()
    with sqlite3.connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """INSERT INTO runs (
                run_id, market_date, cutoff, quote_fetched_at, quote_source, created_at,
                future_data_used, synthetic_or_filled_market_data, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?)
            ON CONFLICT(run_id) DO UPDATE SET metadata_json=excluded.metadata_json""",
            (
                run_id,
                result.market_date.isoformat(),
                result.cutoff.strftime("%H:%M"),
                result.snapshot.fetched_at.isoformat(),
                result.snapshot.source,
                now,
                json.dumps(result.metadata, ensure_ascii=False, default=str),
            ),
        )
        connection.executemany(
            "INSERT OR REPLACE INTO basic_quotes(run_id, code, quote_json) VALUES (?, ?, ?)",
            [
                (run_id, str(row["code"]), _record_json(row))
                for row in result.snapshot.basic_quotes.to_dict("records")
            ],
        )
        connection.executemany(
            "INSERT OR REPLACE INTO excluded_stocks(run_id, code, exclusion_json) VALUES (?, ?, ?)",
            [
                (run_id, str(row["code"]), _record_json(row))
                for row in result.snapshot.excluded_stocks.to_dict("records")
            ],
        )
        connection.executemany(
            """INSERT OR REPLACE INTO daily_bars(
                code, trade_date, adjustment, data_source, open, close, high, low,
                volume_lot, amount_cny, turnover_rate_pct
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            list(_daily_rows(result)),
        )
        daily_rows = list(_daily_rows(result))
        connection.executemany(
            """INSERT OR REPLACE INTO run_daily_bars(
                run_id, code, trade_date, adjustment, data_source, open, close, high, low,
                volume_lot, amount_cny, turnover_rate_pct
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(run_id, *row) for row in daily_rows],
        )
        minute_rows = list(_minute_rows(result.intraday.minute_frames, "stock"))
        index_rows: list[tuple[Any, ...]] = []
        for board, frame in result.relative.index_minute_frames.items():
            index_rows.extend(_minute_rows({board: frame}, "index"))
        connection.executemany(
            """INSERT OR REPLACE INTO minute_bars(
                instrument_code, instrument_kind, trade_date, trade_time, data_source, price,
                minute_volume_lot, minute_amount_cny, cumulative_volume_lot,
                cumulative_amount_cny, previous_close
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            minute_rows + index_rows,
        )
        connection.executemany(
            """INSERT OR REPLACE INTO run_minute_bars(
                run_id, instrument_code, instrument_kind, trade_date, trade_time, data_source, price,
                minute_volume_lot, minute_amount_cny, cumulative_volume_lot,
                cumulative_amount_cny, previous_close
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(run_id, *row) for row in minute_rows + index_rows],
        )
        connection.executemany(
            """INSERT OR REPLACE INTO screening_results(
                run_id, code, name, conditions_passed_count, all_12_conditions_passed,
                composite_score, classification, failure_conditions, risk_warnings, metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    run_id,
                    str(row["code"]),
                    _scalar(row.get("name")),
                    int(row["conditions_passed_count"]),
                    int(bool(row["all_12_conditions_passed"])),
                    _scalar(row.get("composite_score")),
                    str(row["classification"]),
                    str(row.get("failure_conditions", "")),
                    str(row.get("risk_warnings", "")),
                    _record_json(row),
                )
                for row in result.scored.to_dict("records")
            ],
        )
        connection.executemany(
            "INSERT OR IGNORE INTO elimination_log(run_id, code, name, stage, reason) VALUES (?, ?, ?, ?, ?)",
            [
                (run_id, str(row["code"]), _scalar(row.get("name")), str(row["stage"]), str(row["reason"]))
                for row in result.elimination_log.to_dict("records")
            ],
        )
        connection.executemany(
            "INSERT OR REPLACE INTO funnel(run_id, stage, remaining_count) VALUES (?, ?, ?)",
            [(run_id, stage, int(count)) for stage, count in result.funnel.items()],
        )
        counts = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "runs",
                "basic_quotes",
                "excluded_stocks",
                "daily_bars",
                "minute_bars",
                "run_daily_bars",
                "run_minute_bars",
                "screening_results",
                "elimination_log",
                "funnel",
            )
        }
    return DatabaseSaveResult(run_id=run_id, database_path=database_path, counts=counts)
