from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from a_stock.config import AppConfig
from a_stock.providers import AkShareProvider, DataSourceError, EastMoneyProvider, QuoteProvider, TencentProvider
from a_stock.universe import build_universe
from a_stock.validation import validate_included_quotes, validate_raw_quotes


CHINA_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


@dataclass
class Stage1Snapshot:
    fetched_at: datetime
    source: str
    raw_quotes: pd.DataFrame
    basic_quotes: pd.DataFrame
    excluded_stocks: pd.DataFrame
    metadata: dict[str, Any]

    def save(self, output_dir: Path) -> Path:
        day_dir = output_dir / self.fetched_at.strftime("%Y-%m-%d")
        run_dir = day_dir / self.fetched_at.strftime("%H%M%S")
        suffix = 1
        while run_dir.exists():
            run_dir = day_dir / f"{self.fetched_at.strftime('%H%M%S')}_{suffix}"
            suffix += 1
        run_dir.mkdir(parents=True, exist_ok=False)

        self.basic_quotes.to_csv(run_dir / "basic_quotes.csv", index=False, encoding="utf-8-sig")
        self.excluded_stocks.to_csv(run_dir / "excluded_stocks.csv", index=False, encoding="utf-8-sig")
        with (run_dir / "metadata.json").open("w", encoding="utf-8") as handle:
            json.dump(self.metadata, handle, ensure_ascii=False, indent=2)
        return run_dir


class Stage1DataFetcher:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.providers: dict[str, QuoteProvider] = {
            "eastmoney": EastMoneyProvider(config.data),
            "tencent": TencentProvider(config.data),
            "akshare": AkShareProvider(config.data),
        }

    def _source_order(self, source_override: str | None) -> tuple[str, ...]:
        selected = source_override or self.config.data.source
        if selected == "auto":
            return self.config.data.source_order
        if selected not in self.providers:
            raise DataSourceError(f"未知数据源: {selected}")
        return (selected,)

    def fetch(self, source_override: str | None = None) -> Stage1Snapshot:
        attempts: list[dict[str, str]] = []
        for source_name in self._source_order(source_override):
            provider = self.providers[source_name]
            try:
                raw = provider.fetch_basic_quotes()
                validate_raw_quotes(raw)
                included, excluded, reason_counts = build_universe(raw, self.config.universe)
                quality = validate_included_quotes(included)
                fetched_at = datetime.now(CHINA_TZ)
                included.insert(0, "fetched_at", fetched_at.isoformat())
                excluded.insert(0, "fetched_at", fetched_at.isoformat())
                metadata = {
                    "stage": 1,
                    "fetched_at": fetched_at.isoformat(),
                    "source": source_name,
                    "market_field_source": str(raw["market_field_source"].iloc[0]),
                    "raw_row_count": int(len(raw)),
                    "included_row_count": int(len(included)),
                    "excluded_row_count": int(len(excluded)),
                    "exclusion_reason_counts": reason_counts,
                    "source_failures_before_success": attempts,
                    "data_quality": quality,
                    "future_data_used": False,
                    "synthetic_or_filled_market_data": False,
                }
                return Stage1Snapshot(
                    fetched_at=fetched_at,
                    source=source_name,
                    raw_quotes=raw,
                    basic_quotes=included,
                    excluded_stocks=excluded,
                    metadata=metadata,
                )
            except Exception as exc:
                attempts.append({"source": source_name, "error": str(exc)})

        details = "；".join(f"{item['source']}: {item['error']}" for item in attempts)
        raise DataSourceError(f"所有真实数据源均失败，未生成任何模拟数据。{details}")
