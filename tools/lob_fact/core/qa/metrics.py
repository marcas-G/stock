"""lob_fact QA 度量基元（W1，纯函数，与引擎无关；W2/W3 锚定与对拍共享）

口径（规格 §3.3）：
- 全整数价（×10000 units），1 tick = 100 units（config.TICK_UNITS）
- M1a 存现率（门, W3 校准语义）: 锚档价在引擎全深度档集存现数/锚档数
- M1b 价梯（诊断, W1 口径）: rank 对齐逐档分类 match/missing/adjacent/deep；rate=命中 anchor 档/总 anchor 档
- M2 量: 命中档 vol_delta；ghost = 引擎在 anchor 价域内的多余价档（采纳后应=0）
- M3 打印合法性: 成交价 ∈ [best_bid−ε, best_ask+ε]，单侧空 → no_quote（桶计数非硬 FAIL）

stub-defeat 约定：全部函数逐值/逐档输出，禁止只回计数。
"""
import numpy as np

TICK = 100  # 1 tick in ×10000 units（与 config.TICK_UNITS 一致，metrics 内自带防循环依赖）


def rint(x: float) -> int:
    """价归整基元: float64 ×10000 刻度 → 最近整数（浮点噪声安全）"""
    return int(np.rint(x))


# ---------- 快照 ladder ----------

def snap_row_to_ladders(row, side: str):
    """snapshots 风格 dict → 有序 ladder [(price_int, vol_int), ...]

    side: 'bid'(买 1..10, 价降序) / 'ask'(卖 1..10, 价升序)。
    档内价或量非正/NaN → 该档剔除（快照深档缺失合法）。浮点噪声经 rint。
    """
    pk, vk = ('bid_p', 'bid_v') if side == 'bid' else ('ask_p', 'ask_v')
    out = []
    for i in range(1, 11):
        p = row.get(f'{pk}{i}')
        v = row.get(f'{vk}{i}')
        if p is None or v is None or (isinstance(p, float) and np.isnan(p)) \
           or (isinstance(v, float) and np.isnan(v)):
            continue
        pi, vi = rint(p), rint(v)
        if pi > 0 and vi > 0:
            out.append((pi, vi))
    return out


# ---------- M1 价梯 ----------

def ladder_match(anchor, engine):
    """rank 对齐逐档分类。

    anchor: [(p,v), ...] 快照档（best 在前）；engine: [(p,v), ...] 引擎档。
    每 anchor rank i:
      engine 无第 i 档                    → 'missing'
      engine 第 i 档价相同                → 'match'（vol_delta = e_v − a_v）
      |价差| == 1 tick                    → 'adjacent'
      价差 > 1 tick                       → 'deep'
    引擎第 len(anchor).. 之后多出的档      → 'extras'（快照不可见区，不算率）
    rate = n_match / len(anchor)
    """
    ranks, n_match = [], 0
    for i, (ap, av) in enumerate(anchor):
        if i >= len(engine):
            ranks.append(dict(rank=i, a_p=ap, a_v=av, e_p=None, e_v=None,
                              cls='missing', vol_delta=None))
            continue
        ep, ev = engine[i]
        if ep == ap:
            ranks.append(dict(rank=i, a_p=ap, a_v=av, e_p=ep, e_v=ev,
                              cls='match', vol_delta=ev - av))
            n_match += 1
        elif abs(ep - ap) == TICK:
            ranks.append(dict(rank=i, a_p=ap, a_v=av, e_p=ep, e_v=ev,
                              cls='adjacent', vol_delta=None))
        else:
            ranks.append(dict(rank=i, a_p=ap, a_v=av, e_p=ep, e_v=ev,
                              cls='deep', vol_delta=None))
    extras = list(engine[len(anchor):])
    return dict(n_anchor=len(anchor), n_match=n_match,
                rate=(n_match / len(anchor)) if anchor else 1.0,
                ranks=ranks, extras=extras)


def match_summary(results: dict) -> dict:
    """双侧(keys B/S) ladder_match 结果合并汇总"""
    n_a = sum(r['n_anchor'] for r in results.values())
    n_m = sum(r['n_match'] for r in results.values())
    return dict(n_anchor=n_a, n_match=n_m, rate=(n_m / n_a) if n_a else 1.0)


def px_presence(anchor, engine) -> int:
    """锚档按价存现数: anchor 档价在引擎档集（任意深度 rank）中的档数（M1a 存现率分子）

    与 rank 对齐率正交: best-edge extra 换位（快照价域外 → ghost 不算的悖论窗类）
    使 n_match 崩但存现不减; 引擎真缺档（消息不可达/丢段）才逐档减 1。
    stub-defeat: 逐档价集比较; 恒返 len(anchor) 的存根在空引擎上必败。
    """
    epx = {p for p, _ in engine}
    return sum(1 for p, _ in anchor if p in epx)


# ---------- M2 ghost ----------

def ghost_levels(engine, anchor):
    """引擎在 anchor 价域 [min_a, max_a] 内、但 anchor 未见的价档（M2: 采纳后应=0）。

    anchor 价域外的引擎深档/价外档是 band 正常构造，不算 ghost。
    返回 [(p, v), ...] 保留价序。
    """
    if not anchor:
        return []
    lo = min(p for p, _ in anchor)
    hi = max(p for p, _ in anchor)
    aps = {p for p, _ in anchor}
    return [(p, v) for p, v in engine if lo <= p <= hi and p not in aps]


# ---------- M3 打印合法性 ----------

def classify_trade_price(px: int, best_bid, best_ask, eps: int = 0):
    """成交打印价 ∈ [best_bid−eps, best_ask+eps] → 'in_spread'，否则桶。

    任一单侧 None（簿空）→ 'no_quote'（桶计数，非硬 FAIL，规格 M3）。
    eps 单位 = ×10000 units（默认 0，调用方给 config.EPS_TICKS*TICK_UNITS）。
    """
    if best_bid is None or best_ask is None:
        return 'no_quote'
    if px < best_bid - eps:
        return 'below_bid'
    if px > best_ask + eps:
        return 'above_ask'
    return 'in_spread'
