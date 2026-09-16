from __future__ import annotations
from pathlib import Path
import pandas as pd


class FundamentalsStore:
    """Point-in-time fundamentals.

    `available_date` is the earliest date the row was knowable. Columns are
    normalized to the frozen jqdata names/semantics; market_cap is CNY locally.
    """
    def __init__(self, path: str):
        self.path = Path(path)

    def snapshot(self, factor_date) -> pd.DataFrame:
        d = pd.Timestamp(factor_date).normalize()
        df = pd.read_parquet(self.path)
        df['available_date'] = pd.to_datetime(df['available_date']).dt.normalize()
        df['list_date'] = pd.to_datetime(df['list_date']).dt.normalize()
        df = df[df['available_date'] <= d]
        df = df.sort_values(['code','available_date']).groupby('code', as_index=False).tail(1)
        return df.reset_index(drop=True)
