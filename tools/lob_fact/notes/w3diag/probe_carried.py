"""Per-order: leftover auction orders carried-vs-voided by real exchange.
For each registry leftover order: add_ms, px, side, qty, rem after auction fills.
Tag carried = order's px present in REAL first-anchor book (that side). Print add_ms
distributions + thresholds. Runs 600036@20251215 (SH) and 000021@20260706 (SZ)."""
import sys
sys.path.insert(0, '/data/students/gaolei/stock/quant-platform-research/tools/lob_fact')
import measure_w3 as M
from qa import metrics as mt
import config as C
from bisect import bisect_right
from collections import Counter

for code, day in (('600036', '20251215'), ('000021', '20260706')):
    print(f'\n########## {code}@{day} ##########')
    evs, ga, gt = M.load_events(code, day)
    snaps = M.load_snaps(code, day)
    from engine import _KIND_PRIO
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
    from engine import Engine
    eng = Engine()
    eng.ingest(evs[:cur])
    gate = {'B': int(round(fr['ask_p1'])), 'S': int(round(fr['bid_p1']))}
    eng.materialize_leftovers(first_a, cross_gate=gate)
    # real first-anchor px sets per side
    real = {}
    for side, key in (('B', 'bid'), ('S', 'ask')):
        real[side] = {p for p, _ in mt.snap_row_to_ladders(fr, key)}
    # leftovers AFTER materialize: those gated-out still in registry (booked False)
    from collections import defaultdict
    dist = defaultdict(lambda: defaultdict(int))   # (carried?, side) -> add_ms bucket
    for rec in eng.registry_leftovers():
        side = rec['side']
        carried = rec['price'] in real[side]
        # add-ms bucket: seconds into auction (09:15:00 = 0)
        sec = (rec['ms'] - 33300000) // 1000
        dist[(carried, side)][sec // 60] += rec['rem']
    print('leftovers still in registry post-gate (voided-by-gate + not-materialized):')
    for k in sorted(dist):
        carried, side = k
        print(f'  {"CARRIED-REAL" if carried else "NOT-IN-REAL"} {side}: per-minute vol:',
              dict(sorted(dist[k].items())))
    # also: what did the gate skip (crossed) vs real actually do to those px?
    # whole-picture: orders that engine skipped at materialize, were their px in real book?
    skipped_crossed = Counter()
    skipped_inreal = Counter()
    for rec in eng.registry_leftovers():
        lim = gate.get(rec['side'])
        side = rec['side']
        if lim is None:
            continue
        crossed = (rec['price'] >= lim) if side == 'B' else (rec['price'] <= lim)
        if crossed:
            skipped_crossed['vol'] += rec['rem']
            skipped_crossed['n'] += 1
            if rec['price'] in real[side]:
                skipped_inreal['vol'] += rec['rem']
                skipped_inreal['n'] += 1
    print(f'  gate-skipped orders: n={skipped_crossed["n"]} vol={skipped_crossed["vol"]}; '
          f'of those, px PRESENT in real book: n={skipped_inreal["n"]} vol={skipped_inreal["vol"]}')
    # where did materialized orders' px come from in ms terms (booked leftovers)?
    b_ms = [rec['ms'] for rec in eng._orders.values() if rec['booked'] and rec['phase'] in ('auction',)]
    print(f'  booked leftover orders n={len(b_ms)} add-ms min={min(b_ms) if b_ms else None} '
          f'max={max(b_ms) if b_ms else None}')
    for side, name in (('B', 'bid'), ('S', 'ask')):
        px_sets = Counter()
        for rec in eng.registry_leftovers():
            if rec['side'] == side:
                px_sets['in-real' if rec['price'] in real[side] else 'void'] += rec['rem']
        print(f'  side {name}: registry-leftover vol by real-presence: {dict(px_sets)}')
