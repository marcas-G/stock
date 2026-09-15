# Task 6 — 窗口/分块统一（lookback 驱动 + unbounded 互斥）

**命令**
- 单元：`cd platform && .venv/bin/python -m pytest tests/test_chunk_warmup_v2.py tests/test_semantics.py tests/test_compute.py -q`
  → 红 `before.txt`（collection error）；绿 `after_unit.txt`（44 passed）
- 计划 Step 4：`pytest tests/test_chunk_warmup_v2.py tests/test_chunk_label_exactness.py tests/test_qfq_chunk_invariance.py -q`
  → 绿 `after.txt`（42 passed，55s）
- 定向回归：`test_run_factor.py -k "cumulative or chunk or warmup"`（17 passed）、
  `test_plugin_partition_prefix.py`（2 passed）

**新接口**：`required_lookback(formula, pool=None, catalog=None)`（infer 驱动的
lookback 最大值，含变量引用链/方法/池公式）；`unbounded_ops(formula, pool=None,
catalog=None)`（分类表 `window=="unbounded"` 清单，别名解析，方法计 `.name`）；
`reject_cumulative_chunking` 改由该清单驱动（消息保留"累计算子"指引文案）。

## 与 plan.md 的偏差

1. **`_ts_window_days` 保留前缀回退腿**：计划把 `_ts_window_days` 直接换成
   `infer(...).lookback`，但存量测试锁定旧契约——
   - `tests/test_plugin_partition_prefix.py`：插件 `ts_op(close, 60)` 必须提取 60
     （插件不在分类表）；`bare_op(close, 60)`=0（裸名跨资产泄漏由插件命名门拒绝）；
   - `tests/test_compute.py`：`wq.ts_sum(close,10)+ta_MA(close,5)`=10（模块限定名）、
     `ts_mean(close,2.5)`=0（非整 float 窗口不计）。
   实现为 `max(_prefix_window_days, required_lookback)`：旧值必为下界 → 预热只多不少
   （零迁移安全），分类表算子另走推断（如 `ts_corr(x,y,120)` 旧 0 新 120——修正型增强）。
2. **推断的 lookback 只认 int 窗口**（float 仅参与未来门 forward）：保持
   `ts_mean(close,2.5)=0` 的旧口径；`ts_delay(close,-1.0)` 仍拒绝。
3. **变量引用链解析加入语义推断**（`_x = ts_mean(close,20); signal = _x*2` →
   lookback 20）：计划 Task 4 未列该形态，但存量因子大量使用中间变量，旧
   `_ts_window_days` 沿变量链叠加是其核心行为；不加入则 required_lookback 会
   在真实因子上退化到 0。
4. **`cumulative_ops_used` 保留**（test_run_factor.py:875 契约），仅
   `reject_cumulative_chunking` 切换到 `unbounded_ops`。
