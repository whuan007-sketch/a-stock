from pathlib import Path

import pytest

from a_stock.config import load_config
from a_stock.data_fetcher import Stage1DataFetcher
from a_stock.hard_filter import apply_hard_filter


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.real_data
def test_full_stage1_with_live_market_data() -> None:
    config = load_config(ROOT / "config.yaml")
    snapshot = Stage1DataFetcher(config).fetch("auto")
    frame = snapshot.basic_quotes

    assert len(snapshot.raw_quotes) >= 4000
    assert len(frame) >= 3000
    assert set(frame["board"].unique()) == {"sh_main", "sz_main", "chinext"}
    assert frame["code"].is_unique
    assert frame["provider_market_matches_code"].all()
    assert frame["security_status"].eq("normal").all()
    assert not frame[
        [
            "current_price",
            "previous_close",
            "change_pct",
            "volume_ratio",
            "turnover_rate_pct",
            "total_market_cap_cny",
        ]
    ].isna().any().any()
    assert (frame["volume_share"] == frame["volume_lot"] * 100).all()
    assert snapshot.metadata["synthetic_or_filled_market_data"] is False
    assert snapshot.metadata["future_data_used"] is False
    hard = apply_hard_filter(
        frame,
        config,
        evaluated_at=snapshot.fetched_at,
        source=snapshot.source,
    )
    assert hard.summary["synthetic_or_filled_market_data"] is False
    assert hard.summary["future_data_used"] is False
    assert sum(hard.sequential_elimination_counts.values()) + len(hard.passed) == len(frame)
    assert hard.unit_validation["total_market_cap_cny"]["unit"] == "人民币元"
    assert hard.unit_validation["volume_ratio"]["unit"] == "无量纲倍数"
