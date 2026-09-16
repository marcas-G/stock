"""分钟事实库（A3）读取：duckdb 侧聚合 1m → 5m（R20 收编自 ashare_alpha3 的 `alpha3.data.minute`）。

两处声明过的改动：
- 月文件清单由 `core.factio.partitions` 派生（原为 config.yaml 里的 `bars_1m_glob`
  字符串 `…/year=*/month=*/*.parquet`——G-CONTRACT 的判定对象；YAML 不被扫，但那是
  "违规温床"，收编时一并改成代码派生）；
- duckdb 读**显式文件清单**（原为 glob），月份范围由此显式可控。

**已知门盲区（如实记录，不造假绿）**：本模块的读藏在 SQL 字符串里
（`read_parquet([...])`），G-READ 只认方法调用 → 看不见（pending #20 登记）。
以 partitions 单点 + 人工复核补位。
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from factorlab.core.factio import partitions


def month_files(root: Path | str, start, end) -> list[str]:
    """[start, end] 覆盖到的月份 → bars_1m 月 part 文件清单（规则取 partitions 单点）。"""
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    out: list[str] = []
    y, m = s.year, s.month
    while (y, m) <= (e.year, e.month):
        part = partitions.bars_month_part(Path(root), y, m)
        if part.exists():
            out.append(str(part))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    if not out:
        raise FileNotFoundError(f'bars_1m 无月文件：{root} [{s.date()} .. {e.date()}]')
    return out


class MinuteStore:
    def __init__(self, root: Path | str, threads: int = 8):
        self.root = Path(root)
        self.con = duckdb.connect(database=':memory:')
        self.con.execute(f"PRAGMA threads={int(threads)}")

    @staticmethod
    def _codes_sql(codes):
        vals = ','.join("'" + str(x).replace("'", "''") + "'" for x in codes)
        return f"({vals})"

    def _files_sql(self, start, end) -> str:
        return '[' + ','.join(f"'{f}'" for f in month_files(self.root, start, end)) + ']'

    def query_5m(self, codes, start_date, end_date) -> pd.DataFrame:
        sql = f"""
        WITH x AS (
          SELECT *,
                 ((minute_index - 1) // 5)::INTEGER AS bucket_5m,
                 (amount > 0 OR volume > 0) AS trade_active
          FROM read_parquet({self._files_sql(start_date, end_date)}, hive_partitioning=true)
          WHERE code IN {self._codes_sql(codes)}
            AND trade_date BETWEEN DATE '{pd.Timestamp(start_date).date()}' AND DATE '{pd.Timestamp(end_date).date()}'
            AND minute_index BETWEEN 1 AND 240
        ), y AS (
          SELECT trade_date, code, bucket_5m,
                 min(datetime) AS bucket_start,
                 max(datetime) AS datetime,
                 arg_min(open, minute_index) FILTER (WHERE trade_active) AS open,
                 max(high) FILTER (WHERE trade_active) AS high,
                 min(low) FILTER (WHERE trade_active) AS low,
                 arg_max(close, minute_index) FILTER (WHERE trade_active) AS close,
                 sum(amount) AS amount,
                 sum(volume) AS volume,
                 count(*) FILTER (WHERE trade_active) AS trade_minute_count,
                 bool_or(trade_active) AS has_trade
          FROM x
          GROUP BY trade_date, code, bucket_5m
        )
        SELECT *, CASE WHEN bucket_5m = 47 THEN 1 ELSE 0 END::UTINYINT AS session_class
        FROM y
        ORDER BY code, trade_date, bucket_5m
        """
        return self.con.execute(sql).fetchdf()
