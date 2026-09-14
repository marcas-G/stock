"""10-day presence (set-cov) profile vs rank-aligned, day + bucket summary."""
import sys
sys.path.insert(0, '/data/students/gaolei/stock/projects/quant-platform-research/tools/lob_fact')
import measure_w3 as M
from qa import metrics as mt
import config as C
from bisect import bisect_right
from engine import _KIND_PRIO, Engine
from collections import defaultdict

DAYS = [
    ('000155','20260803'), ('000155','20250822'), ('000155','20260210'),
    ('000155','20250915'), ('000021','20260803'), ('000021','20260706'),
    ('600036','20251215'), ('600036','20260513'),
    ('600184','20260803'), ('600184','20250915'),
]
def one(code, day):
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
    win = defaultdict(lambda: [0,0,0,0])   # bkt: [n_match,n_anchor,n_pres,n_anchor]
    for ms, row in anchors:
        idx = bisect_right(times, ms)
        if idx > cur:
            eng.ingest(evs[cur:idx])
        cur = idx
        if ms > C.M1_END: continue
        bkt = (ms - C.OPEN) // 1800000
        for side, key in (('bid','B'), ('ask','S')):
            a = mt.snap_row_to_ladders(row, side)
            epx = {px for px, _ in eng.books[key].items()}
            e = [(px, lv['vol']) for px, lv in eng.books[key].items()]
            e.sort(key=lambda t: -t[0] if key=='B' else t[0])
            m = mt.ladder_match(a, e)
            p = win[bkt]
            p[0] += m['n_match']; p[1] += m['n_anchor']
            p[2] += sum(1 for px, _ in a if px in epx); p[3] += len(a)
    tot = [sum(p[i] for p in win.values()) for i in range(4)]
    line = []
    for k in sorted(win)[:1] + [max(sorted(win))]:
        v = win[k]
        line.append(f'b{k}:r{v[0]/v[1]:.3f}/p{v[2]/v[3]:.3f}')
    print(f'{code}@{day} RANK {tot[0]/tot[1]:.4f} PRES {tot[2]/tot[3]:.4f} | ' + ' '.join(line), flush=True)

for cd in DAYS:
    one(*cd)
