#!/usr/bin/env python
"""W4 批算 — 纯逻辑层 (映射/日表/日门/月门; TDD) + W4c 批算编排 (同文件下半)

纯逻辑层 (无 I/O, 直接可测):
  orders_to_events / trades_to_events / cancels_to_events / parquet_events
    tick_fact parquet 行 → 归一化事件 (W4a parity_probe 14/14 逐事件字节对等语义;
    SH A→add D→cancel S→skip; SZ 0/1/U 全收 add; qty<=0 跳过; trades 按正 ref 拆
    fill (bid 先发); cancels side 0=B 1=S)
  day_tables(code, day, res)  anchoring.run_day 返回 → lob_events/lob_sweep_meta/
    lob_checkpoints 三表 (polars DataFrame)。观测行 (registry 段 prev==new==0 或
    phase != continuous) 不入 events 表 — 表行 = 连续段簿面绝对量流契约
    (fold 语义; 状态全深度, 行带 band 抑制 — W2 冻结)。seq = 1..n 按引擎事件序。
  day_gate(res)  日门 (batch 版, 无 m6): presence = n_present/n_anchor 圆整 5 位
    ≥ GATE_PRES (n_anchor=0 → vacuous 1.0) 且 M4 conservation PASS & orders==0 &
    counters_equal; n_present>n_anchor 数据错拒。与 W3 measure_w3.verdict 同口径
    (少 m6: 生产 band 行 m6='SKIP' 不参与日门)。
  month_gate(day_rows)  月聚合: 锚定日 (n_anchor>0) 交 measure_w3.m1a_gate (SZ/SH
    池化 + 逐日, 单点), vacuous 日独立计数; ok = 池化门 ∧ 逐日门 ∧ 全部日行门。

批算编排 (W4c, 本文件下半 + __main__): date-major 读 + ProcessPool(spawn) +
flock 单实例 + 900s stall 看门狗 + state.json 断点 (month→date) + MemAvailable
节流 + RSS 30s 审计 + worker 直写 (table, date) part 文件 (唯一 tmp + os.replace
原子) + conversion_manifest 输入守卫 + 月 _SUCCESS。
"""
import os

# ---- 线程上限: 必须先于任何 polars/arrow/pandas import 落位 (批算内存纪律;
# spawn worker 每次 import 本模块同样生效; setdefault 不覆盖用户显式设置) ----
os.environ.setdefault('ARROW_NUM_THREADS', '2')
os.environ.setdefault('OMP_NUM_THREADS', '2')
os.environ.setdefault('PYARROW_JEMALLOC', '0')
os.environ.setdefault('POLARS_MAX_THREADS', '4')

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from measure_w3 import GATE_PRES

import argparse, datetime as dt, fcntl, glob, hashlib, json, signal, time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
import multiprocessing

import config as C
import anchoring as A

# ---------- 表 → 事件映射 (W4a 对等语义) ----------

def _ex_of(code):
    """code → 'SZ'/'SH' (裸码前缀或后缀; tick_fact code 列存后缀形 '000155.SZ')"""
    return 'SZ' if code.lstrip('0123456789') == '.SZ' or code.startswith(('0', '3')) \
        else 'SH'


def orders_to_events(rows, ex):
    """orders 行 → (add/cancel, aux) 事件。SH: A→add D→cancel S→skip;
    SZ: 全行 add (0/1/U, 引擎按价裁决)。bs→side, exch_order_no→id,
    order_type→otype (add), qty<=0 跳过 (streams n_bad_row 语义, 坏行不产事件)。"""
    evs = []
    for r in rows:
        t = r['order_type']
        qty = r['volume']
        if t == 'S':                        # SH 杂项: 不进簿不进流
            continue
        if qty <= 0:
            continue                        # streams n_bad_row 语义 (哨兵/坏行)
        side = r['bs']
        if ex == 'SH':
            if t == 'A':
                evs.append(dict(kind='add', ms=int(r['time_ms']),
                                id=r['exch_order_no'], side=side,
                                price=int(r['price_x10000']), qty=int(qty),
                                otype='A'))
            elif t == 'D':
                evs.append(dict(kind='cancel', ms=int(r['time_ms']),
                                id=r['exch_order_no'], qty=int(qty), side=side))
        else:                               # SZ: 类型 0/1/U 全收 (引擎按价裁决)
            evs.append(dict(kind='add', ms=int(r['time_ms']),
                            id=r['exch_order_no'], side=side,
                            price=int(r['price_x10000']), qty=int(qty),
                            otype=t))
    return evs


def trades_to_events(rows):
    """trades 行 → fill 事件: 双侧正 ref 各发一条 (bid 先发); 双零行跳过
    (SH 先成交后报空引用类 — 正确忽略, 簿面由消息重建不受影响)"""
    evs = []
    for r in rows:
        bid, ask, qty = r['bid_seq'], r['ask_seq'], r['volume']
        if bid == 0 and ask == 0:
            continue
        px = int(r['price_x10000'])
        if bid > 0:
            evs.append(dict(kind='fill', ms=int(r['time_ms']), id=bid,
                            qty=int(qty), side='B', price=px))
        if ask > 0:
            evs.append(dict(kind='fill', ms=int(r['time_ms']), id=ask,
                            qty=int(qty), side='S', price=px))
    return evs


