import sys
sys.path.insert(0, '/data/students/gaolei/stock/quant-platform-research/tools/lob_fact')
import measure_w3 as M
from qa import metrics as mt
import config as C
from bisect import bisect_right
from engine import _KIND_PRIO, Engine
from collections import Counter

code, day = '000021', '20260706'
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

rankfail = Counter()      # which anchor ranks mismatch
samples = []              # first 3 weak window snapshots
cls_count = Counter()
for ms, row in anchors:
    idx = bisect_right(times, ms)
    if idx > cur:
        eng.ingest(evs[cur:idx])
    cur = idx
    if ms > C.M1_END:
        break
    for side, key in (('bid','B'), ('ask','S')):
        a = mt.snap_row_to_ladders(row, side)
        e = [(px, lv['vol']) for px, lv in eng.books[key].items()]
        e.sort(key=lambda t: -t[0] if key=='B' else t[0])
        for i, (ap, av) in enumerate(a):
            if i >= len(e):
                rankfail[i] += 1; cls_count['missing'] += 1; continue
            ep, ev = e[i]
            if ep == ap: continue
            rankfail[i] += 1
            cls_count['adjacent' if abs(ep-ap)==100 else 'deep'] += 1
        if len(samples) < 3:
            m = mt.ladder_match(a, e)
            if m['n_match'] < 8:
                samples.append((ms, side, a, e[:12]))
print('rank mismatch histogram:', dict(sorted(rankfail.items())))
print('classes:', dict(cls_count))
for ms, side, a, e in samples:
    print(f'\n-- {ms} ({ms//3600000}:{(ms//60000)%60:02d}:{(ms//1000)%60:02d}) {side}')
    print('  anchor:', a[:10])
    print('  engine:', e[:10])
