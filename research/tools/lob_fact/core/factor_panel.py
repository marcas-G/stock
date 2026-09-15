"""W6 盘口因子面板 + M7 同位门（规格 §3.3 M7）

两路独立实现（"同位"= 同一时刻同一簿态口径）：
  A = BandFold —— **生产表消费路径**: 折叠 lob_events（绝对量行）+ lob_sweep_meta
      （档删除）+ lob_checkpoints（n_queue 分钟口径）→ 采样时刻 band 视图 → 因子
  B = ReplayB —— **独立最小重放**: 按规格重写簿语义（不 import engine.py），
      输入 tick_fact 归一化事件（表→事件映射复用 W4a 已验证的机械转换
      core/events.orders_to_events/trades_to_events/cancels_to_events），
      重放得全簿 + 物化发射视图 shadow → 因子

M7 门（`m7_gate`）四项：
  1. 发射行流逐行全等（time_ms, kind, side, price_x10000, prev_vol, new_vol,
     qty, id, otype）
  2. 耗尽行流逐行全等（time_ms, side, price_x10000, vol_before, tail_order,
     tail_resid）
  3. 采样时刻 band 视图整数簿态全等（A 折叠 vs B shadow）+ A 侧无幻影档
  4. 因子逐列 max|Δ| ≤ 1e-6（nq 列仅检查点对齐样本，见下）

口径边界（冻结；实测数字见 notes/w6_factor_memo.md）：
- lob_fact 是 **band 限物化**（rank ≤ R ∪ δ 对侧 best）：出带档的事件行被抑制，
  其折叠量停留于最近一次在带值 → 与"真实全簿"存在 band 边缘漂移；该漂移在 A/B
  两路**对称**（B 的 shadow 用同一发射律），故 M7 第 3 项比较 shadow 视图；
  漂移本身由 `drift` 诊断量化（A 折叠 vs B 全簿在带视图），不进闸门。
- **窗口流水 = 行流口径**: 计数来自实际落表事件行（出带行/观测行不计; 档清空走
  sweep 行 → 该次全消不计 trade/cancel）——A 只能看到行，B 按同一发射律对齐，
  否则 M7 第 4 项不可证。
- n_queue 在 A 侧只能来自分钟检查点（行流无逐单剩余量：物化行按档聚合、id 级
  剩余量不可回溯）→ 1s 面板 nq 列是**检查点口径**；M7 对 nq 列仅在 `aligned`
  （检查点对齐）样本上比较。
- 窗口定义 = (前一采样, 本采样]；ms 恰为采样点的行计入**以该采样点结束**的窗
  （态与流水同刻提交，见 BandFold.run 步序）。
- 同 (ms, side, price) 内 sweep 与行的先后由链式规则复原（W6 实测定标）：
  见 BandFold._apply_key；无法判定者计入 n_same_ms_ambiguous（报告字段，不静默）。

CLI（真实小样产出）::

    python factor_panel.py --dates 20260803,20260807 \\
        --codes 000155.SZ,600036.SH --out <LOB_FACT_ROOT>
"""
import argparse
import glob
import json
import os
import time
from bisect import bisect_left, insort
from collections import defaultdict
from datetime import date as _date
from pathlib import Path

import polars as pl

from lib import tickdata as T  # noqa: E402  （R4a：数据读取薄封装）
from factorlab.core.factio import partitions  # noqa: E402  （R8c：分区规则单点）

from core import config as C
from core import events as _events

GRID_1S = 1_000
GRID_1M = 60_000
_KIND_PRIO = {'add': 0, 'fill': 1, 'cancel': 2}
_FOLD_KINDS = ('add', 'cancel', 'trade', 'level_materialization')
_FLOW_KINDS = ('add', 'cancel', 'trade')       # 落表事件行 kind（物化不计流水）
_NDEPTH = C.OBI_DEPTH

# ---------- 因子列契约（A/B 两路共用; 顺序 = 面板 parquet 列序） ----------

FACTOR_COLS = [
    'bid_p1', 'ask_p1', 'spread',                   # 价差 (int; 空侧 → 0 / -1)
    'bid_v1', 'ask_v1', 'bid_v5', 'ask_v5',         # 深度 (int)
    'obi1', 'obi5', 'depth_ratio5',                 # 失衡/深度比 (float; 分母 0 → 0.0/-1.0)
    'bid_nq1', 'ask_nq1', 'bid_nq5', 'ask_nq5',     # 挂单笔数 (int; 未知 → -1)
    'n_add', 'n_cancel', 'n_trade', 'n_sweep',      # 窗口流水计数
    'add_vol', 'cancel_vol', 'trade_vol',           # 窗口流水量
    'sweep_vol_sum', 'sweep_vol_max',               # 耗尽冲击 (int)
    'cancel_rate', 'depletion_impact',              # 归一化 (float)
]
NQ_COLS = ('bid_nq1', 'ask_nq1', 'bid_nq5', 'ask_nq5')
FLOAT_COLS = ('obi1', 'obi5', 'depth_ratio5', 'cancel_rate', 'depletion_impact')
PANEL_COLS = ['code', 'trade_date', 'grid', 'time_ms'] + FACTOR_COLS

_FLOW_COLS = ('n_add', 'n_cancel', 'n_trade', 'n_sweep', 'add_vol', 'cancel_vol',
              'trade_vol', 'sweep_vol_sum', 'sweep_vol_max')


