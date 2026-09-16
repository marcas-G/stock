"""Materialized leftover ask orders: px-level table of vol/count/add-ms-range,
tagged real-carry (px in first-anchor real top-10) vs ghost (engine-only)."""
import sys
sys.path.insert(0, '/data/students/gaolei/stock/platform/tools/lob_fact')
import measure_w3 as M
from qa import metrics as mt
import config as C
from bisect import bisect_right
from collections import defaultdict

for code, day in (('000021', '20260706'), ('000021', '20260803'),
                  ('000155', '20260803'), ('600036', '20251215')):
    print(f'\n########## {code}@{day} ##########')
    evs, ga, gt = M.load_events(code, day)
    snaps = M.load_snaps(code, day)
    from engine import _KIND_PRIO, Engine
    evs.sort(key=lambda e: (e['ms'], _KIND_PRIO.get(e['kind'], 9)))
    times = [e['ms'] for e in evs]
    anchors = []
    for row in snaps:
        ms = int(row['time_ms'])
        if ms >= C.OPEN and (not anchors or anchors[-1][0] != ms):
            anchors.append((ms, row))
    anchors.sort()
    first_a = anchors[0][0]
    fr = anchors[0][1]
    cur = bisect_right(times, first_a)
    eng = Engine()
    eng.ingest(evs[:cur])
    gate = {'B': int(round(fr['ask_p1'])), 'S': int(round(fr['bid_p1']))}
    eng.materialize_leftovers(first_a, cross_gate=gate)
    real = {}
    for side, key in (('B', 'bid'), ('S', 'ask')):
        real[side] = {p for p, _ in mt.snap_row_to_ladders(fr, key)}
    for side, name in (('S', 'ask'), ('B', 'bid')):
        agg = defaultdict(lambda: [0, 0, None, None])  # px -> [vol, n, min_ms, max_ms]
        for rec in eng._orders.values():
            if (rec['booked'] and rec['phase'] == 'auction' and rec['side'] == side):
                a = agg[rec['price']]
                a[0] += rec['rem']; a[1] += 1
                a[2] = rec['ms'] if a[2] is None else min(a[2], rec['ms'])
                a[3] = max(a[3], rec["ms"]) if a[3] is not None else rec["ms"]
        rows = sorted(agg.items(), key=lambda t: (-t[0] if side == 'B' else t[0]))
        # show levels within the top-12 window of interest
        vis = rows[:16]
        print(f'--- {name} leftovers (top {len(vis)} px levels): px vol n add-ms-range real?')
        for px, (vol, n, mn, mx) in vis:
            tag = 'REAL' if px in real[side] else 'ghost'
            mns = (mn - 33300000) / 1000.0
            mxs = (mx - 33300000) / 1000.0
            print(f'  {px/10000:9.2f} {vol:9d} {n:4d} {mns:8.1f}-{mxs:8.1f}s  {tag}')
