"""Probe: 600036@20251215 issue windows — dump engine vs anchor ladders at best edge,
trace source orders of engine-only best-edge levels."""
import sys, json
sys.path.insert(0, '/data/students/gaolei/stock/quant-platform-research/tools/lob_fact')
import measure_w3 as M
from qa import metrics as mt
import anchoring as A
from bisect import bisect_right
import config as C

code, day = '600036', '20251215'
evs, ga, gt = M.load_events(code, day)
snaps = M.load_snaps(code, day)
# replicate run_day driver up to a chosen ms
from engine import _KIND_PRIO
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
eng = A.Engine()
eng.ingest(evs[:cur])
fr = row_by_ms[first_a]
gate = {'B': int(round(fr['ask_p1'])), 'S': int(round(fr['bid_p1']))}
eng.materialize_leftovers(first_a, cross_gate=gate)

def ladder(eng, side):
    items = [(px, lv['vol']) for px, lv in eng.books[side].items()]
    items.sort(key=lambda t: -t[0] if side == 'B' else t[0])
    return items

# target window ms list: those issue windows from json
import glob
res = json.load(open(glob.glob('/data/students/gaolei/stock/lob_fact_calib/w3_measure_600036_20251215.json')[0]))
tgt = [w['ms'] for w in res['issue_windows'][:4]]
print('targets:', tgt)

prev = cur
for ms in tgt:
    idx = bisect_right(times, ms)
    if idx > prev:
        eng.ingest(evs[prev:idx])
    prev = idx
    row = row_by_ms[ms]
    print('\n=== window ms', ms)
    for side, evs_side, key in (('bid','B','bid'), ('ask','S','ask')):
        a = mt.snap_row_to_ladders(row, side)
        e = ladder(eng, evs_side)
        aps = {p for p,_ in a}
        print(f'--- {side}: anchor({len(a)}) vs engine top {len(e)}')
        for i in range(max(len(a), 10)):
            ap = a[i] if i < len(a) else None
            ep = e[i] if i < len(e) else None
            flag = ''
            if ap and ep:
                flag = '==' if ap[0]==ep[0] else (f'<{(ep[0]-ap[0])//100}ticks' if abs(ep[0]-ap[0])%100==0 else '?')
            print(f'  r{i:2d} anchor={ap} eng={ep} {flag}')
        # engine-only levels within anchor top-15 px span (incl edge just beyond)
        elo = [p for p,_ in e]
        in_anch = {p for p,_ in a}
        for i,(p,v) in enumerate(e[:12]):
            if p not in in_anch:
                q = eng.books[evs_side][p]['queue']
                recs = [(eng._orders[o]['ms'], eng._orders[o]['qty'], o) for o in list(q)[:4]]
                print(f'  EDGE eng-only r{i}: px={p} vol={v} orders={recs[:4]}...')
