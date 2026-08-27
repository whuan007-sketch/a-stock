from __future__ import annotations

import json
import time
from datetime import date, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from a_stock.config import DataConfig
from a_stock.providers.base import DataSourceError


DAILY_COLUMNS = (
    "date",
    "open",
    "close",
    "high",
    "low",
    "volume_lot",
    "amount_cny",
    "turnover_rate_pct",
    "data_source",
    "adjustment",
)


def provider_symbol(code: str, exchange: str) -> str:
    prefix = "sh" if exchange == "SH" else "sz"
    return f"{prefix}{str(code).zfill(6)}"


def eastmoney_secid(code: str, exchange: str) -> str:
    return f"{1 if exchange == 'SH' else 0}.{str(code).zfill(6)}"


class HistoricalDataProvider:
    """Fetch real, unadjusted daily bars with provider fallback."""

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

    def _tencent(self, code: str, exchange: str, limit: int) -> pd.DataFrame:
        symbol = provider_symbol(code, exchange)
        url = (
            "https://web.ifzq.gtimg.cn/appstock/app/kline/kline?"
            + urlencode({"param": f"{symbol},day,,,{limit}"})
        )
        payload = self._json(url, f"腾讯日K {symbol}")
        node = payload.get("data", {}).get(symbol, {})
        rows = node.get("day")
        if payload.get("code") != 0 or not isinstance(rows, list):
            raise DataSourceError(f"腾讯日K {symbol} 缺少 data.{symbol}.day")
        parsed = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 6:
                continue
            parsed.append(
                {
                    "date": row[0],
                    "open": row[1],
                    "close": row[2],
                    "high": row[3],
                    "low": row[4],
                    "volume_lot": row[5],
                    "amount_cny": pd.NA,
                    "turnover_rate_pct": pd.NA,
                    "data_source": "tencent",
                    "adjustment": "none",
                }
            )
        return pd.DataFrame(parsed, columns=DAILY_COLUMNS)

    def _eastmoney(self, code: str, exchange: str, begin: date, end: date) -> pd.DataFrame:
        params = {
            "secid": eastmoney_secid(code, exchange),
            "klt": "101",
            "fqt": "0",
            "beg": begin.strftime("%Y%m%d"),
            "end": end.strftime("%Y%m%d"),
            "lmt": "1000",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        }
        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urlencode(params)
        payload = self._json(url, f"东财日K {code}")
        rows = payload.get("data", {}).get("klines") if isinstance(payload.get("data"), dict) else None
        if not isinstance(rows, list):
            raise DataSourceError(f"东财日K {code} 缺少 data.klines")
        parsed = []
        for item in rows:
            values = str(item).split(",")
            if len(values) < 11:
                continue
            parsed.append(
                {
                    "date": values[0],
                    "open": values[1],
                    "close": values[2],
                    "high": values[3],
                    "low": values[4],
                    "volume_lot": values[5],
                    "amount_cny": values[6],
                    "turnover_rate_pct": values[10],
                    "data_source": "eastmoney",
                    "adjustment": "none",
                }
            )
        return pd.DataFrame(parsed, columns=DAILY_COLUMNS)

    def _akshare(self, code: str, begin: date, end: date) -> pd.DataFrame:
        try:
            import akshare as ak
        except ImportError as exc:
            raise DataSourceError("AKShare 未安装") from exc
        try:
            source = ak.stock_zh_a_hist(
                symbol=str(code).zfill(6),
                period="daily",
                start_date=begin.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust="",
            )
        except Exception as exc:
            raise DataSourceError(f"AKShare 日K {code} 失败: {exc}") from exc
        mapping = {
            "日期": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume_lot",
            "成交额": "amount_cny",
            "换手率": "turnover_rate_pct",
        }
        missing = set(mapping) - set(source.columns)
        if missing:
            raise DataSourceError(f"AKShare 日K字段缺失: {sorted(missing)}")
        frame = source[list(mapping)].rename(columns=mapping).copy()
        frame["data_source"] = "akshare"
        frame["adjustment"] = "none"
        return frame[list(DAILY_COLUMNS)]

    def fetch_daily(
        self,
        code: str,
        exchange: str,
        *,
        as_of_date: date,
        lookback_calendar_days: int,
        minimum_rows: int,
    ) -> tuple[pd.DataFrame, list[dict[str, str]]]:
        begin = as_of_date - timedelta(days=lookback_calendar_days)
        attempts: list[dict[str, str]] = []
        for source in self.source_order:
            try:
                if source == "tencent":
                    raw = self._tencent(code, exchange, max(minimum_rows + 20, 120))
                elif source == "eastmoney":
                    raw = self._eastmoney(code, exchange, begin, as_of_date)
                elif source == "akshare":
                    raw = self._akshare(code, begin, as_of_date)
                else:
                    raise DataSourceError(f"未知历史日K源: {source}")
                frame = raw.copy()
                frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
                for column in ("open", "close", "high", "low", "volume_lot", "amount_cny", "turnover_rate_pct"):
                    frame[column] = pd.to_numeric(frame[column], errors="coerce")
                frame = frame.loc[frame["date"].notna() & (frame["date"] <= as_of_date)].copy()
                frame = frame.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
                critical = frame[["date", "open", "close", "high", "low", "volume_lot"]]
                if len(frame) < minimum_rows:
                    raise DataSourceError(f"截至 {as_of_date} 仅 {len(frame)} 根日K，需要至少 {minimum_rows} 根")
                if critical.isna().any().any() or (frame[["open", "close", "high", "low"]] <= 0).any().any():
                    raise DataSourceError("日K关键字段缺失或非正")
                if (frame["date"] > as_of_date).any():
                    raise DataSourceError("日K包含 as_of_date 之后的数据")
                return frame, attempts
            except Exception as exc:
                attempts.append({"source": source, "error": str(exc)})
        details = "；".join(f"{item['source']}: {item['error']}" for item in attempts)
        raise DataSourceError(f"{code} 所有真实日K源均失败: {details}")