def sample_times(grid_ms, start_ms=C.OPEN, end_ms=C.CLOSE, skip_lunch=True):
    """采样栅格: start+grid, start+2grid, ... < end; 午休 (LUNCH_START, LUNCH_END] 剔除

    午休边界无消息（簿态不变）→ 剔除只为不产冗余行；13:01 起恢复。"""
    assert grid_ms >= 1
    out = []
    t = start_ms + grid_ms
    while t < end_ms:
        if not (skip_lunch and C.LUNCH_START < t <= C.LUNCH_END):
            out.append(t)
        t += grid_ms
    return out


# ---------- 因子（A/B 共用单点实现） ----------

def _ladder(state, side):
    """(px, vol) best-first (B 降序 / S 升序; 缺侧/空态 → 空梯)"""
    return sorted(state.get(side, {}).items(),
                  key=lambda kv: -kv[0] if side == 'B' else kv[0])


def factors_row(state, nq, flow, prev_state):
    """单样本因子（哨兵冻结）:

    spread = ask_p1 − bid_p1（任一侧空 → −1）; obi_k = (Σb_k − Σa_k)/Σ(b_k+a_k)
    （分母 0 → 0.0）; depth_ratio5 = Σb_5/Σa_5（Σa_5 == 0 → −1.0 未定义哨兵）;
    nq 列任一提及档缺 → −1（未知）; cancel_rate = cancel_vol/add_vol（0 → 0.0）;
    depletion_impact = sweep_vol_sum / 前一样本总 5 档深度（0 → 0.0）。
    """
    lb, la = _ladder(state, 'B'), _ladder(state, 'S')
    b1p, b1v = lb[0] if lb else (0, 0)
    a1p, a1v = la[0] if la else (0, 0)
    b5v = sum(v for _, v in lb[:_NDEPTH])
    a5v = sum(v for _, v in la[:_NDEPTH])
    p5 = sum(v for _, v in _ladder(prev_state, 'B')[:_NDEPTH]) + \
        sum(v for _, v in _ladder(prev_state, 'S')[:_NDEPTH])

    def _nq_sum(lad, side):
        tops = lad[:_NDEPTH]
        if not tops or any(px not in nq.get(side, {}) for px, _ in tops):
            return -1
        return sum(nq[side][px] for px, _ in tops)

    add_v, can_v = flow.get('add_vol', 0), flow.get('cancel_vol', 0)
    sw_sum = flow.get('sweep_vol_sum', 0)
    return {
        'bid_p1': b1p, 'ask_p1': a1p,
        'spread': (a1p - b1p) if (lb and la) else -1,
        'bid_v1': b1v, 'ask_v1': a1v, 'bid_v5': b5v, 'ask_v5': a5v,
        'obi1': (b1v - a1v) / (b1v + a1v) if (b1v + a1v) else 0.0,
        'obi5': (b5v - a5v) / (b5v + a5v) if (b5v + a5v) else 0.0,
        'depth_ratio5': (b5v / a5v) if a5v else -1.0,
        'bid_nq1': nq.get('B', {}).get(b1p, -1) if lb else -1,
        'ask_nq1': nq.get('S', {}).get(a1p, -1) if la else -1,
        'bid_nq5': _nq_sum(lb, 'B'), 'ask_nq5': _nq_sum(la, 'S'),
        **{k: flow.get(k, 0) for k in _FLOW_COLS},
        'cancel_rate': (can_v / add_v) if add_v else 0.0,
        'depletion_impact': (sw_sum / p5) if p5 else 0.0,
    }


def panel_rows(states, nqs, flows, samples, code, day, grid):
    """采样序列 → 面板行（A/B 共用; 列序 = PANEL_COLS 的因子段）"""
    assert len(states) == len(nqs) == len(flows) == len(samples)
    out = []
    prev = {'B': {}, 'S': {}}
    for i, t in enumerate(samples):
        r = {'code': code, 'trade_date': day, 'grid': grid, 'time_ms': int(t)}
        r.update(factors_row(states[i], nqs[i], flows[i], prev))
        out.append(r)
        prev = states[i]
    return out


# ---------- 窗口流水累加（A/B 共用单点） ----------

def _empty_flow():
    return dict(n_add=0, n_cancel=0, n_trade=0, n_sweep=0, add_vol=0,
                cancel_vol=0, trade_vol=0, sweep_vol_sum=0, sweep_vol_max=0)


def _flow_add(flow, kind, qty=0, sweep_vol=0):
    """行/扫单 → 窗口流水 (行流口径): kind ∈ add/cancel/trade 或 'sweep'"""
    if kind == 'sweep':
        flow['n_sweep'] += 1
        flow['sweep_vol_sum'] += sweep_vol
        if sweep_vol > flow['sweep_vol_max']:
            flow['sweep_vol_max'] = sweep_vol
        return
    flow['n_' + kind] += 1
    flow[kind + '_vol'] += qty


def _copy_state(st):
    return {s: dict(d) for s, d in st.items()}


# ---------- 键名归一（A 行来自表列名, B 行来自重放内部 schema） ----------

def _ts(r):
    return r['time_ms'] if 'time_ms' in r else r['ms']


def _px(r):
    return r['price_x10000'] if 'price_x10000' in r else r['price']


def _row_tuple(r):
    """行 → 全等比对元组（语义字段一一对应; seq/phase/code 不在同位口径内）"""
    return (int(_ts(r)), r['kind'], r['side'], int(_px(r)),
            int(r['prev_vol']), int(r['new_vol']), int(r['qty']),
            r.get('id'), r.get('otype'))


def _sweep_tuple(x):
    return (int(_ts(x)), x['side'], int(_px(x)),
            int(x['vol_before']), x.get('tail_order'), int(x.get('tail_resid') or 0))


