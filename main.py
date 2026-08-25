from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from a_stock.config import ConfigError, load_config  # noqa: E402
from a_stock.data_fetcher import Stage1DataFetcher  # noqa: E402
from a_stock.providers import DataSourceError  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A 股监测系统：第 1 阶段基础行情抓取")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument(
        "--source", choices=("auto", "eastmoney", "tencent", "akshare"), help="覆盖配置中的数据源"
    )
    parser.add_argument("--no-save", action="store_true", help="仅验证，不保存快照")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config = load_config(ROOT / args.config if not Path(args.config).is_absolute() else args.config)
        snapshot = Stage1DataFetcher(config).fetch(args.source)
        output = None if args.no_save else snapshot.save(config.data.output_dir)
    except (ConfigError, DataSourceError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 2

    print("第 1 阶段真实行情验证通过")
    print(json.dumps(snapshot.metadata, ensure_ascii=False, indent=2))
    if output:
        print(f"结果目录：{output}")
    print("样例（真实行情前 10 行）：")
    columns = [
        "code",
        "name",
        "board",
        "current_price",
        "change_pct",
        "volume_ratio",
        "turnover_rate_pct",
        "total_market_cap_cny",
    ]
    print(snapshot.basic_quotes[columns].head(10).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
