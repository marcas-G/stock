"""unattr/ghost vol by 30-min bucket — validates memo attribution labels"""
import sys
sys.path.insert(0, '/data/students/gaolei/stock/platform/tools/lob_fact')
import measure_w3 as M
import anchoring as A
import config as C

for code, day in (('600036','20251215'), ('000021','20260706'),
                  ('600184','20250915'), ('600036','20260513')):
    evs, ga, gt = M.load_events(code, day)
    snaps = M.load_snaps(code, day)
    out = A.run_day(evs, snaps)   # band 状态同 measure (rows 抑行不影响 QA)
    from collections import defaultdict
    bk = defaultdict(lambda: [0, 0, 0])   # unattr_v, ghost_v, n_missing_lev
    for w in out['qa']['windows']:
        b = (w['ms'] - C.OPEN) // 1800000
        for s_ in ('bid', 'ask'):
            s = w[s_]
            bk[b][0] += s['unattributed_vol']
            bk[b][1] += sum(v for _, v in s['ghosts'])
            bk[b][2] += s['n_missing']
    tot = [sum(x[i] for x in bk.values()) for i in range(3)]
    parts = []
    for k in sorted(bk):
        v = bk[k]
        parts.append(f'{v[0]:>8},{v[1]:>7},{v[2]:>4}@{k}')
    print(f'{code}@{day} tot unattr={tot[0]} ghost={tot[1]} miss_lev={tot[2]}')
    print('   unattr,ghost,misslev per 30min bkt:', ' | '.join(parts))