def _norm_rows(seq):
    """发射行流归一: 元组（A 侧表行, 已归）直通; dict（B 侧重放行）按表口径过滤

    表口径 = run_lob_batch.day_tables 谓词: phase == 'continuous' 且 非观测行
    (prev==new==0) —— 竞价段观测行/非连续行不入 lob_events, 故 M7 行流比对同口径。"""
    out = []
    for x in seq:
        if isinstance(x, tuple):
            out.append(x)
        elif x.get('phase', 'continuous') == 'continuous' \
                and not (x['prev_vol'] == 0 and x['new_vol'] == 0):
            out.append(_row_tuple(x))
    return out


def _norm_sweeps(seq):
    return [x if isinstance(x, tuple) else _sweep_tuple(x) for x in seq]


# ---------- Path A: lob_fact 行流折叠 ----------

class BandFold:
    """lob_events 行 + lob_sweep_meta 耗尽 + lob_checkpoints 分钟态 → 采样 band 视图。

    run(rows, sweeps, ckpts, samples): rows/sweeps/ckpts 为表列名 dict
    （time_ms/kind/side/price_x10000/prev_vol/new_vol/qty/id/otype；
      vol_before/tail_order/tail_resid；vol/n_queue）。属性:
      states  list[{side: {px: vol}}]      采样时刻带限簿态（行流物化视图）
      nqs     list[{side: {px: n_queue}}]  检查点口径（每个检查点重建该侧字典:
              本刻未见 → 缺键=未知, 旧值不跨检查点残留; 非检查点样本沿用最近一次）
      flows   list[dict]                   窗口 (前样本, 本样本] 行流/扫单计数
      flat_rows/flat_sweeps                归一化行流（M7 逐行比对用）
      n_drift_chain / n_same_ms_ambiguous  折叠诊断计数（见备忘）

    步序（每 ms）: ① 采样点 < ms 的先冻结出图; ② 应用行/扫单; ③ ms 落入当前窗
    （ms > 窗起点）时计流水; ④ 采样点 == ms 出图（含 ②③ 效果，窗口右端闭合于此）。
    """
    def __init__(self):
        self.states, self.nqs, self.flows = [], [], []
        self.flat_rows, self.flat_sweeps = [], []
        self.n_drift_chain = 0
        self.n_same_ms_ambiguous = 0

    def run(self, rows, sweeps, ckpts, samples):
        levels, nq = {'B': {}, 'S': {}}, {'B': {}, 'S': {}}
        by_ms_r, by_ms_s, by_ms_ck = defaultdict(list), defaultdict(list), \
            defaultdict(list)
        for r in rows:
            if r['kind'] not in _FOLD_KINDS:
                raise ValueError('unknown event kind: %r' % (r['kind'],))
            self.flat_rows.append(_row_tuple(r))
            if not (r['prev_vol'] == 0 and r['new_vol'] == 0):
                by_ms_r[int(_ts(r))].append(r)
        for x in sweeps:
            self.flat_sweeps.append(_sweep_tuple(x))
            by_ms_s[int(_ts(x))].append(x)
        for c in ckpts:
            by_ms_ck[int(_ts(c))].append(c)

        cur = _empty_flow()
        si, n = 0, len(samples)
        for ms in sorted(set(by_ms_r) | set(by_ms_s) | set(by_ms_ck)):
            while si < n and samples[si] < ms:
                self._emit_sample(levels, nq, cur)
                cur = _empty_flow()
                si += 1
            boundary = samples[si - 1] if si > 0 else C.OPEN
            for side, px in sorted({(r['side'], int(_px(r)))
                                    for r in by_ms_r.get(ms, ())}
                                   | {(x['side'], int(_px(x)))
                                      for x in by_ms_s.get(ms, ())}):
                self._apply_key(levels, side, px, by_ms_r.get(ms, ()),
                                by_ms_s.get(ms, ()))
            cks = by_ms_ck.get(ms, ())
            for side in {c['side'] for c in cks}:   # 刷新口径: 本刻检查点未见 → 未知
                nq[side] = {}                       # (旧值不得残留, 见 test_..._refresh)
            for c in cks:
                nq[c['side']][int(_px(c))] = int(c['n_queue'])
            if ms > boundary:                      # 窗口 = (前样本, 本样本]
                for r in by_ms_r.get(ms, ()):
                    if r['kind'] in _FLOW_KINDS:
                        _flow_add(cur, r['kind'], int(r['qty']))
                for x in by_ms_s.get(ms, ()):
                    _flow_add(cur, 'sweep', sweep_vol=int(x['vol_before']))
            if si < n and samples[si] == ms:
                self._emit_sample(levels, nq, cur)
                cur = _empty_flow()
                si += 1
        while si < n:                              # 采样点晚于末事件: 冻结态直出
            self._emit_sample(levels, nq, cur)
            cur = _empty_flow()
            si += 1
        return self

    def _emit_sample(self, levels, nq, flow):
        self.states.append(_copy_state(levels))
        self.nqs.append(_copy_state(nq))
        self.flows.append(flow)

    def _apply_key(self, levels, side, px, all_rows, all_sweeps):
        """单 (ms, side, px) 键: 行按 seq 序; sweep 定位靠 vol_before 链位匹配

        折叠契约: 行携绝对量 (prev_vol/new_vol), sweep 只在该档清空时发 (vol_before =
        清空前量)。同 ms 内行与 sweep 分表 (两表 seq 各从 1 起, 不可互比), 相对位置须
        重建:

        ① 行流按 seq 序采纳绝对量; 链断 (prev != 现量) 即记 n_drift_chain 并直采
           new_vol —— 出带漂移的键仍能归位到行给出的真值。链位量 cs[k] = 第 k 行后
           的档量 (cs[0] = 入段量);
        ② sweep 位置在行流之后判定 (同 ms 行/扫单分表, seq 各从 1 起不可互比):
           - 无行 或 vol_before == cs[-1] (链末吻合) → 该档确被清空 → 终结;
           - 仅命中内部链位 cs[k] 且其后行为重建行 (prev==0) → 属"行前清档 + 同 ms
             重建"形: 行流绝对量即终值, 档在 (不删);
           - 无任何链位可解释 → 折叠缺行 (出带段的消耗/加单未落行, 链末不可作反证)
             → 仍按终结采纳, 并计 n_same_ms_ambiguous (报告口径, 不静默)。

        勿按 tail_order 行 id 定位: 实测 (000155.SZ@20260803 ms=34257180) tail 单的
        add 行在段中而其被成交清空在段末, 就地删档会被其后成交行"复活"成幽灵档;
        prev==0 的重建行亦不得消耗 sweep (000021.SZ@20260803 同因 19,539 例)。
        链语义: prev_vol == 折叠现量 (prev==0 与"无档"同义)。
        """
        _k = lambda r: r.get('seq') if r.get('seq') is not None else 0
        rows = sorted((r for r in all_rows if r['side'] == side
                       and int(_px(r)) == px), key=_k)
        sws = sorted((x for x in all_sweeps if x['side'] == side
                      and int(_px(x)) == px), key=_k)
        cs = [levels[side].get(px) or 0]           # cs[k] = 第 k 行后的档量 (k=0 入段量)
        for r in rows:
            if int(r['prev_vol']) != cs[-1]:
                self.n_drift_chain += 1            # 出带漂移/链断: 绝对量直接采纳
            nv = int(r['new_vol'])
            if nv:
                levels[side][px] = nv
            else:
                levels[side].pop(px, None)
            cs.append(nv)
        for x in sws:
            vb = int(x['vol_before'])
            if not rows or vb == cs[-1]:
                levels[side].pop(px, None)         # 终结: 无行 / 行流消耗恰清空该档
            elif any(cs[k] == vb and int(rows[k]['prev_vol']) == 0
                     for k in range(len(rows))):
                pass                               # 行前清档 + 重建行: 终值 = 行流末量
            else:
                self.n_same_ms_ambiguous += 1      # 折叠缺行形: 无从解释 → 仍终结
                levels[side].pop(px, None)


