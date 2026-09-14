"""lob_fact 逐单事件级订单簿引擎（W2，双所对称单实现，显式阶段机）

输入 = 归一化事件（qa/streams 产出；金样 CSV 同构）：
  add(id, side, price, qty, otype)    价>0 ⇒ 簿（连续段，任何类型；W1 冻结"按价不按类型"）
                                      价=0 ⇒ 仅登 registry（'1'/U 永不进簿）
                                      竞价/撮合段 ⇒ 仅登 registry（只观测，簿不重建）
  fill(ms, qty, refs=[...])           逐 ref 消费 min(qty, 订单剩余)；ref=0(空) → zero_ref_fill；
                                      未登记 → unknown_fill；已全消单 → fill_excess（SH 合并打印语义桶）
  cancel(id, qty)                     SH D / SZ C 同通道（W1 全撤剩余量语义，无类型分支）；
                                      消费 min(qty, 剩余)；超量 → cancel_excess；未知 → unknown_cancel

阶段机（config 常量，ms 自动切换）：auction(<09:25) → match(09:25-09:30 撮合打印) →
  continuous(09:30-15:00, 两段簿携入) → post(≥15:00)。竞价 adds 只观测；撮合打印消费 registry
  （不触碰簿）；连续段价>0 全深度入簿 + 档内 FIFO 队列（deque，部分撤/部分成交不换序）。

簿面 = books[side] = {price_x10000: {'vol', 'queue'}}，ghost 剪枝：档量归零即 del + sweep 行。
事件行（events）每 (事件, 触碰价档) 一行带 prev/new 绝对量；band gating 只抑制事件行
  （状态全深度保留）：rank = 全簿(双侧)活档中价严格低于本档的档数+1（W2 测试冻结语义）≤ R
  或 |价−对侧 best| ≤ δ_pct×对侧 best。registry-only adds 在竞价/撮合/收盘段发观测行；
  连续段价=0 市价类不发簿行。sweeps 稀疏（仅档耗尽），不受 band 抑制。

确定性：ingest 先按 (ms, kind 序 add<fill<cancel) 稳定排序再处理（同 ms 同 kind 保留输入序
  ——生产输入文件行序即确定性序）。不变量：每档 vol == Σ 档内订单 rem（测试逐事件断言）。
EOD：registry 残单分桶 auction_rem（竞价/撮合段加单）/ continuous_rem（连续段入簿）/
  unbooked_rem（连续段价=0 永不进簿）。
"""
import gc
from bisect import bisect_left, insort
from collections import deque

from core import config as C

_KIND_PRIO = {'add': 0, 'fill': 1, 'cancel': 2}
_COUNTERS = ('unknown_fill', 'fill_excess', 'fill_over_rem', 'unknown_cancel',
             'cancel_excess', 'dup_add', 'zero_ref_fill', 'unknown_kind')
_M3_BUCKETS = ('print_in_spread', 'print_below_bid', 'print_above_ask',
               'print_no_quote')
_M3_EPS = C.EPS_TICKS * C.TICK_UNITS        # M3 ε = 1 tick (×10000 units)


