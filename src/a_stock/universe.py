from __future__ import annotations

import re
from collections import Counter

import pandas as pd

from a_stock.config import UniverseConfig


BASIC_QUOTE_COLUMNS = (
    "current_price",
    "previous_close",
    "change_pct",
    "volume_ratio",
    "turnover_rate_pct",
    "total_market_cap_cny",
)


def classify_board(code: str, provider_market: int | None) -> tuple[str, str, bool]:
    """Classify a code and independently verify the provider's market field."""
    code = str(code).zfill(6)
    if re.fullmatch(r"60\d{4}", code):
        return "sh_main", "SH", provider_market == 1
    if re.fullmatch(r"00\d{4}", code):
        return "sz_main", "SZ", provider_market == 0
    if re.fullmatch(r"30\d{4}", code):
        return "chinext", "SZ", provider_market == 0
    if re.fullmatch(r"68\d{4}", code):
        return "star_market", "SH", provider_market == 1
    if re.fullmatch(r"(?:4|8)\d{5}", code) or re.fullmatch(r"92\d{4}", code):
        return "bse", "BJ", provider_market == 0
    if re.fullmatch(r"(?:20|90)\d{4}", code):
        expected = 0 if code.startswith("20") else 1
        return "b_share", "SZ" if expected == 0 else "SH", provider_market == expected
    return "other", "UNKNOWN", False


def classify_security_status(name: str) -> str:
    normalized = re.sub(r"\s+", "", str(name)).upper().replace("＊", "*")
    if "退" in normalized:
        return "delisting"
    if normalized.startswith("*ST") or normalized.startswith("S*ST"):
        return "star_st"
    if normalized.startswith("ST") or normalized.startswith("SST"):
        return "st"
    if "风险" in normalized or "警示" in normalized or normalized.startswith("PT"):
        return "risk_warning"
    return "normal"


def _missing_basic_quote(row: pd.Series) -> list[str]:
    missing: list[str] = []
    for column in BASIC_QUOTE_COLUMNS:
        value = row[column]
        if pd.isna(value):
            missing.append(column)
        elif column in {"current_price", "previous_close", "total_market_cap_cny"} and float(value) <= 0:
            missing.append(column)
    return missing


def build_universe(
    raw_quotes: pd.DataFrame, config: UniverseConfig
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    rows = raw_quotes.copy()
    boards: list[str] = []
    exchanges: list[str] = []
    market_matches: list[bool] = []
    statuses: list[str] = []
    reasons: list[str] = []

    for _, row in rows.iterrows():
        market_value = None if pd.isna(row["provider_market"]) else int(row["provider_market"])
        board, exchange, market_matches_code = classify_board(str(row["code"]), market_value)
        provider_state = "" if pd.isna(row.get("provider_security_state", "")) else str(row.get("provider_security_state", ""))
        status = classify_security_status(f"{provider_state}{row['name']}")
        row_reasons: list[str] = []
        if board not in config.include_boards:
            row_reasons.append(f"board_excluded:{board}")
        if not market_matches_code:
            row_reasons.append("provider_market_code_mismatch")
        if status in config.exclude_security_status:
            row_reasons.append(f"security_status:{status}")
        if config.require_basic_quote:
            missing = _missing_basic_quote(row)
            if missing:
                row_reasons.append(f"missing_basic_quote:{'|'.join(missing)}")

        boards.append(board)
        exchanges.append(exchange)
        market_matches.append(market_matches_code)
        statuses.append(status)
        reasons.append(";".join(row_reasons))

    rows["board"] = boards
    rows["exchange"] = exchanges
    rows["provider_market_matches_code"] = market_matches
    rows["security_status"] = statuses
    rows["exclusion_reason"] = reasons

    included = rows.loc[rows["exclusion_reason"].eq("")].copy()
    excluded = rows.loc[rows["exclusion_reason"].ne("")].copy()
    included = included.drop(columns=["exclusion_reason"]).sort_values("code").reset_index(drop=True)
    excluded = excluded.sort_values("code").reset_index(drop=True)

    reason_counts: Counter[str] = Counter()
    for reason_group in excluded["exclusion_reason"]:
        reason_counts.update(str(reason_group).split(";"))
    return included, excluded, dict(sorted(reason_counts.items()))