# ---------- Path B: 独立最小重放 ----------

_COUNTERS_B = ('dup_add', 'unknown_fill', 'unknown_cancel', 'fill_excess',
               'fill_over_rem', 'cancel_excess', 'zero_ref_fill')


class ReplayB:
    """规格簿语义的最小独立实现（不调用 engine.py）。

    与 engine.Engine 冻结语义逐条对应（规格 §2 + engine docstring）:
      阶段机 auction/match/continuous/post（config ms 阈值）; add 价>0 且
      continuous 才入簿（价=0 与竞价/撮合段仅登记 registry，竞价段发观测行
      prev==new==0）; fill/cancel 按 id 消费 min(qty, 剩余)（未知/超额分桶计数）;
      档空 → sweep 行 + 删档 + best 重扫（ghost 剪枝）; band = δ(对侧 best) ∪
      全簿 rank ≤ R 只抑制发射行（状态全深度保留）; 开盘物化 identity-preserving
      (cross_gate 交叉残留不入簿但身份保留)。

    属性: rows/sweeps（发射流, dict: kind/ms/side/price/qty/prev_vol/new_vol/
    id/otype）/ shadow（物化视图采样）/ books（全簿采样）/ trues（全簿在带视图
    采样, 漂移诊断）/ nqs（在带档活单数）/ flows / counters。
    """
    def __init__(self, samples, rank_limit=C.RANK_LIMIT, delta_pct=C.DELTA_PCT):
        self.samples = [int(t) for t in samples]
        self.rank_limit, self.delta_pct = rank_limit, delta_pct
        self.live = {'B': {}, 'S': {}}      # px -> {'vol': int, 'queue': [oid, ...]}
        self.reg = {}                       # oid -> dict(side, price, qty, rem, ...)
        self._px = {'B': [], 'S': []}
        self._best = {'B': None, 'S': None}
        self.rows, self.sweeps = [], []
        self._shadow = {'B': {}, 'S': {}}
        self.counters = {k: 0 for k in _COUNTERS_B}
        self.shadow, self.books, self.trues, self.nqs = [], [], [], []
        self.flows = []
        self._flow = _empty_flow()
        self._flow_on = False
        self._si = 0

    # ---- 簿面原语 ----

    def phase_at(self, ms):
        if ms < C.AUCTION_MATCH:
            return 'auction'
        if ms < C.OPEN:
            return 'match'
        if ms < C.CLOSE:
            return 'continuous'
        return 'post'

    def _booked_lv(self, side, px):
        """(必要时)建档 + 维护 best/有序价表 (与 engine._add 同序: 先建后判 band)"""
        lv = self.live[side].get(px)
        if lv is None:
            lv = {'vol': 0, 'queue': []}
            self.live[side][px] = lv
            insort(self._px[side], px)
            b = self._best[side]
            if b is None or (px > b if side == 'B' else px < b):
                self._best[side] = px
        return lv

    def in_band(self, side, px):
        ob = self._best['S' if side == 'B' else 'B']
        if ob is not None and abs(px - ob) <= self.delta_pct * ob:
            return True
        rank = bisect_left(self._px['B'], px) + bisect_left(self._px['S'], px) + 1
        return rank <= self.rank_limit

    def _drop_level(self, side, px, vol_before, tail_oid, ms):
        self.sweeps.append(dict(kind='sweep', ms=ms, side=side, price=px,
                                vol_before=vol_before, tail_order=tail_oid,
                                tail_resid=0))
        del self.live[side][px]
        self._px[side].remove(px)
        self._shadow[side].pop(px, None)        # 物化视图同删 (sweep 行契约)
        if px == self._best[side]:
            lv = self.live[side]
            self._best[side] = (max(lv) if lv else None) if side == 'B' else \
                (min(lv) if lv else None)
        if self._flow_on:
            _flow_add(self._flow, 'sweep', sweep_vol=vol_before)

    def _emit(self, kind, ms, side, px, qty, prev, new, oid=None, otype=None):
        self.rows.append(dict(kind=kind, ms=ms, side=side, price=px, qty=qty,
                              prev_vol=prev, new_vol=new, id=oid, otype=otype,
                              phase=self.phase_at(ms)))
        if prev == 0 and new == 0:
            return                              # 观测行: 簿面无效应
        self._shadow[side][px] = new

    # ---- 事件处理 ----

    def _add(self, e):
        oid, ms = e['id'], e['ms']
        side, px, qty = e['side'], e['price'], e['qty']
        ph = self.phase_at(ms)
        if oid in self.reg:
            self.counters['dup_add'] += 1
            return
        booked = px > 0 and ph == 'continuous'
        self.reg[oid] = dict(id=oid, side=side, price=px, qty=qty, rem=qty,
                             phase=ph, booked=booked)
        lv = self.live[side].get(px)
        prev = lv['vol'] if lv else 0
        if booked:
            lv = self._booked_lv(side, px)
            lv['queue'].append(oid)
            lv['vol'] += qty
            if not self.in_band(side, px):
                return                          # 出带: 状态保留, 不发事件行
            new = lv['vol']
        else:
            new = prev
            if ph == 'continuous':
                return                          # 连续段价=0 市价类: 不发簿行
        self._emit('add', ms, side, px, qty, prev, new, oid, e.get('otype'))
        if self._flow_on:
            _flow_add(self._flow, 'add', qty)

    def _consume(self, oid, qty, ms, kind):
        """fill/cancel 共用消费通道 (engine._fill/_cancel 同构): min(qty, 剩余)

        流水与发射行同源: 档清空 → 只计 sweep; 出带 → 不计 (行流口径)。"""
        rec = self.reg.get(oid)
        if rec is None:
            self.counters['unknown_fill' if kind == 'fill'
                          else 'unknown_cancel'] += 1
            return
        if rec['rem'] == 0:
            self.counters['fill_excess' if kind == 'fill'
                          else 'cancel_excess'] += 1
            return
        if qty > rec['rem']:
            self.counters['fill_over_rem' if kind == 'fill'
                          else 'cancel_excess'] += 1
        c = qty if qty <= rec['rem'] else rec['rem']
        rec['rem'] -= c
        if not rec['booked']:
            return
        side, px = rec['side'], rec['price']
        ek = 'trade' if kind == 'fill' else 'cancel'
        lv = self.live[side][px]
        lv['vol'] -= c
        if rec['rem'] == 0:
            lv['queue'].remove(oid)
            if not lv['queue']:
                self._drop_level(side, px, lv['vol'] + c, oid, ms)
                return
        new = lv['vol']
        if self.in_band(side, px):
            self._emit(ek, ms, side, px, c, new + c, new, oid)
            if self._flow_on:
                _flow_add(self._flow, ek, c)

    def _fill(self, e):
        refs = e.get('refs')
        if refs is None:
            rid = e.get('id')
            refs = (rid,) if rid else ()
        for ref in refs:
            if not ref:
                self.counters['zero_ref_fill'] += 1
                continue
            self._consume(ref, e['qty'], e['ms'], 'fill')

    def _cancel(self, e):
        self._consume(e['id'], e['qty'], e['ms'], 'cancel')

    def materialize(self, ms, gate=None):
        """开盘物化 (engine.materialize_leftovers 同构; 返回物化行; 不计流水)"""
        agg, rows = {}, []
        for rec in self.reg.values():
            if rec['booked'] or rec['rem'] <= 0 or rec['price'] <= 0 \
                    or rec['phase'] not in ('auction', 'match'):
                continue
            side, px = rec['side'], rec['price']
            lim = (gate or {}).get(side)
            if lim is not None and (px >= lim if side == 'B' else px <= lim):
                continue                        # 交叉残留: 不入簿, 身份保留
            a = agg.get((side, px))
            if a is None:
                lv0 = self.live[side].get(px)
                a = agg[(side, px)] = dict(prev_vol=lv0['vol'] if lv0 else 0,
                                           qty=0)
            lv = self._booked_lv(side, px)
            lv['queue'].append(rec['id'])
            lv['vol'] += rec['rem']
            a['qty'] += rec['rem']
            rec['booked'] = True
        for (side, px), a in agg.items():
            self._emit('level_materialization', ms, side, px, a['qty'],
                       a['prev_vol'], self.live[side][px]['vol'])
            rows.append(self.rows[-1])
        return rows

    # ---- 驱动 ----

    def run(self, events, first_anchor_ms=None, gate=None):
        evs = sorted(events, key=lambda e: (e['ms'], _KIND_PRIO.get(e['kind'], 9)))
        done_mat = first_anchor_ms is None
        for e in evs:
            if not done_mat and e['ms'] > first_anchor_ms:
                self._at(first_anchor_ms)
                self.materialize(first_anchor_ms, gate)
                done_mat = True
            self._step(e)
        if not done_mat:
            self._at(first_anchor_ms)
            self.materialize(first_anchor_ms, gate)
        while self._si < len(self.samples):
            self._snapshot()
        return self

    def _at(self, ms):
        """置当前事件刻 + 流水窗判定 (事件刻 > 窗起点才计流水; 起点=前一采样/OPEN)"""
        self._flow_on = ms > (self.samples[self._si - 1] if self._si > 0
                              else C.OPEN)

    def _step(self, e):
        while self._si < len(self.samples) and self.samples[self._si] < e['ms']:
            self._snapshot()
        self._at(e['ms'])
        k = e['kind']
        if k == 'add':
            self._add(e)
        elif k == 'fill':
            self._fill(e)
        elif k == 'cancel':
            self._cancel(e)

    def _snapshot(self):
        i = self._si
        self.shadow.append(_copy_state(self._shadow))
        self.books.append({s: {px: lv['vol'] for px, lv in self.live[s].items()}
                           for s in ('B', 'S')})
        self.trues.append({s: {px: lv['vol'] for px, lv in self.live[s].items()
                               if self.in_band(s, px)} for s in ('B', 'S')})
        self.nqs.append({s: {px: len(lv['queue'])
                             for px, lv in self.live[s].items()
                             if self.in_band(s, px)} for s in ('B', 'S')})
        self.flows.append(self._flow)
        self._flow = _empty_flow()
        self._si = i + 1


