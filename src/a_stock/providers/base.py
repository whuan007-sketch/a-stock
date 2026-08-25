from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from a_stock.config import DataConfig


class DataSourceError(RuntimeError):
    """A data source failed or returned an unsafe partial result."""


class QuoteProvider(ABC):
    name: str

    def __init__(self, config: DataConfig) -> None:
        self.config = config

    @abstractmethod
    def fetch_basic_quotes(self) -> pd.DataFrame:
        """Return a real full-market snapshot using the standard column schema."""

