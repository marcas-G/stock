"""Decisive δ test: compare per-window match with engine state at anchor ms vs
anchor+500ms / +1000ms (lagged). If lagged recovers -> δ-boundary intrinsic."""
import sys
sys.path.insert(0, '/data/students/gaolei/stock/quant-platform-research/tools/lob_fact')
import measure_w3 as M
from qa import metrics as mt
import config as C
from bisect import bisect_right
from engine import _KIND_PRIO, Engine
from collections import defaultdict

for code, day in (('000021','20260706'), ('000155','20260803'), ('600036','20251215')):
    evs, ga, gt = M.load_events(code, day)
    snaps = M.load_snaps(code, day)
    evs.sort(key=lambda e: (e['ms'], _KIND_PRIO.get(e['kind'], 9)))
    times = [e['ms'] for e in evs]
    anchors = []
    for row in snaps:
        ms = int(row['time_ms'])
        if ms >= C.OPEN and (not anchors or anchors[-1][0] != ms):
            anchors.append((ms, row))
    anchors.sort()
    row_by_ms = dict(anchors)
    first_a = anchors[0][0]
    cur = bisect_right(times, first_a)
    eng = Engine()
    eng.ingest(evs[:cur])
    fr = row_by_ms[first_a]
    gate = {'B': int(round(fr['ask_p1'])), 'S': int(round(fr['bid_p1']))}
    eng.materialize_leftovers(first_a, cross_gate=gate)
    res = defaultdict(lambda: [0,0])
    nwin = 0
    for ms, row in anchors:
        idx = bisect_right(times, ms)
        if idx > cur:
            eng.ingest(evs[cur:idx])
        cur = idx
        if ms > C.M1_END or ms < first_a:
            continue
        # strict: current engine state
        for side, key in (('bid','B'), ('ask','S')):
            a = mt.snap_row_to_ladders(row, side)
            e = [(px, lv['vol']) for px, lv in eng.books[key].items()]
            e.sort(key=lambda t: -t[0] if key=='B' else t[0])
            m = mt.ladder_match(a, e)
            res['strict'][0] += m['n_match']; res['strict'][1] += m['n_anchor']
        # lagged: ingest through ms+lag, compare SAME anchor
        lag = 500
        idx2 = bisect_right(times, ms + lag)
        if idx2 > cur:
            eng.ingest(evs[cur:idx2])
        cur = idx2
        for side, key in (('bid','B'), ('ask','S')):
            a = mt.snap_row_to_ladders(row, side)
            e = [(px, lv['vol']) for px, lv in eng.books[key].items()]
            e.sort(key=lambda t: -t[0] if key=='B' else t[0])
            m = mt.ladder_match(a, e)
            res['lag500'][0] += m['n_match']; res['lag500'][1] += m['n_anchor']
        nwin += 1
    print(f'{code}@{day}: windows={nwin}')
    for k in ('strict', 'lag500'):
        v = res[k]
        print(f'   {k:7s}: {v[0]}/{v[1]} = {v[0]/v[1]:.5f}')