# ---------- 采样派生 (1s 超集 → 1m 子集) ----------

def select_grid(states, nqs, flows, samples, target):
    """源采样序列 (超集) → 目标采样序列 (子集): 态按索引取; 流水按窗口求和

    窗口 (前目标样本, 本目标样本] 由源窗口无缝拼接 → 计数/量直加, sweep_vol_max
    取窗口内最大 (max 可结合 ✓)。目标必须是源子集 (assert)。
    """
    idx = {t: i for i, t in enumerate(samples)}
    assert all(t in idx for t in target), 'target 非源采样子集'
    out_st, out_nq, out_fl = [], [], []
    prev_i = -1
    for t in target:
        i = idx[t]
        acc = _empty_flow()
        for k in range(prev_i + 1, i + 1):
            for key, v in flows[k].items():
                if key == 'sweep_vol_max':
                    if v > acc[key]:
                        acc[key] = v
                else:
                    acc[key] += v
        out_st.append(states[i])
        out_nq.append(nqs[i])
        out_fl.append(acc)
        prev_i = i
    return out_st, out_nq, out_fl


# ---------- M7 闸门 ----------

def m7_gate(a, b, samples, aligned, tol=1e-6):
    """M7 同位门: a/b = dict(rows, sweeps, states, nqs, panel)。

    rows/sweeps 可给元组（已归一）或 dict（表列名/重放 schema 皆可，见 _norm）。
    返回 dict(ok, n_samples, n_rows, n_state_levels, row_mismatch, sweep_mismatch,
    state_mismatch, phantom, nq_mismatch, nq_compared, factor_delta_max,
    factor_mismatch): 任一项非空 → ok=False。四项各自独立检测 → 硬编码/存根必败。
    """
    g = dict(ok=True, n_samples=len(samples), n_rows=len(a['rows']),
             n_state_levels=0, row_mismatch=[], sweep_mismatch=[],
             state_mismatch=[], phantom=[], nq_mismatch=[], nq_compared=0,
             factor_delta_max=0.0, factor_mismatch=[])
    ra, rb = _norm_rows(a['rows']), _norm_rows(b['rows'])
    sa, sb = _norm_sweeps(a['sweeps']), _norm_sweeps(b['sweeps'])
    if ra != rb:
        g['row_mismatch'] = _first_diff(ra, rb)
    if sa != sb:
        g['sweep_mismatch'] = _first_diff(sa, sb)
    for i in range(len(samples)):
        for side in ('B', 'S'):
            la, lb = a['states'][i][side], b['states'][i][side]
            g['n_state_levels'] += len(lb)
            for px, vol in lb.items():
                if la.get(px) != vol:
                    g['state_mismatch'].append((i, samples[i], side, px, vol,
                                                la.get(px)))
            for px in la:
                if px not in lb:
                    g['phantom'].append((i, samples[i], side, px, la[px]))
    pa, pb = a['panel'], b['panel']
    for i in range(len(samples)):
        if samples[i] in aligned:
            g['nq_compared'] += 1               # nq 对齐样本数 (A 侧为检查点口径)
    for col in FACTOR_COLS:
        for i in range(len(samples)):
            if col in NQ_COLS and samples[i] not in aligned:
                continue
            va, vb = pa[i].get(col), pb[i].get(col)
            d = abs(float(va) - float(vb)) if (va is not None and vb is not None) \
                else (0.0 if va == vb else float('inf'))
            if d > g['factor_delta_max']:
                g['factor_delta_max'] = d
            if d > tol:
                rec = (i, samples[i], col, va, vb)
                if col in NQ_COLS:
                    g['nq_mismatch'].append(rec)
                else:
                    g['factor_mismatch'].append(rec)
    if (g['row_mismatch'] or g['sweep_mismatch'] or g['state_mismatch']
            or g['phantom'] or g['nq_mismatch'] or g['factor_mismatch']):
        g['ok'] = False
    return g


