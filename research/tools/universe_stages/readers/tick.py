from dataclasses import dataclass
from pathlib import Path
import re

import numpy as np
import pandas as pd

_DAY_DIR_RE = re.compile(r'\d{8}')


@dataclass
class TickColumnMap:
    timestamp: str = 'timestamp'
    price: str = 'price'
    volume: str = 'volume'
    amount: str = 'amount'
    side: str = 'side'


class TickStore:
    """Generic local tick reader. Actual Wind/export layouts are configured by adapter."""
    def __init__(self, root: str, columns: TickColumnMap | None = None):
        self.root = Path(root)
        self.columns = columns or TickColumnMap()

    def read_csv(self, path: str | Path) -> pd.DataFrame:
        df = pd.read_csv(path)
        c = self.columns
        rename = {c.timestamp: 'timestamp', c.price: 'price', c.volume: 'volume', c.amount: 'amount'}
        if c.side in df.columns:
            rename[c.side] = 'side'
        df = df.rename(columns=rename)
        required = {'timestamp', 'price', 'volume', 'amount'}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f'missing tick columns: {sorted(missing)}')
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        return df.sort_values('timestamp').reset_index(drop=True)


class WindTickStore(TickStore):
    """20260817/ 目录的 Wind 逐笔成交导出（GBK CSV）。

    布局：每股票目录（{code}.SZ/）下 逐笔成交.csv，列含
    时间(HHMMSSmmm), BS标志(B/S/空格), 成交价格(元×10000 定点), 成交数量(股)。
    价格<=0 的记录是盘前无效单（09:15-09:25 集合竞价前的系统记录），剔除。
    """
    def __init__(self, root: str):
        super().__init__(root)

    def read_trade_csv(self, code: str, trade_date: str) -> pd.DataFrame:
        # R01-TOOLS-I8：目录日 ≠ 请求日时拒绝读取——本方法用 trade_date 重标时间戳，
        # 跨日调用等于把某日的逐笔伪装成请求日的窗口（静默编造）。
        root_name = Path(self.root).name
        if _DAY_DIR_RE.fullmatch(root_name) and root_name != trade_date:
            raise ValueError(
                f'拒绝跨日重标时间戳: tick 目录 {root_name} != 请求日 {trade_date}'
                f'（会把 {root_name} 的逐笔伪装成 {trade_date} 的窗口）')
        path = Path(self.root) / f'{code}' / '逐笔成交.csv'
        if not path.exists():
            raise FileNotFoundError(f'tick file not found: {path}')
        df = pd.read_csv(path, encoding='gbk')
        df = df[df['成交价格'] > 0].copy()
        if df.empty:
            return pd.DataFrame(columns=['timestamp', 'price', 'volume', 'side'])
        ts = df['时间'].astype(str).str.zfill(9)
        hh, mm, ss, mmm = ts.str[:2].astype(int), ts.str[2:4].astype(int), \
            ts.str[4:6].astype(int), ts.str[6:9].astype(int)
        base = pd.Timestamp(f'{trade_date} 00:00:00')
        df['timestamp'] = pd.Timestamp(base) + pd.to_timedelta(
            hh * 3600 + mm * 60 + ss, unit='s') + pd.to_timedelta(mmm, unit='ms')
        df['price'] = df['成交价格'] / 10000.0
        df['volume'] = pd.to_numeric(df['成交数量'], errors='coerce')
        df['amount'] = df['price'] * df['volume']
        side = df['BS标志'].astype(str).str.upper()
        df['side'] = np.where(side == 'B', 1, np.where(side == 'S', -1, 0))
        return df[['timestamp', 'price', 'volume', 'amount', 'side']] \
            .dropna(subset=['volume']) \
            .sort_values('timestamp').reset_index(drop=True)
