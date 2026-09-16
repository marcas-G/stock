"""1m 折日特征定义（研究侧批算与平台引擎对拍共用同一原文）。

口径（与 platform docs/superpowers/specs/2026-09-08-factorlab-1m-funnel-design.md
B2/B6 一致）：im_* 窗口 = 分钟槽位（bars_1m 每交易日 240 槽固定网格），严格限
当日；(code, date) 组内折日为常数的输出形态（date × code 一行）。
amount 元、volume 股、价格 raw 不复权。日级上下文只经注入列：
eod_close（当日 raw 日收盘）/ prev_close / day_amt / day_vol / adv20_*。

特征：

- vwap30_bias：开盘段 30 分钟 VWAP（成交额/量加权）相对当日收盘的偏离——
  vwap30 = im_sum(close*volume, 30) / im_sum(volume, 30)；
  signal = day_last(vwap30 / eod_close - 1)。
  口径注：flat bar（volume=0 且 amount=0）对分子分母贡献 0，天然免疫陈旧价；
  30 槽全零成交（极端死盘）→ 0/0 → NaN（研究侧保留原样，QA 计数非有限值，
  平台不自动过滤——规格"flat 守卫在公式层"）。
- open30_amt_share：当日前 30 根（09:25 集合竞价 + 09:31..09:59 连续段，
  minute_index < 30）成交额占全天成交额比——
  signal = day_sum(if_else(minute_index < 30, amount, 0)) / day_amt。
"""
from __future__ import annotations

# 输出列名（= formula 顶层赋值名；批算与对拍按此引用）
VWAP30_BIAS = "vwap30_bias"
OPEN30_AMT_SHARE = "open30_amt_share"

FEATURE_NAMES = [VWAP30_BIAS, OPEN30_AMT_SHARE]

FORMULA = f"""{VWAP30_BIAS} = day_last(im_sum(close * volume, 30) / im_sum(volume, 30) / eod_close - 1)
{OPEN30_AMT_SHARE} = day_sum(if_else(minute_index < 30, amount, 0)) / day_amt"""
