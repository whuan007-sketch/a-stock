from __future__ import annotations

from typing import Any

import pandas as pd

from a_stock.providers.base import DataSourceError
from a_stock.universe import BASIC_QUOTE_COLUMNS


REQUIRED_RAW_COLUMNS = {
    "code",
    "name",
    "provider_market",
    "current_price",
    "change_pct",
    "volume_lot",
    "volume_share",
    "amount_cny",
    "turnover_rate_pct",
    "volume_ratio",
    "previous_close",
    "total_market_cap_cny",
    "circulating_market_cap_cny",
    "provider_security_state",
    "provider_security_type",
    "data_source",
    "market_field_source",
}


def validate_raw_quotes(frame: pd.DataFrame) -> None:
    missing = REQUIRED_RAW_COLUMNS - set(frame.columns)
    if missing:
        raise DataSourceError(f"基础行情标准字段缺失: {sorted(missing)}")
    if frame.empty:
        raise DataSourceError("基础行情为空")
    if len(frame) < 4000:
        raise DataSourceError(f"全 A 行情疑似不完整，仅 {len(frame)} 行（安全阈值 4000）")
    if frame["code"].duplicated().any():
        duplicates = frame.loc[frame["code"].duplicated(), "code"].head(10).tolist()
        raise DataSourceError(f"行情代码重复: {duplicates}")
    invalid_codes = ~frame["code"].astype(str).str.fullmatch(r"\d{6}")
    if invalid_codes.any():
        raise DataSourceError(f"存在非六位证券代码: {frame.loc[invalid_codes, 'code'].head(10).tolist()}")


def validate_included_quotes(frame: pd.DataFrame) -> dict[str, Any]:
    if len(frame) < 3000:
        raise DataSourceError(f"目标股票池疑似不完整，仅 {len(frame)} 行（安全阈值 3000）")
    if frame["code"].duplicated().any():
        raise DataSourceError("目标股票池存在重复代码")
    if not set(frame["board"].dropna().unique()) <= {"sh_main", "sz_main", "chinext"}:
        raise DataSourceError("目标股票池混入未允许板块")
    if not frame["provider_market_matches_code"].all():
        raise DataSourceError("目标股票池存在代码与数据源市场字段不一致")
    if not frame["security_status"].eq("normal").all():
        raise DataSourceError("目标股票池混入风险警示或退市证券")

    missing_counts = {column: int(frame[column].isna().sum()) for column in frame.columns}
    critical_missing = {column: missing_counts[column] for column in BASIC_QUOTE_COLUMNS if missing_counts[column]}
    if critical_missing:
        raise DataSourceError(f"目标股票池关键行情仍有缺失: {critical_missing}")

    nonpositive_price = int((frame["current_price"] <= 0).sum())
    nonpositive_cap = int((frame["total_market_cap_cny"] <= 0).sum())
    negative_volume = int((frame["volume_lot"] < 0).sum())
    if nonpositive_price or nonpositive_cap or negative_volume:
        raise DataSourceError(
            f"单位/数值检查失败: 非正价格={nonpositive_price}, 非正总市值={nonpositive_cap}, 负成交量={negative_volume}"
        )

    board_counts = {str(key): int(value) for key, value in frame["board"].value_counts().sort_index().items()}
    if any(board_counts.get(board, 0) == 0 for board in ("sh_main", "sz_main", "chinext")):
        raise DataSourceError(f"三类目标板块未全部取得数据: {board_counts}")

    return {
        "included_row_count": int(len(frame)),
        "board_counts": board_counts,
        "missing_by_column": missing_counts,
        "unit_checks": {
            "volume_lot": "手",
            "volume_share": "股（成交量手数×100）",
            "amount_cny": "人民币元",
            "total_market_cap_cny": "人民币元",
            "circulating_market_cap_cny": "人民币元",
            "change_pct": "百分数",
            "turnover_rate_pct": "百分数",
            "amplitude_pct": "百分数",
        },
    }
