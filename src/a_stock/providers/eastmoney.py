from __future__ import annotations

import json
import math
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from a_stock.providers.base import DataSourceError, QuoteProvider


FIELD_MAP = {
    "f12": "code",
    "f14": "name",
    "f13": "provider_market",
    "f2": "current_price",
    "f3": "change_pct",
    "f4": "change_amount",
    "f5": "volume_lot",
    "f6": "amount_cny",
    "f7": "amplitude_pct",
    "f8": "turnover_rate_pct",
    "f9": "pe_dynamic",
    "f10": "volume_ratio",
    "f15": "high",
    "f16": "low",
    "f17": "open",
    "f18": "previous_close",
    "f20": "total_market_cap_cny",
    "f21": "circulating_market_cap_cny",
    "f23": "pb",
    "f22": "price_speed_pct",
    "f11": "change_5m_pct",
    "f24": "change_60d_pct",
    "f25": "change_ytd_pct",
}

NUMERIC_COLUMNS = [column for column in FIELD_MAP.values() if column not in {"code", "name", "provider_market"}]


class EastMoneyProvider(QuoteProvider):
    """Direct adapter for Eastmoney's public full-market quote endpoint."""

    name = "eastmoney"
    endpoints = (
        "https://82.push2.eastmoney.com/api/qt/clist/get",
        "https://push2.eastmoney.com/api/qt/clist/get",
    )

    def _params(self, page: int) -> dict[str, str]:
        return {
            "pn": str(page),
            "pz": str(self.config.page_size),
            "po": "1",
            "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "invt": "2",
            "fid": "f12",
            "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
            "fields": ",".join(FIELD_MAP),
        }

    def _request_page(self, endpoint: str, page: int) -> dict[str, Any]:
        url = f"{endpoint}?{urlencode(self._params(page))}"
        last_error: Exception | None = None
        headers = {
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://quote.eastmoney.com/center/gridlist.html",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        }
        for attempt in range(1, self.config.retries + 1):
            try:
                request = Request(url, headers=headers)
                with urlopen(request, timeout=self.config.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                data = payload.get("data")
                if not isinstance(data, dict) or not isinstance(data.get("diff"), list):
                    raise DataSourceError(f"东财第 {page} 页缺少 data.diff")
                return data
            except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError, DataSourceError) as exc:
                last_error = exc
                if attempt < self.config.retries:
                    time.sleep(self.config.retry_backoff_seconds * (2 ** (attempt - 1)))
        raise DataSourceError(f"东财第 {page} 页连续 {self.config.retries} 次失败: {last_error}")

    def _fetch_from_endpoint(self, endpoint: str) -> pd.DataFrame:
        first = self._request_page(endpoint, 1)
        total = int(first.get("total") or 0)
        if total <= 0:
            raise DataSourceError("东财返回的股票总数为 0")

        rows = list(first["diff"])
        pages = math.ceil(total / self.config.page_size)
        for page in range(2, pages + 1):
            if self.config.pause_between_pages_seconds:
                time.sleep(self.config.pause_between_pages_seconds)
            rows.extend(self._request_page(endpoint, page)["diff"])

        if len(rows) < total:
            raise DataSourceError(f"东财分页结果不完整: 声明 {total} 行，实际 {len(rows)} 行")

        frame = pd.DataFrame(rows).rename(columns=FIELD_MAP)
        missing_columns = set(FIELD_MAP.values()) - set(frame.columns)
        if missing_columns:
            raise DataSourceError(f"东财字段缺失: {sorted(missing_columns)}")
        frame = frame[list(FIELD_MAP.values())].copy()
        frame["code"] = frame["code"].astype("string").str.zfill(6)
        frame["name"] = frame["name"].astype("string").str.strip()
        frame["provider_market"] = pd.to_numeric(frame["provider_market"], errors="coerce").astype("Int64")
        for column in NUMERIC_COLUMNS:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame["volume_share"] = frame["volume_lot"] * 100.0
        frame["provider_security_state"] = ""
        frame["provider_security_type"] = ""
        frame["data_source"] = self.name
        frame["market_field_source"] = "provider"
        return frame.drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)

    def fetch_basic_quotes(self) -> pd.DataFrame:
        failures: list[str] = []
        for endpoint in self.endpoints:
            try:
                return self._fetch_from_endpoint(endpoint)
            except DataSourceError as exc:
                failures.append(f"{endpoint}: {exc}")
        raise DataSourceError("；".join(failures))
