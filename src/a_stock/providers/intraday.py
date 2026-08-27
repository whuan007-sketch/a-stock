from __future__ import annotations

import json
import time
from datetime import date, time as daytime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from a_stock.config import DataConfig
from a_stock.providers.base import DataSourceError
from a_stock.providers.history import eastmoney_secid, provider_symbol


MINUTE_COLUMNS = (
    "date",
    "time",
    "price",
    "minute_volume_lot",
    "minute_amount_cny",
    "cumulative_volume_lot",
    "cumulative_amount_cny",
    "previous_close",
    "data_source",
)


class IntradayDataProvider:
    """Fetch real one-minute bars without inventing missing minutes."""

    def __init__(self, config: DataConfig, source_order: tuple[str, ...]) -> None:
        self.config = config
        self.source_order = source_order

    def _json(self, url: str, label: str) -> dict[str, Any]:
        headers = {
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://stockapp.finance.qq.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        }
        last_error: Exception | None = None
        for attempt in range(1, self.config.retries + 1):
            try:
                with urlopen(Request(url, headers=headers), timeout=self.config.timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < self.config.retries:
                    time.sleep(self.config.retry_backoff_seconds * (2 ** (attempt - 1)))
        raise DataSourceError(f"{label} 连续 {self.config.retries} 次失败: {last_error}")

    def _tencent(self, code: str, exchange: str, target_date: date) -> pd.DataFrame:
        symbol = provider_symbol(code, exchange)
        url = "https://web.ifzq.gtimg.cn/appstock/app/day/query?" + urlencode({"code": symbol})
        payload = self._json(url, f"腾讯分钟线 {symbol}")
        node = payload.get("data", {}).get(symbol, {})
        days = node.get("data")
        if payload.get("code") != 0 or not isinstance(days, list):
            raise DataSourceError(f"腾讯分钟线 {symbol} 缺少 data.{symbol}.data")
        target = next((item for item in days if str(item.get("date")) == target_date.strftime("%Y%m%d")), None)
        if not isinstance(target, dict) or not isinstance(target.get("data"), list):
            available = [str(item.get("date")) for item in days if isinstance(item, dict)]
            raise DataSourceError(f"腾讯分钟线没有 {target_date}，可用日期={available}")
        previous_close = pd.to_numeric(target.get("prec"), errors="coerce")
        parsed: list[dict[str, Any]] = []
        for item in target["data"]:
            values = str(item).split()
            if len(values) < 4:
                continue
            parsed.append(
                {
                    "date": target_date,
                    "time": values[0],
                    "price": values[1],
                    "cumulative_volume_lot": values[2],
                    "cumulative_amount_cny": values[3],
                    "previous_close": previous_close,
                    "data_source": "tencent",
                }
            )
        frame = pd.DataFrame(parsed)
        if frame.empty:
            raise DataSourceError(f"腾讯分钟线 {symbol} 在 {target_date} 为空")
        frame["cumulative_volume_lot"] = pd.to_numeric(frame["cumulative_volume_lot"], errors="coerce")
        frame["cumulative_amount_cny"] = pd.to_numeric(frame["cumulative_amount_cny"], errors="coerce")
        frame["minute_volume_lot"] = frame["cumulative_volume_lot"].diff()
        frame["minute_amount_cny"] = frame["cumulative_amount_cny"].diff()
        frame.loc[frame.index[0], "minute_volume_lot"] = frame.loc[frame.index[0], "cumulative_volume_lot"]
        frame.loc[frame.index[0], "minute_amount_cny"] = frame.loc[frame.index[0], "cumulative_amount_cny"]
        return frame[list(MINUTE_COLUMNS)]

    def _eastmoney(self, code: str, exchange: str, target_date: date) -> pd.DataFrame:
        params = {
            "secid": eastmoney_secid(code, exchange),
            "klt": "1",
            "fqt": "0",
            "beg": target_date.strftime("%Y%m%d"),
            "end": target_date.strftime("%Y%m%d"),
            "lmt": "1000",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        }
        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urlencode(params)
        payload = self._json(url, f"东财分钟线 {code}")
        data = payload.get("data")
        rows = data.get("klines") if isinstance(data, dict) else None
        previous_close = pd.to_numeric(data.get("preKPrice"), errors="coerce") if isinstance(data, dict) else pd.NA
        if not isinstance(rows, list):
            raise DataSourceError(f"东财分钟线 {code} 缺少 data.klines")
        parsed = []
        for item in rows:
            values = str(item).split(",")
            if len(values) < 8:
                continue
            stamp = pd.to_datetime(values[0], errors="coerce")
            if pd.isna(stamp):
                continue
            parsed.append(
                {
                    "date": stamp.date(),
                    "time": stamp.strftime("%H%M"),
                    "price": values[2],
                    "minute_volume_lot": values[5],
                    "minute_amount_cny": values[6],
                    "previous_close": previous_close,
                    "data_source": "eastmoney",
                }
            )
        frame = pd.DataFrame(parsed)
        if frame.empty:
            raise DataSourceError(f"东财分钟线 {code} 在 {target_date} 为空")
        frame["minute_volume_lot"] = pd.to_numeric(frame["minute_volume_lot"], errors="coerce")
        frame["minute_amount_cny"] = pd.to_numeric(frame["minute_amount_cny"], errors="coerce")
        frame["cumulative_volume_lot"] = frame["minute_volume_lot"].cumsum()
        frame["cumulative_amount_cny"] = frame["minute_amount_cny"].cumsum()
        return frame[list(MINUTE_COLUMNS)]

    def fetch_minutes(
        self,
        code: str,
        exchange: str,
        *,
        target_date: date,
        cutoff: daytime,
    ) -> tuple[pd.DataFrame, list[dict[str, str]]]:
        attempts: list[dict[str, str]] = []
        for source in self.source_order:
            try:
                if source == "tencent":
                    raw = self._tencent(code, exchange, target_date)
                elif source == "eastmoney":
                    raw = self._eastmoney(code, exchange, target_date)
                else:
                    raise DataSourceError(f"未知分钟源: {source}")
                frame = raw.copy()
                frame["price"] = pd.to_numeric(frame["price"], errors="coerce")
                frame["previous_close"] = pd.to_numeric(frame["previous_close"], errors="coerce")
                parsed_time = pd.to_datetime(frame["time"].astype(str).str.zfill(4), format="%H%M", errors="coerce")
                frame["time"] = parsed_time.dt.time
                frame = frame.loc[(frame["date"] == target_date) & frame["time"].notna() & (frame["time"] <= cutoff)].copy()
                frame = frame.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)
                critical = frame[
                    ["price", "minute_volume_lot", "minute_amount_cny", "cumulative_volume_lot", "cumulative_amount_cny"]
                ]
                if len(frame) < 120:
                    raise DataSourceError(f"截至 {cutoff} 仅 {len(frame)} 根有效分钟记录")
                if critical.isna().any().any() or (frame["price"] <= 0).any():
                    raise DataSourceError("分钟行情关键字段缺失或价格非正")
                if (frame[["minute_volume_lot", "minute_amount_cny"]] < 0).any().any():
                    raise DataSourceError("分钟成交量或成交额出现负值")
                if not frame["cumulative_volume_lot"].is_monotonic_increasing:
                    raise DataSourceError("分钟累计成交量不是单调递增")
                return frame, attempts
            except Exception as exc:
                attempts.append({"source": source, "error": str(exc)})
        details = "；".join(f"{item['source']}: {item['error']}" for item in attempts)
        raise DataSourceError(f"{code} 所有真实分钟源均失败: {details}")
