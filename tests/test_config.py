from pathlib import Path

from a_stock.config import load_config
from a_stock.database import SCHEMA
from a_stock.scoring import CONDITION_COLUMNS


ROOT = Path(__file__).resolve().parents[1]


def test_repository_config_has_required_stage_parameters() -> None:
    config = load_config(ROOT / "config.yaml")
    assert config.data.source_order[0] == "eastmoney"
    assert config.universe.include_boards == {"sh_main", "sz_main", "chinext"}
    assert sum(config.raw["scoring_weights"].values()) == 100
    assert config.raw["hard_filter"]["total_market_cap_cny"] == {
        "min": 5_000_000_000,
        "max": 20_000_000_000,
    }


def test_all_pipeline_parameters_and_safety_tables_are_configured() -> None:
    config = load_config(ROOT / "config.yaml")
    assert len(CONDITION_COLUMNS) == 12
    assert config.raw["historical"]["adjustment"] == "none"
    assert config.raw["intraday"]["cutoff_1445"] == "14:45"
    assert config.raw["intraday"]["market_minutes"] == 240
    assert config.raw["relative_strength"]["minimum_score"] == 55
    assert config.raw["min_resistance_distance"] == 0.03
    assert config.raw["classification"] == {"a_min_score": 80, "b_min_score": 65}
    assert "CHECK (future_data_used = 0)" in SCHEMA
    assert "CHECK (synthetic_or_filled_market_data = 0)" in SCHEMA
    assert "CREATE TABLE IF NOT EXISTS run_daily_bars" in SCHEMA
    assert "CREATE TABLE IF NOT EXISTS run_minute_bars" in SCHEMA
