# Task 2 — polars_ta 全量分类表（生成器 + 产物）

**命令**
- 生成：`.venv/bin/python scripts/gen_op_catalog.py`
- 校验：`.venv/bin/python scripts/gen_op_catalog.py --check`（exit 0/1）
- 测试：`cd platform && .venv/bin/python -m pytest tests/test_op_classification.py -q`
- 红：`before.txt`（3 failed：`_generated_ta_ops` / 脚本不存在）；绿：`after.txt`（14 passed）

**产物规模**：381 条（ts=216 / cs=30 / el=135），≥ 计划下限 350（Spike 1 预期 350~400）。

## 与 plan.md 字面规则的偏差（存疑处理，逐条理由）

1. **窗口判定从"第 2 个必需参数"改为"位置参数中第一个 int 参数"**
   - 现实：polars_ta 0.5.17 的窗口参数几乎都带默认值（`ts_mean(x, d: int = 5)`），
     计划的 `_required` 判定 `req[1]` 只取无默认参数 → 绝大多数 ts 函数窗口判成 None，
     G3（窗口推断驱动预热/分块）整体失效。
   - 调整：扫描位置参数（含带默认值者）中第一个 int 参数；窗口在第 3+ 位也正确
     （`ts_corr(x,y,d)` → arg:2，测试锁定）。
2. **float 注解窗口特判**：`BBANDS.close, timeperiod: float = 5.0` 是窗口（正式名
   timeperiod），但通达信选股族的 `N: float`（低开大阳线/高开大阴线等）是阈值。
   规则：float 注解仅正式窗口名（window/period/length/lag/timeperiod）算窗口，排除
   单字母 n/d。否则阈值参数会被当窗口，合法调用会报"窗口参数必须是常量"。
3. **冒烟调用显式传 int 窗口**：vendor `BBANDS(close)` 因默认 `timeperiod=5.0` 内部
   `int` 转换 TypeError（实测）；模板对"正式窗口名 float 默认参数"显式传 int
   （`BBANDS(close, 5)` 正常）——这是调用模板参数类型问题（Spike 1 归因），非算子缺陷。
4. **跨模块同名去重（wq > ta > tdx）**：expr_codegen 生成代码按 tdx→ta→wq→cdl→vec
   顺序 star import（后者覆盖前者）→ 运行时优先序 wq > ta > tdx；产物按同序去重，
   避免同名两条元数据随机覆盖。
5. **`ts_quantile` 不存在**：polars_ta 0.5.17 三库无此函数（实测 `vars` 扫描），
   计划测试中的 `ts_quantile` 用例改用真实未注册函数 `ts_arg_max`（`arg:1`）与
   `ts_corr`（`arg:2`）作为开放面证据。计划 Task 6/7/8 中的 `ts_quantile` 同样替换
   （见各任务证据）。
6. **计划的 render 漏写 ROWS 收尾 `]`**（生成文件 `SyntaxError`，计划自带样例含 `]`）：
   已补（`FOOTER` 以 `]` 开头）。

## MANUAL_OVERRIDES（逐条理由，无 TBD）

- **全历史累计/递归/有状态 → unbounded（与分块互斥 fail fast）**：
  `ts_OBV`（cum_sum）、`ts_AD`（cum_sum）、`ts_CUMSUM`（cum_sum）、
  `ts_BARSLAST`/`ts_BARSLASTCOUNT`/`ts_BARSSINCE`（cum_count/cum_sum+forward_fill）、
  `ts_DMA`（ewm 递归）、`ts_VALUEWHEN`（forward_fill）、`ts_up_stat`（累计连板）、
  `ts_signals_to_size`（顺序状态机）、`ts_resid`/`ts_pred`（窗口参数 keyword-only，
  静态不可见 → 保守 unbounded，宁可分块 fail fast 不算错）。
- **固定窗口（source 核验）**：`ts_weighted_decay`=2（rolling_sum(2)）、
  `ts_TRANGE`/`ts_TR`=1（shift(1)）、`ts_CROSS`=2（shift(1)/shift(2)）。
- **CS 多数据参数掩码（保持存量语义）**：`cs_resid`/`cs_resid_w`/`cs_resid_zscore`/
  `cs_mad_zscore_resid`/`cs_zscore_resid`/`cs_regression_neut`/`cs_regression_proj`/
  `cs_rank_if` → `(0,1)`。其中 `cs_resid` 是存量注册算子（平台掩码表原为 (0,1)），
  默认规则 (0,) 会改变 152 存量因子行为。

## 已知残余（Plan 2 conformance 范围）

- 通达信 REF 模式族（`ts_早晨之星`/`ts_四串阳`/`ts_单日放量` 等）硬编码 REF/MA 偏移，
  签名无可推断窗口 → 目录中 window=None（lookback 0）。全段跑结果正确；分块运行时
  块边界的 REF 预热不充分。已在报告"未解决/存疑点"列出。