def cancels_to_events(rows):
    """cancels 行 (SZ-only) → cancel 事件: side 0=B 1=S, id=order_ref"""
    return [dict(kind='cancel', ms=int(r['time_ms']), id=r['order_ref'],
                 qty=int(r['volume']), side='B' if r['side'] == 0 else 'S')
            for r in rows]


def parquet_events(code, day, rows_o, rows_t, rows_c):
    """code-day 全事件: orders+trades (+SZ cancels) 块序拼接; day 仅信息参数
    (行已含 trade_date; 引擎 ingest 内部按 (ms, kind) 确定性排序兜底)"""
    ex = _ex_of(code)
    evs = orders_to_events(rows_o, ex) + trades_to_events(rows_t)
    if ex == 'SZ':
        evs += cancels_to_events(rows_c)
    return evs


# ---------- 日表构建 (schema 契约; 列序 = 行序契约) ----------

COL_EVENTS = [('code', pl.Utf8), ('trade_date', pl.Date), ('time_ms', pl.Int32),
              ('seq', pl.Int64), ('kind', pl.Utf8), ('phase', pl.Utf8),
              ('side', pl.Utf8), ('price_x10000', pl.Int64),
              ('prev_vol', pl.Int64), ('new_vol', pl.Int64),
              ('qty', pl.Int64), ('id', pl.Int64), ('otype', pl.Utf8)]
COL_SWEEP = [('code', pl.Utf8), ('trade_date', pl.Date), ('time_ms', pl.Int32),
             ('seq', pl.Int64), ('phase', pl.Utf8), ('side', pl.Utf8),
             ('price_x10000', pl.Int64), ('vol_before', pl.Int64),
             ('tail_order', pl.Int64), ('tail_resid', pl.Int64)]
COL_CKPT = [('code', pl.Utf8), ('trade_date', pl.Date), ('time_ms', pl.Int32),
            ('seq', pl.Int64), ('side', pl.Utf8), ('price_x10000', pl.Int64),
            ('vol', pl.Int64), ('n_queue', pl.Int32)]

_ROW_KEY = {'kind': 'kind', 'phase': 'phase', 'side': 'side',
            'price_x10000': 'price', 'qty': 'qty', 'id': 'id'}

_COL_TABLES = {'lob_events': COL_EVENTS, 'lob_sweep_meta': COL_SWEEP,
               'lob_checkpoints': COL_CKPT}


def day_tables(code, day, res):
    """anchoring.run_day 返回 → {lob_events, lob_sweep_meta, lob_checkpoints}。

    events: res['rows'] 中连续段行 (观测行 prev==new==0/非 continuous phase 不入表;
    含 level_materialization — 开盘簿面物化 = 表内绝对量锚); seq = 引擎事件序 1..n。
    sweep_meta: res['sweeps'] 原序 (ms 单调); ckpts: qa.checkpoints 逐分钟逐侧
    best-first (bid 先行) long 行, seq 全程连续。三表 schema/列序 = 上方 COL_*。
    """
    rows = [r for r in res['rows']
            if r.get('phase') == 'continuous'
            and not (r['prev_vol'] == 0 and r['new_vol'] == 0)]
    ev = _fill(rows, code, day, COL_EVENTS)
    sw = _fill(res['sweeps'], code, day, COL_SWEEP)
    ck_rows = []
    for ck in res['qa']['checkpoints']:
        for side in ('bid', 'ask'):
            for px, vol, nq in ck[side]:
                ck_rows.append(dict(ms=ck['ms'], side='B' if side == 'bid' else 'S',
                                    price=px, vol=vol, n_queue=nq))
    ck = _fill(ck_rows, code, day, COL_CKPT)
    return {'lob_events': ev, 'lob_sweep_meta': sw, 'lob_checkpoints': ck}


def _fill(rows, code, day, schema):
    """schema 列序 + 逐列取值 (seq 独立; 空表保持 schema dtype 非退化)"""
    out = {}
    for name, typ in schema:
        if name == 'time_ms':
            v = [int(r['ms']) for r in rows]
        elif name == 'trade_date':
            d = day if isinstance(day, dt.date) \
                else dt.datetime.strptime(day, '%Y%m%d').date()
            v = [d] * len(rows)
        elif name == 'seq':
            v = list(range(1, len(rows) + 1))
        elif name == 'code':
            v = [code] * len(rows)
        else:
            v = [r.get(_ROW_KEY.get(name, name)) for r in rows]
        out[name] = pl.Series(name, v, dtype=typ, strict=False)
    return pl.DataFrame(out, schema=[n for n, _ in schema])


# ---------- 日门 / 月门 (W3 门语义; batch 版日门无 m6) ----------

GATE_FLOOR = 0.90     # 日级 M1a 硬底线 (W4d 双阶修订依据): 真缺档/坏数据日崩
                      # presence << 0.90; δ 滞后带 0.90-0.97 日 = fast 名消息回报
                      # 滞后 (W4d 实测 20260803: 32/300 code-day presence
                      # 0.9132-0.9699, M4 逐单守恒/守卫全净, δ 归因带内) — W3 逐日
                      # 0.97 校准集 (10 日) 不含此带, 对全市场分布过严 → 池化
                      # 0.97 承担存现门, band 日 ok 但计数报告。

