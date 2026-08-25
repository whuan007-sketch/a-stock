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


class TencentProvider(QuoteProvider):
    """Independent fallback using Tencent Securities' public board ranking API."""

    name = "tencent"
    endpoint = "https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList"
    page_size = 200

    def _request_page(self, offset: int) -> dict[str, Any]:
        params = {
            "_appver": "11.17.0",
            "board_code": "aStock",
            "sort_type": "price",
            "direct": "down",
            "offset": str(offset),
            "count": str(self.page_size),
        }
        url = f"{self.endpoint}?{urlencode(params)}"
        headers = {
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://stockapp.finance.qq.com/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        }
        last_error: Exception | None = None
        for attempt in range(1, self.config.retries + 1):
            try:
                request = Request(url, headers=headers)
                with urlopen(request, timeout=self.config.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                data = payload.get("data")
                if payload.get("code") != 0 or not isinstance(data, dict) or not isinstance(data.get("rank_list"), list):
                    raise DataSourceError(f"腾讯 offset={offset} 缺少 data.rank_list")
                return data
            except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError, DataSourceError) as exc:
                last_error = exc
                if attempt < self.config.retries:
                    time.sleep(self.config.retry_backoff_seconds * (2 ** (attempt - 1)))
        raise DataSourceError(f"腾讯 offset={offset} 连续 {self.config.retries} 次失败: {last_error}")

    @staticmethod
    def _number(frame: pd.DataFrame, source: str) -> pd.Series:
        if source not in frame:
            return pd.Series(float("nan"), index=frame.index, dtype="float64")
        return pd.to_numeric(frame[source].replace({"--": pd.NA, "": pd.NA}), errors="coerce")

    def fetch_basic_quotes(self) -> pd.DataFrame:
        first = self._request_page(0)
        total = int(first.get("total") or 0)
        if total <= 0:
            raise DataSourceError("腾讯返回的股票总数为 0")

        rows = list(first["rank_list"])
        for page in range(1, math.ceil(total / self.page_size)):
            if self.config.pause_between_pages_seconds:
                time.sleep(self.config.pause_between_pages_seconds)
            rows.extend(self._request_page(page * self.page_size)["rank_list"])
        if len(rows) < total:
            raise DataSourceError(f"腾讯分页结果不完整: 声明 {total} 行，实际 {len(rows)} 行")

        source = pd.DataFrame(rows).drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)
        if "code" not in source or "name" not in source:
            raise DataSourceError("腾讯响应缺少 code/name")

        result = pd.DataFrame(index=source.index)
        prefixed_code = source["code"].astype("string").str.lower()
        result["code"] = prefixed_code.str[2:].str.zfill(6)
        result["name"] = source["name"].astype("string").str.strip()
        result["provider_market"] = prefixed_code.str[:2].map({"sh": 1, "sz": 0, "bj": 0}).astype("Int64")
        result["current_price"] = self._number(source, "zxj")
        result["change_pct"] = self._number(source, "zdf")
        result["change_amount"] = self._number(source, "zd")
        result["volume_lot"] = self._number(source, "volume")
        result["volume_share"] = result["volume_lot"] * 100.0
        # Tencent turnover and market-cap fields are returned in 万元 and 亿元 respectively.
        result["amount_cny"] = self._number(source, "turnover") * 10_000.0
        result["amplitude_pct"] = self._number(source, "zf")
        result["turnover_rate_pct"] = self._number(source, "hsl")
        result["pe_dynamic"] = self._number(source, "pe_ttm")
        result["volume_ratio"] = self._number(source, "lb")
        result["high"] = float("nan")
        result["low"] = float("nan")
        result["open"] = float("nan")
        result["previous_close"] = result["current_price"] - result["change_amount"]
        result["total_market_cap_cny"] = self._number(source, "zsz") * 100_000_000.0
        result["circulating_market_cap_cny"] = self._number(source, "ltsz") * 100_000_000.0
        result["pb"] = float("nan")
        result["price_speed_pct"] = self._number(source, "speed")
        result["change_5m_pct"] = float("nan")
        result["change_60d_pct"] = self._number(source, "zdf_d60")
        result["change_ytd_pct"] = self._number(source, "zdf_y")
        result["provider_security_state"] = source.get(
            "state", pd.Series("", index=source.index, dtype="string")
        ).astype("string")
        result["provider_security_type"] = source.get(
            "stock_type", pd.Series("", index=source.index, dtype="string")
        ).astype("string")
        result["data_source"] = self.name
        result["market_field_source"] = "provider_code_prefix"
        return result.sort_values("code").reset_index(drop=True)