def _first_diff(xa, xb, n=5):
    out = []
    for i in range(max(len(xa), len(xb))):
        va = xa[i] if i < len(xa) else None
        vb = xb[i] if i < len(xb) else None
        if va != vb:
            out.append((i, va, vb))
            if len(out) >= n:
                break
    return out


# ---------- 表读入 / 单 code-day 驱动 ----------

def _read_lob(lob_root, table, day, code):
    """单 code 的 lob 日文件（R4a：收敛到平台单点 adapters.lob_read 经 lib.tickdata）。"""
    return T.read_lob(table, day, codes=[code], root=lob_root, missing_ok=True)


def _read_tick(tick_root, table, day, code):
    """单 code 的 tick 月切片（投影 = 研究侧声明；缺数据 → None）。"""
    return T.read_tick(table, day, codes=[code], root=tick_root, missing_ok=True)


def _anchors(snaps):
    """快照行 → (首锚 ms, 物化交叉闸门) (anchoring.run_day 同口径: ≥ OPEN 首张,
    闸门 = {'B': ask_p1, 'S': bid_p1})"""
    first, gate = None, None
    for row in sorted(snaps, key=lambda r: int(r['time_ms'])):
        ms = int(row['time_ms'])
        if ms < C.OPEN:
            continue
        first = ms
        b1, a1 = row.get('bid_p1'), row.get('ask_p1')
        b1 = int(round(b1)) if b1 else None
        a1 = int(round(a1)) if a1 else None
        if b1 is not None or a1 is not None:
            gate = {'B': a1, 'S': b1}
        break
    return first, gate


