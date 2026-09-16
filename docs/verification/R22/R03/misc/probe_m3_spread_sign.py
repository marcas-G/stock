"""R03-M3 probe：quant_core `decile_returns.spread` 符号约定（direction 相对量）。

构造确定性面板（signal 0..9，fwd 与 signal 正相关、跨 4 周重复）：
- 同一因子 raw IC 不随 direction 变；spread = (g0−g9)×direction 随 direction 翻转；
- 方向自洽（声明方向与数据一致）时 spread 恒为负：
  dir=+1 + 正相关（IC>0）→ spread<0；dir=−1 + 负相关（IC<0）→ spread<0。

运行：cd platform && .venv/bin/python \
      ../docs/verification/R22/R03/misc/probe_m3_spread_sign.py
"""

import quant_core

dates, codes, pos, neg = [], [], [], []
for i in range(4):
    d = f"2024-01-{5 + i:02d}"
    for j in range(10):
        dates.append(d)
        codes.append(f"{j:06d}.SZ")
        pos.append(float(j))        # 正相关：fwd = signal
        neg.append(-float(j))       # 负相关：fwd = −signal

print("# 正相关面板（raw IC>0）")
for dr in (1, -1):
    r = quant_core.evaluate_factor(dates, codes, pos, pos, "f", dr)
    drr = r["decile_returns"]
    print(f"  direction={dr:+d} ic={r['ic']['mean']:+.4f} "
          f"spread={drr['spread']['ret']:+.4f} (g0={drr['groups'][0]['mean_ret']:.2f} "
          f"g9={drr['groups'][9]['mean_ret']:.2f})")

print("# 负相关面板（signal=pos、fwd=neg，raw IC<0）")
for dr in (1, -1):
    r = quant_core.evaluate_factor(dates, codes, pos, neg, "f", dr)
    drr = r["decile_returns"]
    print(f"  direction={dr:+d} ic={r['ic']['mean']:+.4f} "
          f"spread={drr['spread']['ret']:+.4f} (g0={drr['groups'][0]['mean_ret']:.2f} "
          f"g9={drr['groups'][9]['mean_ret']:.2f})")

r1 = quant_core.evaluate_factor(dates, codes, pos, pos, "f", 1)
r2 = quant_core.evaluate_factor(dates, codes, pos, pos, "f", -1)
assert r1["ic"]["mean"] == r2["ic"]["mean"] > 0
assert r1["decile_returns"]["spread"]["ret"] == -r2["decile_returns"]["spread"]["ret"]
assert r1["decile_returns"]["spread"]["ret"] < 0   # dir=+1 且正相关 → 负（方向自洽）
assert r2["decile_returns"]["spread"]["ret"] > 0   # dir=−1 与数据相反 → 正
print("VERDICT: spread=(g0−g9)×direction；raw IC 不受 direction 影响；"
      "方向自洽 ⇒ spread<0（读法已写入 platform/docs/interface.md 评估段）")
