from __future__ import annotations

import pandas as pd

from a_stock.providers.base import DataSourceError, QuoteProvider


AKSHARE_COLUMN_MAP = {
    "代码": "code",
    "名称": "name",
    "最新价": "current_price",
    "涨跌幅": "change_pct",
    "涨跌额": "change_amount",
    "成交量": "volume_lot",
    "成交额": "amount_cny",
    "振幅": "amplitude_pct",
    "换手率": "turnover_rate_pct",
    "市盈率-动态": "pe_dynamic",
    "量比": "volume_ratio",
    "最高": "high",
    "最低": "low",
    "今开": "open",
    "昨收": "previous_close",
    "总市值": "total_market_cap_cny",
    "流通市值": "circulating_market_cap_cny",
    "市净率": "pb",
    "涨速": "price_speed_pct",
    "5分钟涨跌": "change_5m_pct",
    "60日涨跌幅": "change_60d_pct",
    "年初至今涨跌幅": "change_ytd_pct",
}


class AkShareProvider(QuoteProvider):
    """Optional fallback adapter for AKShare's real-time A-share snapshot."""

    name = "akshare"

    def fetch_basic_quotes(self) -> pd.DataFrame:
        try:
            import akshare as ak
        except ImportError as exc:
            raise DataSourceError('AKShare 未安装；请执行 python -m pip install "akshare>=1.18"') from exc

        try:
            frame = ak.stock_zh_a_spot_em()
        except Exception as exc:  # Third-party package exposes multiple network exception types.
            raise DataSourceError(f"AKShare 实时行情请求失败: {exc}") from exc
        if frame is None or frame.empty:
            raise DataSourceError("AKShare 返回空行情")

        missing = set(AKSHARE_COLUMN_MAP) - set(frame.columns)
        if missing:
            raise DataSourceError(f"AKShare 字段缺失: {sorted(missing)}")

        result = frame.rename(columns=AKSHARE_COLUMN_MAP)[list(AKSHARE_COLUMN_MAP.values())].copy()
        result["code"] = result["code"].astype("string").str.zfill(6)
        result["name"] = result["name"].astype("string").str.strip()
        result["provider_market"] = result["code"].str[0].map(
            lambda prefix: 1 if prefix in {"6", "9"} else 0 if prefix in {"0", "2", "3", "4", "8"} else pd.NA
        ).astype("Int64")
        for column in result.columns:
            if column not in {"code", "name", "provider_market"}:
                result[column] = pd.to_numeric(result[column], errors="coerce")
        result["volume_share"] = result["volume_lot"] * 100.0
        result["provider_security_state"] = ""
        result["provider_security_type"] = ""
        result["data_source"] = self.name
        result["market_field_source"] = "inferred_from_code"
        return result.drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)