def run_code_day(code, day, tick_root, lob_root, grids=('1s', '1m')):
    """单 code-day: A 折叠 / B 重放 / 因子面板 / M7 门 (按 grid)。

    1s 为主跑 (采样超集), 1m 由 select_grid 派生 (态按索引, 流水按窗口和) — 两路
    用同一派生函数, 派生本身不引入路径差。返回 dict(code, day, panel, m7, drift,
    diag, guard)。
    """
    if isinstance(day, str):
        day = _date(int(day[:4]), int(day[4:6]), int(day[6:8]))
    ev_df = _read_lob(lob_root, 'lob_events', day, code)
    sw_df = _read_lob(lob_root, 'lob_sweep_meta', day, code)
    ck_df = _read_lob(lob_root, 'lob_checkpoints', day, code)
    if ev_df is None or sw_df is None or ck_df is None:
        raise FileNotFoundError(f'lob_fact 缺表: {code} {day:%Y%m%d}')
    rows = ev_df.sort('seq').to_dicts()
    sweeps = sw_df.sort('seq').to_dicts()
    ckpts = ck_df.sort(['time_ms', 'seq']).to_dicts()
    ck_ms = sorted({int(c['time_ms']) for c in ckpts})
    first_ckpt = ck_ms[0] if ck_ms else C.OPEN + C.MINUTE_MS

    o = _read_tick(tick_root, 'orders', day, code)
    t = _read_tick(tick_root, 'trades', day, code)
    c = _read_tick(tick_root, 'cancels', day, code)
    evs = _events.parquet_events(code, day,
                           o.to_dicts() if o is not None else [],
                           t.to_dicts() if t is not None else [],
                           c.to_dicts() if c is not None else [])
    sn = _read_tick(tick_root, 'snapshots', day, code)
    first_a, gate = _anchors(sn.to_dicts() if sn is not None else [])

    grid_specs = {'1s': sample_times(GRID_1S), '1m': sample_times(GRID_1M)}
    src = [x for x in grid_specs['1s'] if x >= first_ckpt]
    fold = BandFold().run(rows, sweeps, ckpts, src)
    b = ReplayB(src)
    b.run(evs, first_anchor_ms=first_a, gate=gate)

    aligned = set(ck_ms) & set(src)
    panel, m7, drift = {}, {}, {}
    for gname in grids:
        tgt = [x for x in grid_specs[gname] if x >= first_ckpt]
        if tgt == src:
            st, nq, fl, st_b, nq_b, fl_b = (fold.states, fold.nqs, fold.flows,
                                            b.shadow, b.nqs, b.flows)
        else:
            st, nq, fl = select_grid(fold.states, fold.nqs, fold.flows, src, tgt)
            st_b, nq_b, fl_b = select_grid(b.shadow, b.nqs, b.flows, src, tgt)
        pa = panel_rows(st, nq, fl, tgt, code, day, gname)
        pb = panel_rows(st_b, nq_b, fl_b, tgt, code, day, gname)
        panel[gname] = pa
        m7[gname] = m7_gate(dict(rows=fold.flat_rows, sweeps=fold.flat_sweeps,
                                 states=st, nqs=nq, panel=pa),
                            dict(rows=b.rows, sweeps=b.sweeps, states=st_b,
                                 nqs=nq_b, panel=pb),
                            tgt, aligned & set(tgt))
        drift[gname] = _drift_report(fold.states, b.trues, src, tgt)
    return dict(code=code, day=day, panel=panel, m7=m7, drift=drift,
                diag=dict(n_events=len(evs), n_rows_fold=len(fold.flat_rows),
                          n_rows_b=len(b.rows), n_sweeps=len(sweeps),
                          n_samples_1s=len(src), first_ckpt=first_ckpt,
                          n_rows_b_table=len(_norm_rows(b.rows)),
                          first_anchor=first_a, gate=gate,
                          n_drift_chain=fold.n_drift_chain,
                          n_same_ms_ambiguous=fold.n_same_ms_ambiguous,
                          counters_b=dict(b.counters)))


