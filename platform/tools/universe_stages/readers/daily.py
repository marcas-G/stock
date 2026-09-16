"""日线事实层读取：复权价派生（复权方法不固定在数据层）。

daily_fact.parquet 只存原始价 + adj_factor（当日后复权价/原始收盘，等比累计因子）。
复权价由本层按调用方选择派生：
  - raw：原始价（列名 open/high/low/close）
  - hf（后复权）：raw × factor
  - pre（前复权）：raw × factor / factor(anchor_date)，锚点默认每股最后一个因子
"""
from __future__ import annotations
from pathlib import Path

import pandas as pd

import universe_paths  # 工具内路径单点（R20）


PRICE_COLS = ('open', 'high', 'low', 'close')


def default_fact_path() -> Path:
    """A5 权威位（R20：原从 config.yaml 的 paths.daily_fact 读——那是第二份路径真相）。"""
    return universe_paths.daily_fact()


def _anchor_factor(df: pd.DataFrame, anchor_date=None) -> pd.Series:
    """每股在 anchor_date（含）前的最后一个 adj_factor；None → 全库最新"""
    fa = df[df['adj_factor'].notna()]
    if anchor_date is not None:
        fa = fa[fa['trade_date'] <= pd.Timestamp(anchor_date).normalize()]
    return fa.groupby('code')['adj_factor'].last()


def load_daily(path: str | Path | None = None, adjust: str = 'pre',
               anchor_date=None, columns=None) -> pd.DataFrame:
    """读 daily_fact.parquet 并按 adjust 派生复权价。

    pre 锚点默认 = 每股最新因子（最新快照的前复权）；传 anchor_date 可复现
    历史任意时点的前复权快照。columns 为 None 返回全部列。
    """
    df = pd.read_parquet(path or default_fact_path())
    if columns is not None:
        df = df[list(columns)]
    if adjust == 'raw':
        return df
    k = df['adj_factor']
    if adjust == 'hf':
        out = df.copy()
        for c in PRICE_COLS:
            out[c] = df[c] * k
        return out.rename(columns={c: 'pre_' + c for c in PRICE_COLS})
    if adjust == 'pre':
        anchor = _anchor_factor(df, anchor_date)
        out = df.copy()
        for c in PRICE_COLS:
            out[c] = df[c] * k / out['code'].map(anchor)
        return out.rename(columns={c: 'pre_' + c for c in PRICE_COLS})
    raise ValueError(f'unknown adjust: {adjust} (raw|hf|pre)')


class DailyStore:
    """Local daily store.

    Important parity rule: V4's jqdata `count=N, skip_paused=False` chooses the
    last N *market trading dates* first, then the factor code drops money==0.
    Therefore windows are selected by the market calendar, never by `tail(N)`
    per stock. The input file may omit suspended stock rows; that is fine as
    long as the global market calendar is complete.

    adjust 默认 'pre'（与 jqdata fq='pre' 对齐的因子语义）；换复权方法无需
    改数据，传 adjust='raw'/'hf' 或 anchor_date 即可。
    """
    def __init__(self, path: str | Path | None = None, adjust: str = 'pre',
                 anchor_date=None):
        self.path = Path(path) if path else default_fact_path()
        self.adjust = adjust
        self.anchor_date = anchor_date
        self._df = None

    def _load(self) -> pd.DataFrame:
        if self._df is None:
            df = load_daily(self.path, adjust=self.adjust, anchor_date=self.anchor_date)
            df['trade_date'] = pd.to_datetime(df['trade_date']).dt.normalize()
            self._df = df.sort_values(['trade_date', 'code']).reset_index(drop=True)
        return self._df

    def calendar(self, end=None) -> list[pd.Timestamp]:
        df = self._load()
        dates = pd.Series(df['trade_date'].unique()).sort_values()
        if end is not None:
            dates = dates[dates <= pd.Timestamp(end).normalize()]
        return [pd.Timestamp(x).normalize() for x in dates.tolist()]

    def previous_trade_date(self, scan_date) -> pd.Timestamp:
        sd = pd.Timestamp(scan_date).normalize()
        dates = [d for d in self.calendar(end=sd) if d < sd]
        if not dates:
            raise ValueError(f'no previous trade date before {sd.date()}')
        return dates[-1]

    def read(self, codes=None, start=None, end=None) -> pd.DataFrame:
        df = self._load()
        x = df
        if codes is not None:
            x = x[x['code'].isin(list(codes))]
        if start is not None:
            x = x[x['trade_date'] >= pd.Timestamp(start).normalize()]
        if end is not None:
            x = x[x['trade_date'] <= pd.Timestamp(end).normalize()]
        return x.copy().sort_values(['code','trade_date']).reset_index(drop=True)

    def window_market_dates(self, codes, end_date, count: int) -> pd.DataFrame:
        dates = self.calendar(end=end_date)
        if not dates:
            return pd.DataFrame()
        dates = dates[-int(count):]
        return self.read(codes=codes, start=dates[0], end=dates[-1])


class IndexDailyStore:
    def __init__(self, path: str):
        self.path = Path(path)

    def close_window(self, end_date, count=520) -> pd.Series:
        df = pd.read_parquet(self.path)
        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.normalize()
        df = df[df['trade_date'] <= pd.Timestamp(end_date).normalize()].sort_values('trade_date').tail(int(count))
        return pd.to_numeric(df['pre_close'], errors='coerce').dropna().reset_index(drop=True)
