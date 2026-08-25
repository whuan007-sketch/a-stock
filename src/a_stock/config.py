from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when the configuration is missing or inconsistent."""


@dataclass(frozen=True)
class DataConfig:
    source: str
    source_order: tuple[str, ...]
    timeout_seconds: float
    retries: int
    retry_backoff_seconds: float
    page_size: int
    pause_between_pages_seconds: float
    output_dir: Path


@dataclass(frozen=True)
class UniverseConfig:
    include_boards: frozenset[str]
    exclude_security_status: frozenset[str]
    require_basic_quote: bool


@dataclass(frozen=True)
class AppConfig:
    data: DataConfig
    universe: UniverseConfig
    raw: dict[str, Any]


def _required(mapping: dict[str, Any], key: str, section: str) -> Any:
    if key not in mapping:
        raise ConfigError(f"config.yaml 缺少 {section}.{key}")
    return mapping[key]


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).resolve()
    if not config_path.exists():
        raise ConfigError(f"配置文件不存在: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ConfigError("config.yaml 顶层必须是映射")

    data_raw = _required(raw, "data", "root")
    universe_raw = _required(raw, "universe", "root")
    if not isinstance(data_raw, dict) or not isinstance(universe_raw, dict):
        raise ConfigError("data 和 universe 必须是映射")

    source_order = tuple(str(item) for item in _required(data_raw, "source_order", "data"))
    if not source_order or any(item not in {"eastmoney", "tencent", "akshare"} for item in source_order):
        raise ConfigError("data.source_order 只能包含 eastmoney/tencent/akshare 且不能为空")

    source = str(data_raw.get("source", "auto"))
    if source not in {"auto", "eastmoney", "tencent", "akshare"}:
        raise ConfigError("data.source 必须是 auto、eastmoney、tencent 或 akshare")

    page_size = int(data_raw.get("page_size", 100))
    if not 1 <= page_size <= 100:
        raise ConfigError("data.page_size 必须在 1～100；东财接口当前单页上限按 100 处理")

    scoring = raw.get("scoring_weights", {})
    if not isinstance(scoring, dict) or sum(float(value) for value in scoring.values()) != 100:
        raise ConfigError("scoring_weights 权重之和必须等于 100")

    output_dir = Path(str(data_raw.get("output_dir", "data/snapshots")))
    if not output_dir.is_absolute():
        output_dir = config_path.parent / output_dir

    data = DataConfig(
        source=source,
        source_order=source_order,
        timeout_seconds=float(data_raw.get("timeout_seconds", 15)),
        retries=int(data_raw.get("retries", 3)),
        retry_backoff_seconds=float(data_raw.get("retry_backoff_seconds", 1.0)),
        page_size=page_size,
        pause_between_pages_seconds=float(data_raw.get("pause_between_pages_seconds", 0.05)),
        output_dir=output_dir.resolve(),
    )
    if data.timeout_seconds <= 0 or data.retries < 1 or data.retry_backoff_seconds < 0:
        raise ConfigError("数据源超时、重试次数或退避参数无效")

    universe = UniverseConfig(
        include_boards=frozenset(str(item) for item in _required(universe_raw, "include_boards", "universe")),
        exclude_security_status=frozenset(
            str(item) for item in _required(universe_raw, "exclude_security_status", "universe")
        ),
        require_basic_quote=bool(universe_raw.get("require_basic_quote", True)),
    )
    allowed_boards = {"sh_main", "sz_main", "chinext"}
    if not universe.include_boards or not universe.include_boards <= allowed_boards:
        raise ConfigError("universe.include_boards 只能从 sh_main/sz_main/chinext 中选择")

    return AppConfig(data=data, universe=universe, raw=raw)
