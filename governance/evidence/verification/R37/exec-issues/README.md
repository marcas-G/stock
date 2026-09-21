# R37 执行面问题证据（方案 A）

## R37-EXEC-I1（滑点越限 fail-fast）

- 命令：`flab strategy run ~/quantresearch/experiments/r37_5y/low_lottery_5y.yaml`（5 年窗口，slippage_bps=5）
- 报错原文（保留摘录，产物 JSON 已按方案 A 移除）：
  `ValueError: 600900.SH cost-model slippage crosses legal market limit：execution_price=21.480735 不在 [down=17.58, up=21.48]——bounded slippage model is not implemented（不 clipping）`
- 复现 doc：`low_lottery_5y_slippage5.yaml`（本目录）

## R37-EXEC-I2（无涨跌幅制度日 → stk_limit 误带）

- 探针：`probe_stk_limit_gap.py` → 输出 `stk-limit-gap-output.txt`、`outside-band-rows.csv`
- 结论：5 年窗口 140 行 / 138 券越带（0.0008%）；样例 000502.SZ 2022-06-06（退市整理期首日 3.80→0.45 合法无限制）；
  平台侧已知近似（derive_stk_limit.py docstring），研究侧应对=股票池剔除退市整理期/复牌股。