class Engine:
    """全深度逐单订单簿。构造: Engine(rank_limit=R, delta_pct=δ)（默认 config）。"""

    def __init__(self, rank_limit=C.RANK_LIMIT, delta_pct=C.DELTA_PCT):
        self.rank_limit = rank_limit
        self.delta_pct = delta_pct
        self.books = {'B': {}, 'S': {}}   # side → {px: {'vol': int, 'queue': deque[oid]}}
        self._px = {'B': [], 'S': []}     # 双侧活档价排序列表（rank 计档用）
        self._orders = {}                 # oid → rec（registry；簿内外全含）
        self.counters = {k: 0 for k in _COUNTERS}
        self.m3 = {k: 0 for k in _M3_BUCKETS}   # M3 打印价合法性桶 (逐 fill 事件, 仅连续段)
        self.events = []                  # 事件行（band 抑制后）
        self.sweeps = []                  # 档耗尽稀疏行（尾单 id + 残余）
        self.eod_summary = {'auction_rem': 0, 'continuous_rem': 0, 'unbooked_rem': 0}
        self.stats = {'max_levels': 0, 'max_queue': 0}   # W4 worker 内存定标观测
        # ---- 热路径缓存（事件流按 ms 单调 → 相位/双侧 best 增量维护合法） ----
        self._ph = 'auction'
        self._ph_ms = -1
        self._best = {'B': None, 'S': None}   # 活档 best（增量更新, 跌出时重扫）

    # ---------- 阶段机 ----------

    @staticmethod
    def phase_at(ms):
        """ms-of-day → 阶段（config 阈值；午休窗无行数据，并入 continuous）"""
        if ms < C.AUCTION_MATCH:
            return 'auction'
        if ms < C.OPEN:
            return 'match'
        if ms < C.CLOSE:
            return 'continuous'
        return 'post'

    def _phase(self, ms):
        """单调缓存相位（事件流 ms 升序 → 越过边界才重算; ms 回退时全量重算）"""
        if ms < self._ph_ms:                      # 非单调（防滥用）
            self._ph = self.phase_at(ms)
            self._ph_ms = ms
            return self._ph
        self._ph_ms = ms
        if self._ph == 'auction':
            if ms >= C.AUCTION_MATCH:
                self._ph = 'match'
        if self._ph == 'match' and ms >= C.OPEN:
            self._ph = 'continuous'
        if self._ph == 'continuous' and ms >= C.CLOSE:
            self._ph = 'post'
        return self._ph

    # ---------- 簿查询 ----------

    def level_state(self, side, px):
        lv = self.books[side].get(px)
        if lv is None:
            return {'vol': 0, 'orders': []}
        return {'vol': lv['vol'], 'orders': list(lv['queue'])}

    def level_vol(self, side, px):
        lv = self.books[side].get(px)
        return lv['vol'] if lv else 0

    def side_books(self, side):
        return {px: {'vol': lv['vol'], 'orders': list(lv['queue'])}
                for px, lv in self.books[side].items()}

    def best(self, side):
        lv = self.books[side]
        if not lv:
            return None
        return max(lv) if side == 'B' else min(lv)

    def in_band(self, side, px):
        """带判定公开谓词（=_band: δ 对侧 best ±δ_pct ∪ rank≤R, 只判不改簿面）—
        行带 (emit 抑制) 与 W4 带限检查点共用同一判定"""
        return self._band(side, px)

    def order(self, oid):
        return self._orders.get(oid)

    def registry_seq(self, side, px):
        """(oid, rec) 按该侧该价档 FIFO 加单序（与 level_state orders 同源）"""
        lv = self.books[side].get(px)
        if lv is None:
            return
        for oid in lv['queue']:
            yield oid, self._orders[oid]

    # ---------- 处理 ----------

    def ingest(self, events):
        """归一化事件流 → (ms, kind) 确定性排序 → 逐事件重放（可多次调用）

        百万级事件日 GC 关停（簿面无环引用, 无泄漏; 大列表归调用方持有）。
        输入已 (ms, kind) 有序（批算向量化预排序链）则跳过排序。
        """
        n = len(events)
        if n > 0:
            _k = _KIND_PRIO
            # 单调性沿序检查 (ms, prio)；已有序（批算向量化预排序链）→ 免排序
            mono = True
            pm = events[0]['ms']
            pp = _k.get(events[0]['kind'], 9)
            for ev in events[1:]:
                m = ev['ms']
                p = _k.get(ev['kind'], 9)
                if m < pm or (m == pm and p < pp):
                    mono = False
                    break
                pm, pp = m, p
            if not mono:
                events = sorted(events, key=lambda e: (e['ms'], _k.get(e['kind'], 9)))
        gc_off = gc.isenabled()
        if gc_off:
            gc.disable()
        try:
            for ev in events:
                k = ev['kind']
                if k == 'add':
                    self._add(ev)
                elif k == 'fill':
                    self._fill(ev)
                elif k == 'cancel':
                    self._cancel(ev)
                else:
                    self.counters['unknown_kind'] += 1
        finally:
            if gc_off:
                gc.enable()
        return self

    def _band(self, side, px):
        """事件行 band gating（OR 语义, 先 δ 后 rank——触档事件绝大多数近价, δ O(1) 短路;
        rank = 全簿(双侧)活档价 < px 的档数 + 1 ≤ R 兜底稀疏深档）。只抑制行, 不改簿面。"""
        ob = self._best['S' if side == 'B' else 'B']
        if ob is not None and abs(px - ob) <= self.delta_pct * ob:
            return True
        rank = bisect_left(self._px['B'], px) + bisect_left(self._px['S'], px) + 1
        return rank <= self.rank_limit

    def _add(self, ev):
        oid = ev['id']
        side, px, qty = ev['side'], ev['price'], ev['qty']
        ms = ev['ms']
        ph = self._phase(ms)
        if oid in self._orders:
            self.counters['dup_add'] += 1
            return
        booked = px > 0 and ph == 'continuous'
        self._orders[oid] = dict(id=oid, ms=ms, side=side, price=px, qty=qty,
                                 added=qty, otype=ev.get('otype'), phase=ph,
                                 rem=qty, booked=booked,
                                 filled=0, canceled=0)      # M4 逐单账
        lv = self.books[side].get(px)
        prev_vol = lv['vol'] if lv else 0
        if booked:
            if lv is None:
                lv = {'vol': 0, 'queue': deque()}
                self.books[side][px] = lv
                insort(self._px[side], px)
                n = len(self.books['B']) + len(self.books['S'])
                if n > self.stats['max_levels']:
                    self.stats['max_levels'] = n
                b = self._best[side]
                if b is None or (px > b if side == 'B' else px < b):
                    self._best[side] = px
            lv['queue'].append(oid)
            if len(lv['queue']) > self.stats['max_queue']:
                self.stats['max_queue'] = len(lv['queue'])
            lv['vol'] += qty
            if not self._band(side, px):
                return                       # 出带：状态保留，不发事件行
            new_vol = lv['vol']
        else:
            new_vol = prev_vol               # registry-only：簿面无变化
            if ph == 'continuous':
                return                       # 连续段价=0 市价类：观测计数在 registry，不发簿行
        self.events.append(dict(kind='add', ms=ms, side=side, price=px, qty=qty,
                                id=oid, otype=ev.get('otype'), phase=ph,
                                prev_vol=prev_vol, new_vol=new_vol))

    def _fill(self, ev):
        ms = ev['ms']
        qty = ev['qty']
        ph = self._phase(ms)
        # M3 打印合法性 (连续段 + 带价才分桶; eps = 1 tick, qa.metrics 同口径):
        # 消费前对簿面双侧 best 分类 — 打印价的合法性独立于 ref 可解析性
        px = ev.get('price')
        if px and ph == 'continuous':
            bb, ba = self._best['B'], self._best['S']
            if bb is None or ba is None:
                self.m3['print_no_quote'] += 1
            elif px < bb - _M3_EPS:
                self.m3['print_below_bid'] += 1
            elif px > ba + _M3_EPS:
                self.m3['print_above_ask'] += 1
            else:
                self.m3['print_in_spread'] += 1
        # 双形态: 金样 refs 列表 / streams 单 ref 事件 (bid/ask 各一条 fill, id=ref)
        refs = ev.get('refs')
        if refs is None:
            rid = ev.get('id')
            refs = (rid,) if rid else ()
        for ref in refs:
            if not ref:
                self.counters['zero_ref_fill'] += 1   # SH 先成交后报空引用类（非错）
                continue
            rec = self._orders.get(ref)
            if rec is None:
                self.counters['unknown_fill'] += 1    # SH taker 未登记类（簿面不动）
                continue
            if rec['rem'] == 0:
                self.counters['fill_excess'] += 1     # 已全消单的成交 ref（防御桶）
                continue
            if qty > rec['rem']:
                self.counters['fill_over_rem'] += 1   # 活单超量（SH 合并打印同 ms 类）—
                # min 消费, 与死单 fill_excess 分桶 (M4 映射: eng excess+over == ledger)
            c = qty if qty <= rec['rem'] else rec['rem']
            rec['rem'] -= c
            rec['filled'] += c
            if rec['booked']:
                side, px = rec['side'], rec['price']
                lv = self.books[side][px]
                lv['vol'] -= c
                if rec['rem'] == 0:
                    lv['queue'].remove(ref)
                    if not lv['queue']:
                        self._drop_level(side, px, lv['vol'] + c, ref, ms, ph)
                        continue
                new_vol = lv['vol']
                if self._band(side, px):
                    self.events.append(dict(kind='trade', ms=ms, side=side,
                                            price=px, qty=c, id=ref, phase=ph,
                                            prev_vol=new_vol + c, new_vol=new_vol))

    def _cancel(self, ev):
        """双所同构撤单通道（SH D / SZ C 同一代码路径，无类型分支）：
        消费 min(qty, 剩余)；超量/已消 → cancel_excess；未知 → unknown_cancel"""
        ms = ev['ms']
        ph = self._phase(ms)
        oid, qty = ev['id'], ev['qty']
        rec = self._orders.get(oid)
        if rec is None:
            self.counters['unknown_cancel'] += 1
            return
        if qty > rec['rem']:
            self.counters['cancel_excess'] += 1
        if rec['rem'] == 0:
            return
        c = qty if qty <= rec['rem'] else rec['rem']
        rec['rem'] -= c
        rec['canceled'] += c
        if rec['booked']:
            side, px = rec['side'], rec['price']
            lv = self.books[side][px]
            lv['vol'] -= c
            if rec['rem'] == 0:
                lv['queue'].remove(oid)
                if not lv['queue']:
                    self._drop_level(side, px, lv['vol'] + c, oid, ms, ph)
                    return
            new_vol = lv['vol']
            if self._band(side, px):
                self.events.append(dict(kind='cancel', ms=ms, side=side,
                                        price=px, qty=c, id=oid, phase=ph,
                                        prev_vol=new_vol + c, new_vol=new_vol))

    def _drop_level(self, side, px, vol_before, tail_oid, ms, ph):
        """档耗尽（ghost 剪枝）：sweep 稀疏行 + 删档（双侧 rank 列表同步）"""
        self.sweeps.append(dict(kind='sweep', ms=ms, side=side, price=px, phase=ph,
                                vol_before=vol_before, tail_order=tail_oid,
                                tail_resid=0))
        del self.books[side][px]
        self._px[side].remove(px)
        if px == self._best[side]:            # best 档耗尽 → 重扫
            lv = self.books[side]
            self._best[side] = (max(lv) if lv else None) if side == 'B' else \
                (min(lv) if lv else None)

    def registry_leftovers(self):
        """开盘物化候选: 竞价/撮合段残留 (价>0, 剩余>0, 未入簿)，按加单序 (dict 插序)"""
        for rec in self._orders.values():
            if (not rec['booked'] and rec['rem'] > 0 and rec['price'] > 0
                    and rec['phase'] in ('auction', 'match')):
                yield rec

    def materialize_leftovers(self, ms, cross_gate=None):
        """开盘物化（anchoring 调用）: registry 竞价残留逐单入簿（身份保留, 同价 FIFO=加单序,
        物化前已建档的突发单在前——确定性 tie-break），返回新增事件行
        (kind=level_materialization, 每 (side,px) 一行: prev_vol=既有档量(无=0),
        new_vol=物化后最终档量=既有+Σ残留——行流 fold 重建簿面的绝对量契约)。
        已被撮合打印吃光/价=0/连续段单不触碰。物化后 EOD 归 continuous_rem。

        cross_gate: 首锚对侧 best 闸门 {side: 价}（B=锚 ask best, S=锚 bid best）——
        残留价越过对侧 best (B px ≥ gate['B'] / S px ≤ gate['S']) 的交叉档真实交易所
        开盘簿绝不携带 (开盘瞬间已消化或静默撤销; W3 实测 SH 600036@20251215
        B425700×10600 与 S375700 系, SZ 000021@20260706 ask 559000×345300 均不现于
        09:30:00.000+ 任何快照, 而闸门内侧同档照常携带) → 不入簿但**保留 registry
        身份**: 后续 fills/cancels 仍按 id 消费 rem (followup 实证), unknown 桶不误计,
        EOD 归 auction_rem。"""
        if not any(self.registry_leftovers()):
            return []
        ph = self._phase(ms)
        agg = {}                       # (side, px) → {prev_vol: 首触时既有档量, qty: Σ残留}
        rows = []
        for rec in self.registry_leftovers():
            side, px = rec['side'], rec['price']
            lim = (cross_gate or {}).get(side)
            if lim is not None and (px >= lim if side == 'B' else px <= lim):
                continue               # 交叉残留: 不入簿 (对侧 best 闸门), 身份保留
            a = agg.get((side, px))
            if a is None:
                lv0 = self.books[side].get(px)
                a = agg[(side, px)] = dict(prev_vol=lv0['vol'] if lv0 else 0,
                                           qty=0)
            lv = self.books[side].get(px)
            if lv is None:
                lv = {'vol': 0, 'queue': deque()}
                self.books[side][px] = lv
                insort(self._px[side], px)
                n = len(self.books['B']) + len(self.books['S'])
                if n > self.stats['max_levels']:
                    self.stats['max_levels'] = n
                b = self._best[side]
                if b is None or (px > b if side == 'B' else px < b):
                    self._best[side] = px
            lv['queue'].append(rec['id'])
            if len(lv['queue']) > self.stats['max_queue']:
                self.stats['max_queue'] = len(lv['queue'])
            lv['vol'] += rec['rem']
            a['qty'] += rec['rem']
            rec['booked'] = True
        for (side, px), a in agg.items():
            vol = self.books[side][px]['vol']      # 最终档量（物化循环后读, 无交叠）
            rows.append(dict(kind='level_materialization', ms=ms, side=side,
                             price=px, phase=ph, qty=a['qty'],
                             prev_vol=a['prev_vol'], new_vol=vol))
        self.events.extend(rows)
        return rows

    def eod(self):
        """EOD 结算：registry 残单按入簿/阶段分桶（不变量: 连续段+物化入簿残单 == 簿面总量）"""
        auc = cont = unbook = 0
        for rec in self._orders.values():
            if rec['booked']:
                cont += rec['rem']
            elif rec['phase'] in ('auction', 'match'):
                auc += rec['rem']
            else:
                unbook += rec['rem']
        self.eod_summary = {'auction_rem': auc, 'continuous_rem': cont,
                            'unbooked_rem': unbook}
        return self.eod_summary
