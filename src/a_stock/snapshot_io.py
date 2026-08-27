from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from a_stock.data_fetcher import Stage1Snapshot
from a_stock.providers.base import DataSourceError


def load_real_snapshot(snapshot_dir: str | Path) -> Stage1Snapshot:
    directory = Path(snapshot_dir).resolve()
    required = ("metadata.json", "basic_quotes.csv", "excluded_stocks.csv")
    missing = [name for name in required if not (directory / name).exists()]
    if missing:
        raise DataSourceError(f"快照目录缺少文件: {missing}")
    with (directory / "metadata.json").open("r", encoding="utf-8") as handle:
        metadata = json.load(handle)
    if metadata.get("synthetic_or_filled_market_data") is not False:
        raise DataSourceError("快照未声明 synthetic_or_filled_market_data=false")
    if metadata.get("future_data_used") is not False:
        raise DataSourceError("快照未声明 future_data_used=false")
    included = pd.read_csv(directory / "basic_quotes.csv", dtype={"code": "string"})
    excluded = pd.read_csv(directory / "excluded_stocks.csv", dtype={"code": "string"})
    raw = pd.concat([included, excluded], ignore_index=True, sort=False)
    expected_raw = int(metadata.get("raw_row_count", len(raw)))
    if len(raw) != expected_raw:
        raise DataSourceError(f"快照行数不一致: metadata={expected_raw}, files={len(raw)}")
    return Stage1Snapshot(
        fetched_at=datetime.fromisoformat(str(metadata["fetched_at"])),
        source=str(metadata["source"]),
        raw_quotes=raw,
        basic_quotes=included,
        excluded_stocks=excluded,
        metadata=metadata,
    )