def _drift_report(states_a, trues, src, tgt):
    """band 边缘漂移诊断: A 折叠视图 vs B 全簿在带视图 (同类口径, 非同刻全等)"""
    idx = {t: i for i, t in enumerate(src)}
    n_cmp = n_diff = tot = 0
    for t in tgt:
        i = idx[t]
        for side in ('B', 'S'):
            for px, vol in trues[i][side].items():
                n_cmp += 1
                tot += vol
                if states_a[i][side].get(px) != vol:
                    n_diff += 1
    return dict(n_compared=n_cmp, n_drift_levels=n_diff, vol_total=tot,
                drift_rate=round(n_diff / n_cmp, 6) if n_cmp else 0.0)


# ---------- 面板落盘 ----------

def _panel_df(rows):
    cols = {c: [] for c in PANEL_COLS}
    for r in rows:
        for c in PANEL_COLS:
            cols[c].append(r[c])
    schema = {'code': pl.Utf8, 'trade_date': pl.Date, 'grid': pl.Utf8,
              'time_ms': pl.Int32}
    for c in FACTOR_COLS:
        schema[c] = pl.Float64 if c in FLOAT_COLS else pl.Int64
    return pl.DataFrame([pl.Series(c, cols[c], dtype=schema[c])
                         for c in PANEL_COLS])


def write_panel(out_root, grid, day, rows):
    """单 (grid, date) 面板原子落盘 (tmp + fsync + os.replace); 返回 Path

    布局 = <out_root>/panel_<grid>/year=YYYY/month=MM/YYYYMMDD.parquet
    (date 文件含全部 code; 逐 code-day 调用 write_panel 会覆盖 → 批量跑需先聚合)。"""
    d = str(partitions.partition_dir(Path(out_root) / f'panel_{grid}', table=None,
                                     year=day.year, month=day.month))
    os.makedirs(d, exist_ok=True)
    final = os.path.join(d, f'{day:%Y%m%d}.parquet')
    tmp = os.path.join(d, f'.{day:%Y%m%d}.parquet.tmp.{os.getpid()}')
    _panel_df(rows).write_parquet(tmp)
    with open(tmp, 'rb') as f:
        os.fsync(f.fileno())
    os.replace(tmp, final)
    return Path(final)


def write_panels(out_root, panel, code, day):
    """便利封装: 单 code-day {grid: rows} → {grid: Path} (行自带 code, code 仅签名)"""
    return {g: write_panel(out_root, g, day, rows) for g, rows in panel.items()}


# ---------- CLI ----------

def _parse(argv=None):
    ap = argparse.ArgumentParser(description='W6 盘口因子面板 + M7 同位门')
    ap.add_argument('--dates', required=True, help='逗号分隔 YYYYMMDD')
    ap.add_argument('--codes', required=True, help='逗号分隔 code（后缀形）')
    ap.add_argument('--tick-root', default=C.TICK_FACT_ROOT)
    ap.add_argument('--lob-root', default=C.LOB_FACT_ROOT)
    ap.add_argument('--out', default=C.LOB_FACT_ROOT, help='panel_* 落盘根')
    ap.add_argument('--grids', default='1s,1m')
    ap.add_argument('--report', default=None, help='报告 json 路径')
    return ap.parse_args(argv)


def main(argv=None):
    args = _parse(argv)
    grids = tuple(args.grids.split(','))
    dates = [_date(int(d[:4]), int(d[4:6]), int(d[6:8]))
             for d in args.dates.split(',')]
    codes = args.codes.split(',')
    t0 = time.time()
    report = dict(dates=args.dates, codes=codes, grids=list(grids),
                  per_code={}, m7={}, drift={},
                  started=time.strftime('%F %T'))
    for day in dates:
        acc = {g: [] for g in grids}
        for code in codes:
            rep = run_code_day(code, day, args.tick_root, args.lob_root, grids)
            key = f'{code}@{day:%Y%m%d}'
            for g in grids:
                acc[g].extend(rep['panel'][g])
                report['m7'].setdefault(g, {})[key] = {
                    k: (len(v) if isinstance(v, list) else v)
                    for k, v in rep['m7'][g].items()}
                report['drift'].setdefault(g, {})[key] = rep['drift'][g]
            report['per_code'][key] = rep['diag']
            bad = [g for g in grids if not rep['m7'][g]['ok']]
            print(f'[{time.time() - t0:.0f}s] {key} '
                  f'samples={rep["diag"]["n_samples_1s"]} '
                  f'rows_fold={rep["diag"]["n_rows_fold"]}'
                  f'/b={rep["diag"]["n_rows_b"]} '
                  f'm7={"PASS" if not bad else "FAIL:" + ",".join(bad)} '
                  f'drift={rep["drift"][grids[0]]["drift_rate"]}', flush=True)
        for g in grids:
            p = write_panel(args.out, g, day, acc[g])
            print(f'  wrote {p} rows={len(acc[g])}', flush=True)
    report['elapsed_s'] = round(time.time() - t0, 1)
    out = args.report or os.path.join(
        args.out, 'panel_runs', time.strftime('%Y%m%d_%H%M%S') + '_panel.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w') as f:
        json.dump(report, f, ensure_ascii=False, indent=1, default=str)
    print(f'report → {out}', flush=True)
    n_fail = sum(1 for d in report['m7'].values() for v in d.values()
                 if not v['ok'])
    return 0 if n_fail == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
