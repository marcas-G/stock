"""1m_features 月份解析与枚举（R16）：`YYYY-MM` 口径 + 分区扫描（源侧只读）。

从 `run_1m_feature.py`（单文件四职责）拆出。分区规则取 `core.factio.partitions` 单点，
枚举以 **part 文件存在**为准（不是目录存在）。
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))       # tools/
from _env import ensure_platform  # noqa: E402

ensure_platform()

import datetime as dt

from factorlab.core.factio import partitions, paths  # noqa: E402

# ---------------------------------------------------------------- 路径常量
# 路径字面量收敛到 core.factio.paths 单点（R4c；原先硬编码 /data/students/gaolei/...）
DEFAULT_BARS_ROOT = str(paths.bars_1m_root())
DEFAULT_DAILY = str(paths.daily_fact_path())
INJ_LEFT_CAL_DAYS = 40      # ≥20 交易日（CN 最长假期 ~10 天）的日历余量
INJ_COLS = ["trade_date", "code", "close", "amount", "volume"]

_BAR_COLS = ["trade_date", "code", "minute_index", "close", "amount", "volume"]
_MAXABS_TOL = 1e-6          # 引擎×本地对拍容差（同源 f64，应逐位相等）


def _parse_ym(s: str) -> tuple[int, int]:
    y, m = s.split("-", 1)
    return int(y), int(m)


def _iter_months(bars_root: str):
    """(year, month) 升序（数据存在性以 part 文件为准）。"""
    out = []
    yp, mp = partitions.YEAR_PREFIX, partitions.MONTH_PREFIX   # R8c：前缀/偏移单点
    for entry in sorted(os.listdir(bars_root)):
        if not entry.startswith(yp):
            continue
        year = int(entry[len(yp):])
        mdir = os.path.join(bars_root, entry)
        for m in sorted(os.listdir(mdir)):
            if m.startswith(mp) and partitions.bars_month_part(
                    Path(bars_root), year, int(m[len(mp):])).exists():
                out.append((year, int(m[len(mp):])))
    return out


def _month_part(bars_root: str, y: int, m: int) -> str:
    """（保留给 --only/日志显示）单月 part 路径——规则取 core.factio.partitions 单点。"""
    return str(partitions.bars_month_part(Path(bars_root), y, m))