def day_gate(res):
    """日门 (生产 batch 版, W4d 双阶): M1a 存现 ≥ GATE_PRES 直接过;
    [GATE_FLOOR, GATE_PRES) = δ 滞后带 → ok 过 + band 标记 (m1a_delta_band 记
    notes, 非 reasons); < GATE_FLOOR → m1a_presence 硬拒。M4 conservation PASS &
    逐单 0 mismatch & counters_equal; n_present>n_anchor 数据错拒; n_anchor=0 →
    vacuous 1.0 (不豁免 m4)。presence 圆整 5 位 (W3 verdict 同口径)。
    返回 dict(ok, presence, vacuous, band, reasons, notes)"""
    d = res['qa']['day']
    n_p, n_a = d['n_present'], d['n_anchor']
    reasons, notes = [], []
    if n_p > n_a:
        reasons.append('presence_invalid')
    vacuous = n_a == 0
    presence = 1.0 if vacuous else round(n_p / n_a, 5)
    band = False
    if presence < GATE_PRES:
        if presence >= GATE_FLOOR:
            band = True
            notes.append('m1a_delta_band')
        else:
            reasons.append('m1a_presence')
    m4 = res['qa']['m4']
    if m4['conservation'] != 'PASS':
        reasons.append('m4_conservation')
    if m4['orders'] != 0:
        reasons.append('m4_orders')
    if not m4['counters_equal']:
        reasons.append('m4_counters')
    return dict(ok=not reasons, presence=presence, vacuous=vacuous,
                band=band, reasons=reasons, notes=notes)


def month_gate(day_rows):
    """月门聚合 (batch 版, W4d 修订): 逐日硬底线 (≥ GATE_FLOOR) 全过 且 日级
    gate.ok 全 PASS (δ 带日 ok 已过, band 独立计数 n_band 报告) 且 SZ/SH 池化
    (原始计数和, 非逐日均值) ≥ GATE_PRES — 池化承担 M1a 存现门 (W3 m1a_gate
    逐日 0.97 对真实 fast 名 δ 带过严, 见 GATE_FLOOR 注)。空锚日 (vacuous,
    含于 n_days) 独立 PASS 不进池 — 无锚日不给池贡献分母。空月 ok。"""
    anchored = [r for r in day_rows
                if not r['gate'].get('vacuous') and r['m1']['n_anchor'] > 0]
    vac = [r for r in day_rows if r['gate'].get('vacuous')]

    def pool(ss):
        n_p = sum(s['m1']['n_present'] for s in ss)
        n_a = sum(s['m1']['n_anchor'] for s in ss)
        return round(n_p / n_a, 5) if n_a else 1.0

    sz = [r for r in anchored if r['code'][0] in '03']
    sh = [r for r in anchored if r['code'][0] not in '03']
    p_sz, p_sh = pool(sz), pool(sh)
    # per_day = 硬底线 (真缺档日 presence 崩 → 拒); δ 带日 ≥ FLOOR 视为过
    per_day = [r['m1']['n_present'] / r['m1']['n_anchor']
               if r['m1']['n_anchor'] else 1.0 for r in day_rows]
    gate_ok = (p_sz >= GATE_PRES and p_sh >= GATE_PRES
               and all(p >= GATE_FLOOR for p in per_day))
    ok = gate_ok and all(r['gate'].get('ok', True) for r in day_rows)
    n_band = sum(1 for r in day_rows if r['gate'].get('band'))
    return dict(n_days=len(day_rows), n_anchored=len(anchored),
                n_vacuous=len(vac), n_band=n_band,
                gate=dict(sz=p_sz, sh=p_sh,
                          per_day=[p >= GATE_FLOOR for p in per_day],
                          ok=gate_ok),
                ok=ok)


# ---------- W4c 输入守卫 + 原子写盘 (纯函数; 编排层共用) ----------

_GUARD_KEY = (('orders', 'n_orders'), ('trades', 'n_trades'),
              ('snaps', 'n_snap'), ('cancels', 'n_cancels'))


def guard_check(code, day, reads, man):
    """输入守卫 (纯函数): 读行数 vs conversion/cancels manifest 全等。

    reads = 该 code-day 实读行数 dict {orders, trades, snaps[, cancels]}
    (只含读过的表; SH 不读 cancels 故无此键); man = 嵌套 dict
    code → day → manifest 行 (conversion: n_orders/n_trades/n_snap;
    cancels: n_cancels, SZ 增补表)。返回 (ok, mismatches):
      - 表行数不等 → ('表名', 读, 清单) 逐条列 (确定性序);
      - 无 manifest 行但读到行 → ('missing_manifest', None, None) 单条 (不静默);
      - 全零读且无 manifest 行 → 合法 (停牌/空日: manifest 只记有源的 code-day);
      - 零读且清单无该字段 (cancels 未记录) → 合法跳过; 清单有字段则严格对等。
    """
    row = (man.get(code) or {}).get(day) if man else None
    if row is None:
        if any(int(reads.get(t, 0)) > 0 for t, _ in _GUARD_KEY):
            return False, [('missing_manifest', None, None)]
        return True, []
    mm = []
    for t, k in _GUARD_KEY:
        if t not in reads:
            continue                       # 该表未读 (表存在性由 day_tables 保证)
        got = int(reads[t])
        want = row.get(k)
        if want is None:
            if got == 0:
                continue                   # 零读且清单未记录 → 无可比, 跳过
            want = None
        if got != want:
            mm.append((t, got, want))
    return not mm, mm


