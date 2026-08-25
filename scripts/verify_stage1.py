from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from a_stock.config import load_config  # noqa: E402
from a_stock.data_fetcher import Stage1DataFetcher  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="使用真实行情验收第 1 阶段")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--source", choices=("auto", "eastmoney", "tencent", "akshare"), default="auto")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    config = load_config(config_path)
    snapshot = Stage1DataFetcher(config).fetch(args.source)

    frame = snapshot.basic_quotes
    assert len(snapshot.raw_quotes) >= 4000, "全市场原始行情少于安全阈值"
    assert len(frame) >= 3000, "目标股票池少于安全阈值"
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

    print("STAGE1_REAL_DATA_OK")
    print(json.dumps(snapshot.metadata, ensure_ascii=False, indent=2))
    print(
        frame[
            [
                "code",
                "name",
                "board",
                "current_price",
                "change_pct",
                "volume_ratio",
                "turnover_rate_pct",
                "total_market_cap_cny",
            ]
        ]
        .head(10)
        .to_string(index=False)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
