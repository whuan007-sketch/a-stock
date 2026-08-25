from pathlib import Path

from a_stock.config import load_config


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