def write_part(table_dir, date_str, df, pid, seq):
    """date part 原子落盘 (worker 直写): 目标目录唯一 tmp (含 pid+seq) →
    write_parquet → fsync → os.replace → 目录 fsync。覆盖重写幂等 (断点重跑同
    date); 空 df 保持 schema 非退化。返回 (最终 path, n_rows)。"""
    import os
    import time
    table_dir = os.fspath(table_dir)
    os.makedirs(table_dir, exist_ok=True)
    tmp = os.path.join(table_dir, '.%s.parquet.tmp.%d.%d' % (date_str, pid, seq))
    final = os.path.join(table_dir, '%s.parquet' % date_str)
    try:
        df.write_parquet(tmp)
        fd = os.open(tmp, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, final)
        dfd = os.open(table_dir, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        if os.path.exists(tmp):            # 失败路径不留残 tmp (next 轮 stale 清理兜底)
            try:
                os.remove(tmp)
            except OSError:
                pass
    from pathlib import Path
    return Path(final), df.height


# ---------- W4d 内存定标修正: 零拷贝按 code 归并 + 流式写 (见 process_date) ----------

_PA_TYPE = {pl.Utf8: pa.large_string(), pl.Date: pa.date32(),
            pl.Int32: pa.int32(), pl.Int64: pa.int64()}


def _pa_schema(cols):
    """COL_* (name, polars dtype) → pyarrow schema (与 pl.frame.to_arrow() 同构:
    Utf8→large_string/Date→date32 — 空表 schema-only 文件与行表同 schema)"""
    return pa.schema([(name, _PA_TYPE[typ]) for name, typ in cols])


class _TableStream:
    """单 date part 流式写: 逐 code 批 append (单 writer, 确定性行组分界 → 重跑
    字节全等), 0 批日期以 schema-only 文件收尾 (schema 非退化)。原子收尾 =
    fsync + os.replace; 失败路径唯一 tmp 清残 (下轮 stale 清理兜底)。"""

    def __init__(self, table_dir, date_str, schema):
        self.dir = os.fspath(table_dir)
        os.makedirs(self.dir, exist_ok=True)
        self.schema = schema
        self.tmp = os.path.join(self.dir, '.%s.parquet.tmp.%d.s' %
                                (date_str, os.getpid()))
        self.final = os.path.join(self.dir, '%s.parquet' % date_str)
        self.f = open(self.tmp, 'wb')
        self.w = pq.ParquetWriter(self.f, schema, compression='zstd')
        self.n = 0

    def append(self, df):
        if df.height:
            self.w.write_table(df.to_arrow())
            self.n += df.height

    def finish(self):
        """close + fsync + 原子替换; 返回 (final Path, rows)"""
        self.w.close()
        self.f.flush()
        os.fsync(self.f.fileno())
        self.f.close()
        os.replace(self.tmp, self.final)
        dfd = os.open(self.dir, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
        from pathlib import Path
        return Path(self.final), self.n

    def abort(self):
        try:
            self.w.close()
            self.f.close()
        except Exception:
            pass
        try:
            if os.path.exists(self.tmp):
                os.remove(self.tmp)
        except OSError:
            pass


def _code_bounds(df):
    """sorted-by-code 帧 → {code: (start, len)} (0 拷贝; 组界由 group_by
    maintain_order 得, 输入有序 → 序 = code 升序 = manifest 序)"""
    if df is None or df.height == 0:
        return {}
    g = df.group_by('code', maintain_order=True).agg(pl.len().alias('__n'))
    bounds, pos = {}, 0
    for code, n in g.iter_rows():
        bounds[code] = (pos, n)
        pos += n
    return bounds


# ---------- W4c 编排层 (date-major worker + 看门狗 + 断点; 见模块 docstring) ----------

AUDIT_S = 30            # RSS/内存审计采样间隔
STALL_S = 2400          # 无完成容忍秒数 (W4d smoke 实测: 20260803 单 date 全量
                        # ~15-20 min > 镜像 extract_sz_cancels 的 900s → 900s 会
                        # 误杀真长 date 成 kill 循环; 2400s = > 单 date 最长上界)
LOW_WATER_KB = 8_000_000    # MemAvailable 低水位 (~7.6GB): 低于不派发新 date

_TICK_COLS = {
    'orders': ['code', 'time_ms', 'order_type', 'bs', 'price_x10000',
               'volume', 'exch_order_no'],
    'trades': ['code', 'time_ms', 'price_x10000', 'volume',
               'ask_seq', 'bid_seq'],
    'cancels': ['code', 'time_ms', 'side', 'order_ref', 'volume'],
    'snapshots': ['code', 'time_ms'] +
                 [f'{s}_{k}{i}' for s in ('bid', 'ask')
                  for k in ('p', 'v') for i in range(1, 11)],
}
_IN_TABLES = ('orders', 'trades', 'snapshots', 'cancels')
_GUARD_TBL = {'orders': 'orders', 'trades': 'trades',
              'snapshots': 'snaps', 'cancels': 'cancels'}


# ---- worker 全局 (initializer 置位; spawn 每进程一份 manifest 索引) ----

_G = {}
_LOOKUP = {}            # (code, day) -> 合并 manifest 行 {n_orders,...}
_DCODES = {}            # day -> 升序 code 列表 (conversion manifest 全量)
_TOTALS = {}            # (day, tbl_key) -> manifest 表总数


def _init_worker(cfg):
    global _G, _LOOKUP, _DCODES, _TOTALS
    _G = cfg
    _LOOKUP, _DCODES, _TOTALS = {}, {}, {}
    for path, keys in ((cfg['conv_manifest'],
                        (('orders', 'n_orders'), ('trades', 'n_trades'),
                         ('snaps', 'n_snap'))),
                       (cfg.get('cancels_manifest'),
                        (('cancels', 'n_cancels'),))):
        if not path or not os.path.exists(path):
            continue
        for r in pl.read_parquet(path).to_dicts():
            day = r['trade_date'].strftime('%Y%m%d')
            code = r['code']
            lk = _LOOKUP.setdefault((code, day), {})
            for tkey, k in keys:
                lk[k] = int(r[k])
                if tkey == 'snaps':      # conversion 行: 登记 code-day 全集
                    _DCODES.setdefault(day, set()).add(code)
                _TOTALS[(day, tkey)] = (_TOTALS.get((day, tkey), 0)
                                        + int(r[k]))
    for d in _DCODES:
        _DCODES[d] = sorted(_DCODES[d])


def _read_date(tbl, day):
    """date-major 月文件切片 (RG 剪枝按 trade_date; 列裁剪; 该表该日全 code)"""
    y, m, d = int(day[:4]), int(day[4:6]), int(day[6:8])
    parts = sorted(glob.glob(os.path.join(
        _G['tick_root'], tbl, f'year={y}', f'month={m:02d}', '*.parquet')))
    if not parts:
        return None
    return (pl.scan_parquet(parts)
            .filter(pl.col('trade_date') == dt.date(y, m, d))
            .select(_TICK_COLS[tbl]).collect())


def _sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def process_date(day):
    """单 date: 4 表 date-major 切读 → 逐表 sort('code') 零拷贝组界 (_code_bounds;
    W4d 定标弃 partition_by — 实测 +4.6GB RSS 纯拷贝) → manifest 序逐 code 守卫 +
    run_day(band_ckpts) → 三表 _TableStream 流式 append (单 writer 确定性行组;
    不累积/不 concat — 日终大帧峰值消除) → 原子收尾 → 载荷。
    返回 dict(day, ok, errors, n_codes, recs, tables{rows/bytes/sha256},
    engine_ms...); 任何异常 → 流 abort + future 抛 (该 date 不 done 下轮重试;
    表文件整 date 原子覆盖; 收尾前无 final 文件)。"""
    t0 = time.time()
    errs, recs = [], []
    codes = _DCODES.get(day, [])
    if not codes:                       # 无 manifest code 日 = 空日: 零 I/O 早退
        return dict(day=day, ok=False, errors=['manifest 无该日 code'],
                    n_codes=0, recs=[], tables={}, engine_ms=0.0,
                    read_s=round(time.time() - t0, 1))
    dfs = {t: _read_date(t, day) for t in _IN_TABLES}
    srt, lens = {}, {}
    for t in _IN_TABLES:
        d = dfs[t]
        dfs[t] = None                   # 释放未排序原帧 (sort 输出为工作副本)
        if d is None or d.height == 0:
            srt[t], lens[t] = None, 0
        else:
            srt[t] = d.sort('code')
            lens[t] = srt[t].height
    bnd = {t: _code_bounds(srt[t]) for t in _IN_TABLES}
    consumed = dict.fromkeys(_IN_TABLES, 0)
    read_tot = dict.fromkeys(('orders', 'trades', 'snaps', 'cancels'), 0)
    streams = {}
    engine_ms = 0.0
    try:
        for code in codes:
            ex = _ex_of(code)
            reads, rows = {}, {}
            for t in ('orders', 'trades', 'snapshots'):
                b = bnd[t].get(code)
                if b is None:
                    reads[_GUARD_TBL[t]] = 0
                    rows[t] = []
                    continue
                start, nrow = b
                reads[_GUARD_TBL[t]] = nrow
                consumed[t] += nrow
                read_tot[_GUARD_TBL[t]] += nrow
                rows[t] = srt[t].slice(start, nrow).to_dicts()   # 视图切片 0 拷贝
            if ex == 'SZ':
                b = bnd['cancels'].get(code)
                if b is None:
                    reads['cancels'] = 0
                    rows['cancels'] = []
                else:
                    start, nrow = b
                    reads['cancels'] = nrow
                    consumed['cancels'] += nrow
                    read_tot['cancels'] += nrow
                    rows['cancels'] = srt['cancels'].slice(start, nrow).to_dicts()
            row = _LOOKUP.get((code, day))
            g_ok, mm = guard_check(code, day, reads,
                                   {code: {day: row}} if row else {})
            evs = orders_to_events(rows['orders'], ex) + trades_to_events(rows['trades'])
            if ex == 'SZ':
                evs += cancels_to_events(rows['cancels'])
            snaps = [dict(r) for r in rows['snapshots']]
            te = time.time()
            res = A.run_day(evs, snaps, band_ckpts=True)
            engine_ms += time.time() - te
            gate = day_gate(res)
            tabs = day_tables(code, day, res)
            for key, frame in tabs.items():
                st = streams.get(key)
                if st is None:
                    st = streams[key] = _TableStream(
                        os.path.join(_G['lob_root'], key, f'year={day[:4]}',
                                     f'month={day[4:6]}'),
                        day, _pa_schema(_COL_TABLES[key]))
                st.append(frame)
            m4 = res['qa']['m4']
            d1 = res['qa']['day']
            recs.append(dict(
                code=code, day=day, ok=g_ok and gate['ok'],
                gate=dict(presence=gate['presence'], vacuous=gate['vacuous'],
                          band=gate['band'], ok=gate['ok'],
                          reasons=gate['reasons'], notes=gate['notes']),
                guard=dict(ok=g_ok, mismatches=mm),
                m1=dict(n_anchor=d1['n_anchor'], n_present=d1['n_present'],
                        missing_vol=d1['missing_vol'],
                        unattributed_vol=d1['unattributed_vol'],
                        delta_attr=round(d1['delta_attribution'], 4)),
                m4=dict(conservation=m4['conservation'], orders=m4['orders'],
                        counters_equal=m4['counters_equal']),
                n_events=len(evs), n_snaps=len(snaps),
                rows=tabs['lob_events'].height,
                sweeps=tabs['lob_sweep_meta'].height,
                engine_ms=round((time.time() - te) * 1000, 1)))
            del res, evs, snaps
        # date 级读总数守卫 (orders/trades/snaps 与 conversion manifest 恒等; cancels
        # 清单只记有 C 行的 code-day, 总数比对会误报 → 只逐 code 守卫)
        for tkey in ('orders', 'trades', 'snaps'):
            want = _TOTALS.get((day, tkey))
            if want is not None and read_tot[tkey] != want:
                errs.append(f'{tkey}: date 实读 {read_tot[tkey]} != manifest {want}')
        for t in _IN_TABLES:            # 帧内未消费行 = 无 manifest 归属 (漂移哨兵)
            if consumed[t] < lens[t]:
                errs.append(f'{t}: 帧内 {lens[t] - consumed[t]} 行无 manifest '
                            f'code 归属 (上游漂移, 不静默)')
        files = {}
        for key, st in streams.items():
            path, n = st.finish()
            files[key] = dict(rows=n, bytes=os.path.getsize(path),
                              sha256=_sha256(path))
        ok = not errs and all(r['ok'] for r in recs)
        return dict(day=day, ok=ok, errors=errs, n_codes=len(codes), recs=recs,
                    tables=files, engine_ms=round(engine_ms, 1),
                    n_fail=sum(1 for r in recs if not r['ok']),
                    read_s=round(time.time() - t0, 1))
    except BaseException:
        for st in streams.values():
            st.abort()
        raise


# ---- 内存/审计工具 ----

def _mem_avail_kb():
    try:
        with open('/proc/meminfo') as f:
            for ln in f:
                if ln.startswith('MemAvailable:'):
                    return int(ln.split()[1])
    except OSError:
        return None
    return None


def _proc_rss_kb(pid):
    try:
        with open(f'/proc/{pid}/status') as f:
            for ln in f:
                if ln.startswith('VmRSS:'):
                    return int(ln.split()[1])
    except OSError:
        return None
    return None


def _month_codes():
    """月 code-day 全集索引 (conversion manifest; 兼 date 计划源)"""
    mf = os.path.join(C.TICK_FACT_ROOT, '_manifest', 'conversion_manifest.parquet')
    if not os.path.exists(mf):
        raise SystemExit(f'conversion_manifest 缺失: {mf}')
    days = {}
    for r in pl.read_parquet(mf).to_dicts():
        days.setdefault(r['trade_date'].strftime('%Y%m%d'), 0)
        days[r['trade_date'].strftime('%Y%m%d')] += 1
    return days


# ---- main (flock 单实例 + 断点 + 看门狗 + 节流 + 审计 + 月收尾) ----

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--month', required=True, help='YYYYMM 批算目标月')
    ap.add_argument('--workers', type=int, default=2,
                    help='并行 worker (真数据实测: 单 worker 峰 ~10-13GB: '
                         'date 切片 + partition 驻留 + 逐 code 瞬态; '
                         '2 worker 常态 ~22-26GB 需审计定标, W4d 后定 W5 默认)')
    ap.add_argument('--force', action='store_true',
                    help='无视 done/SUCCESS 全月重跑 (字节级重跑比对用)')
    ap.add_argument('--only-day', default=None, help='YYYYMMDD 单日 (调试)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--nice', type=int, default=15)
    args = ap.parse_args(argv)
    month = args.month
    os.nice(args.nice)

    # ---- 单实例锁 ----
    bdir = os.path.join(C.LOB_FACT_ROOT, '_batch')
    os.makedirs(bdir, exist_ok=True)
    lock_f = open(os.path.join(bdir, '.lock'), 'w')
    try:
        fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(f'另一批算实例正在运行 (锁占用) → 退出', flush=True)
        return
    # 清理孤儿 tmp (异常退出残留)
    for root, _, fnames in os.walk(C.LOB_FACT_ROOT):
        for f in fnames:
            if '.tmp.' in f:
                p = os.path.join(root, f)
                print(f'清理 stale tmp: {p}', flush=True)
                os.unlink(p)

    tick_mf = os.path.join(C.TICK_FACT_ROOT, '_manifest', 'conversion_manifest.parquet')
    canc_mf = os.path.join(C.TICK_FACT_ROOT, '_manifest', 'cancels_manifest.parquet')
    cfg = dict(tick_root=C.TICK_FACT_ROOT, lob_root=C.LOB_FACT_ROOT,
               conv_manifest=tick_mf, cancels_manifest=canc_mf)
    if not os.path.exists(canc_mf):
        raise SystemExit(f'cancels_manifest 缺失: {canc_mf} (W0 未完成?)')

    # 源月目录存在性硬查 (静默缺表 = 假数据)
    y, m = int(month[:4]), int(month[4:6])
    for t in _IN_TABLES:
        d = os.path.join(C.TICK_FACT_ROOT, t, f'year={y}', f'month={m:02d}')
        if not glob.glob(os.path.join(d, '*.parquet')):
            raise SystemExit(f'源表缺失: {t} @ {month} ({d} 无 parquet)')

    # ---- date 计划: conversion manifest 该月日期 (源全量事实) ----
    _days = _month_codes()
    plan = sorted(d for d in _days if d[:6] == month)
    if args.only_day:
        plan = [d for d in plan if d == args.only_day]
    if not plan:
        print(f'{month}: manifest 无该月日期'); return
    # 断点 (state.json) + SUCCESS 标记
    state_p = os.path.join(bdir, 'state.json')
    state = {}
    if os.path.exists(state_p):
        with open(state_p) as f:
            state = json.load(f)
    done = set((state.get('months') or {}).get(month, {}).get('done', []))
    succ_p = os.path.join(bdir, f'SUCCESS_{month}')
    if not args.force and os.path.exists(succ_p):
        print(f'{month}: 已有 SUCCESS 标记 (--force 重跑) → 退出'); return
    todo = [d for d in plan if d not in done] if not args.force else list(plan)
    if args.dry_run:
        print(f'dry-run {month}: plan={len(plan)} done={len(done)} '
              f'todo={len(todo)} → 退出'); return
    finalize_only = False
    if not todo:
        print(f'{month}: plan 全 done={len(plan)} 但无 SUCCESS '
              f'→ 仅收尾 (跨 run 月门 + 标记), 不重算')
        finalize_only = True

    run_id = time.strftime('%Y%m%d_%H%M%S') + f'_{os.getpid()}'
    rdir = os.path.join(bdir, 'runs', run_id)
    os.makedirs(rdir, exist_ok=True)
    rows_f = open(os.path.join(rdir, 'day_rows.jsonl'), 'w')
    audit_f = open(os.path.join(rdir, 'rss_audit.csv'), 'w')
    audit_f.write('t,pid,tag,rss_kb,mem_avail_kb\n')
    audit_f.flush()
    err_rows = []          # (day, kind, detail)
    t0 = time.time()
    n_done_run = 0
    if finalize_only:
        todo = []

    def audit(when):
        for pid in list(getattr(ex, '_processes', {})):
            rss = _proc_rss_kb(pid)
            if rss is not None:
                audit_f.write(f'{when},{pid},worker,{rss},{_mem_avail_kb()}\n')
        audit_f.write(f'{when},{os.getpid()},parent,'
                      f'{_proc_rss_kb(os.getpid())},{_mem_avail_kb()}\n')
        audit_f.flush()

    ctx = multiprocessing.get_context('spawn')
    MAX_INFLIGHT = max(args.workers * 3, 12)
    pending = list(reversed(todo))     # pop() 取尾部 → 日期正序
    stall_streak = 0
    last_act = time.time()
    last_aud = 0.0
    ex = ProcessPoolExecutor(max_workers=args.workers, mp_context=ctx,
                             initializer=_init_worker, initargs=(cfg,))
    futs = {}
    try:
        while pending or futs:
            # 节流 + 补单
            while len(futs) < MAX_INFLIGHT and pending:
                avail = _mem_avail_kb()
                if avail is not None and avail < LOW_WATER_KB:
                    break                    # 低水位: 等审计 tick 再派
                d = pending.pop()
                futs[ex.submit(process_date, d)] = d
            now = time.time()
            if now - last_aud >= AUDIT_S:
                audit(now)
                last_aud = now
            if not futs:
                time.sleep(AUDIT_S)
                continue
            done_f, _ = wait(futs, timeout=AUDIT_S, return_when=FIRST_COMPLETED)
            if not done_f:
                if time.time() - last_act > STALL_S:
                    stall_streak += 1
                    print(f'STALL {STALL_S}s 无完成 → SIGKILL worker '
                          f'(in-flight={len(futs)} pending={len(pending)})',
                          flush=True)
                    for pid in list(getattr(ex, '_processes', {})):
                        try:
                            os.kill(pid, signal.SIGKILL)
                            os.waitpid(pid, 0)
                        except (OSError, ChildProcessError):
                            pass
                    if stall_streak >= 3:
                        print(f'连续 {stall_streak} STALL → 放弃剩余 '
                              f'{len(pending)+len(futs)} date', flush=True)
                        for d in futs.values():
                            err_rows.append((d, 'stall_abandon',
                                             f'{stall_streak} stalls'))
                        pending.clear()
                        futs.clear()
                        break
                    for d in futs.values():
                        pending.append(d)
                    futs = {}
                    ex.shutdown(wait=False, cancel_futures=True)
                    ex = ProcessPoolExecutor(
                        max_workers=args.workers, mp_context=ctx,
                        initializer=_init_worker, initargs=(cfg,))
                    last_act = time.time()
                    print(f'  worker 已清理, 剩余 {len(pending)} date, 重建 executor',
                          flush=True)
                continue
            last_act = time.time()
            for fu in done_f:
                d = futs.pop(fu)
                try:
                    p = fu.result()
                except Exception as e:
                    err_rows.append((d, 'date_exc', repr(e)[:300]))
                    print(f'{d}: 异常 {e!r}', flush=True)
                    continue
                rows_f.write(json.dumps(p, ensure_ascii=False) + '\n')
                rows_f.flush()
                n_done_run += 1
                if p['ok']:
                    done.add(d)
                    print(f'{d}: OK codes={p["n_codes"]} rows='
                          f'{sum(v["rows"] for v in p["tables"].values())} '
                          f'eng={p["engine_ms"]}s fail={p["n_fail"]} '
                          f'[{time.time()-t0:.0f}s]', flush=True)
                else:
                    err_rows.append((d, 'date_gate', '; '.join(p['errors'])
                                     or f'{p["n_fail"]} code-day fail'))
                    print(f'{d}: GATE_FAIL codes={p["n_codes"]} '
                          f'fail={p["n_fail"]} errs={p["errors"]}', flush=True)
                audit(time.time())      # 每 date 完结算一笔峰值
    finally:
        rows_f.close()
        audit_f.close()
        ex.shutdown(wait=False, cancel_futures=True)
    # state 落盘 (断点: 只记 ok date)
    st_m = state.setdefault('months', {}).setdefault(month, {})
    st_m['done'] = sorted(done)
    st_m['plan_n'] = len(plan)
    st_p_tmp = state_p + '.tmp'
    with open(st_p_tmp, 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(st_p_tmp, state_p)

    # ---- run summary (含每 (table,date) sha256 → 字节级重跑比对) ----
    parity = {'compared': False, 'mismatch_dates': [], 'ok': None}
    prev = None
    for r in sorted(os.listdir(os.path.join(bdir, 'runs'))):
        if r == run_id:
            continue
        sp = os.path.join(bdir, 'runs', r, 'summary.json')
        if os.path.exists(sp):
            try:
                with open(sp) as f:
                    s = json.load(f)
                if s.get('month') == month:
                    prev = s
            except Exception:
                pass
    tbs = {}
    with open(os.path.join(rdir, 'day_rows.jsonl')) as f:
        for ln in f:
            p = json.loads(ln)
            tbs[p['day']] = p.get('tables', {})
    if prev is not None:
        parity['compared'] = True
        mism = []
        for d, tbls in tbs.items():
            old = (prev.get('tables') or {}).get(d)
            if not old:
                continue
            for k, v in tbls.items():
                ov = old.get(k)
                if ov is None or ov.get('sha256') != v['sha256']:
                    mism.append((d, k, (ov or {}).get('sha256'), v['sha256']))
        parity['mismatch_dates'] = sorted({x[0] for x in mism})
        parity['ok'] = not mism
    summary = dict(month=month, run_id=run_id, plan_n=len(plan),
                   n_done_run=n_done_run, n_errors=len(err_rows),
                   errors=err_rows, tables=tbs, parity=parity,
                   elapsed_s=round(time.time() - t0, 1))
    with open(os.path.join(rdir, 'summary.json'), 'w') as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)

    # ---- 月收尾: 跨 run 日行聚合 → 月门 → SUCCESS ----
    rows_all = {}
    for r in sorted(os.listdir(os.path.join(bdir, 'runs'))):
        rp = os.path.join(bdir, 'runs', r, 'day_rows.jsonl')
        if not os.path.exists(rp):
            continue
        with open(rp) as f:
            for ln in f:
                p = json.loads(ln)
                if p.get('day', '')[:6] != month:
                    continue
                for rec in p.get('recs', []):
                    if rec.get('ok'):
                        rows_all[(rec['code'], rec['day'])] = rec
    recs = [rows_all[k] for k in sorted(rows_all)]
    mg = month_gate(recs) if recs else dict(n_days=0, n_anchored=0,
                                            n_vacuous=0, gate={}, ok=True)
    mgf = os.path.join(bdir, f'month_gate_{month}.json')
    with open(mgf, 'w') as f:
        json.dump(dict(month=month, n_code_day=len(recs), month_gate=mg,
                       n_dates_done=len(done), plan_n=len(plan),
                       generated_at=time.strftime('%Y-%m-%d %H:%M:%S')),
                  f, ensure_ascii=False, indent=1)
    complete = len(done) == len(plan) and not err_rows and mg['ok']
    if complete:
        with open(succ_p, 'w') as f:
            json.dump(dict(month=month, n_code_day=len(recs), gate=mg['gate'],
                           parity_ok=parity['ok']), f, ensure_ascii=False)
        print(f'\n{month}: SUCCESS — code-day {len(recs)}, '
              f'月门 SZ={mg["gate"].get("sz")} SH={mg["gate"].get("sh")} '
              f'parity_ok={parity["ok"]} elapsed={time.time()-t0:.0f}s', flush=True)
    else:
        print(f'\n{month}: 未完成 — done {len(done)}/{len(plan)}, '
              f'errors {len(err_rows)}, 月门 ok={mg["ok"]} '
              f'(重跑续做; SUCCESS 只在全净完成后落)', flush=True)
    print(f'summary -> {rdir}', flush=True)


if __name__ == '__main__':
    main()
