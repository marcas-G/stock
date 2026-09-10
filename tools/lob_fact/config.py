"""lob_fact 校准常量单点（W1 冻结，引擎/锚定/批算共享，勿散落硬编码）

量纲纪律：快照/委托价 raw 已 ×10000 标度（122800.0 = 12.28 元），
引擎全链路整数价（units）；1 tick = 0.01 元 = 100 units。
"""
import os

# ---- 价格刻度 ----
PRICE_SCALE = 10000          # 价 ×10000 → 整数元×10000
TICK_UNITS = 100             # 1 tick(0.01 元) 的整数单位
EPS_TICKS = 1                # M3 打印价在簿价差 ε（tick 数）

# ---- 主动 gating（W2 生效，先冻结默认） ----
RANK_LIMIT = 50              # band: rank ≤ R
DELTA_PCT = 0.01             # band: |价−对侧 best| ≤ δ×best
SNAP_DEPTH = 10              # 快照 10 档 anchor 深度（可验证深度）
OBI_DEPTH = 5                # W6 面板: 深度/OBI 聚合档数（深度比/失衡 5 档）

# ---- 连续段阶段时间（ms-of-day） ----
AUCTION_START = 33_300_020   # 09:15:00.020（SZ 起收单；SH 同）
AUCTION_MATCH = 33_900_000   # 09:25:00.000（撮合快照点）
OPEN = 34_200_000            # 09:30:00.000（SZ 硬基线）
LUNCH_START = 41_400_000     # 11:30:00.000（午休起点; 采样栅格剔除 (LUNCH_START, LUNCH_END]）
LUNCH_END = 46_800_000       # 13:00:00.000
CLOSE = 54_000_000           # 15:00:00.000

# ---- 锚定 (W3 冻结) ----
M1_END = CLOSE - 180_000     # QA 核心段止于 14:57:00 (SZ 收盘集合竞价窗口不测)
ABSORB_MS = 500              # δ 滞后吸收窗 (W1: 消息时戳晚于簿面生效 0-300ms, 500ms 覆盖 99%+)
MINUTE_MS = 60_000           # 分钟检查点步长

# ---- 源布局 ----
QUARK_ROOT = '/data/students/gaolei/stock/quark_downloaded/'
TICK_FACT_ROOT = '/data/students/gaolei/stock/tick_fact/'
LOB_FACT_ROOT = '/data/students/gaolei/stock/lob_fact/'
CALIB_OUT = '/data/students/gaolei/stock/lob_fact_calib/'

# ---- 校准集（W1 冻结 10 code-day：4 代码 × 双所 × 7 个月） ----
# 含: B 格式撤单行真空月(20260706/20260210/20250822)、2025-09 差 1 行归因月、
#     13 月跨度两端 20250812-20260823、SZ/SH 各 2 代码
CALIB_DAYS = [
    ('000155', '20260803'), ('000021', '20260803'),
    ('000021', '20260706'), ('000155', '20250822'),
    ('000155', '20260210'), ('000155', '20250915'),
    ('600184', '20260803'), ('600184', '20250915'),
    ('600036', '20251215'), ('600036', '20260513'),
]

SZ_POOL = ('000155', '000021', '000858', '300750')
SH_POOL = ('600184', '600036', '601318', '600519')

def code_zip(code: str, day: str) -> str:
    ex = 'SZ' if code.startswith(('0', '3')) else 'SH'
    return f'{QUARK_ROOT}{day}/{code}/{code}.{ex}.zip'


def tick_month(tbl: str, day: str) -> str:
    return f'{TICK_FACT_ROOT}{tbl}/year={day[:4]}/month={day[4:6]}/'
