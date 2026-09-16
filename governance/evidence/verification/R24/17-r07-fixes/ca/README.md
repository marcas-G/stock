# R07-DATA-I8（I）：CA Gate 多年连续回测硬阻断 — 修复证据

- 日期：2026-09-16
- 发现：R07 报告 §2 `R07-DATA-I8`；原始复现 `r07-2026-09-16-gap-audit/evidence/data-audit/04-ca-gate-repro.txt`
- 处置（R07 §4.6）：评估"多年连续"能力 → 本证据 = 能力落地（不再是"提供分段拼接口径"）

## 1. 根因

WS5 CA Gate 只读 `adj_event(ts_code, trade_date)`（**只有日期**）：窗口
`(prev_exec, exec]` 内 held(PRE) 命中即 `ExecutionDataQualityError`。真实数据下
月频策略 3 窗口必撞 2（R03-I8），分段 run 又因"每段从 initial_cash + 空仓位
开始"丢失段间持仓/资金连续性 → 多年回测无连续产物（无法算 Sharpe/回撤）。

## 2. 设计选择（口径）

| 决策点 | V1 选择 | 理由 / 证据 |
|---|---|---|
| 事件源 | `adj_event`（日期） + **`adj_detail`（调整量）** | 明细全表 18,124,805 行（`adj_detail_semantics.txt`）；事件行 57,173 |
| 资格日 | 除权日前收市持仓 = **PRE 持仓** | 跨 exec 间隔无成交，PRE 持仓 == 每个窗口日收市持仓 == 登记日持仓；买入日=事件日豁免（左开窗口）不变 |
| 现金分红 | `cash += 资格股数 × div_cash/10`（元/10股）；下一次 exec 开盘前入账 | 手算样例 1；真实片段 600519 精确对拍 |
| 送股/转增 | `qty/sellable ×= 1+(b+t)/10`；**不足 1 股 floor 舍去** | 数据含分数比例（1736 行非整数送转）；floor 与登记结算"零股顺延分配"不确定性的保守近似；Decimal 精确缩放（float 在 100×1.01 会少 1 股） |
| 配股 | **不参与**：shares/cash 不变 + `CorporateActionWarning` | 参与需申购/中签近似与现金扣减；不参与时除权价格落差自然计入 NAV = 真实成本，且 share-unit 连续 |
| 未支持 | fail-closed（负分红/缩股/负配股/明细缺失/停牌×CA） | 与 WS5 同级，不加宽 |
| 停牌×CA | fail-closed | 冻结 mark 为除权前 basis；不发明（理论）价格 |

## 3. 改动（platform 树）

- `src/factorlab/adapters/read/market_open.py`：新增 `load_adj_detail_window`
  （duckdb/ch 编译对；表缺失 typed empty；缺列/重复 fail fast；NaN 归一 NULL）
- `src/factorlab/core/execution/corporate_actions.py`（新）：
  `apply_corporate_actions`（PRE 状态 + 明细 → 调整后 PRE；Decimal floor 缩放；
  配股 warning）
- `src/factorlab/app/backtest/backtest.py`：`_assert_ca_gate` → `_apply_ca_adjustments`
  （快照后、订单前；fail-closed 面见 `interface.md` §6 CA Gate v2）
- `tests/test_backtest_ca_gate.py`（B1-B14 语义更新 + 明细 loader 合约 + 真实 CH 片段）
- `tests/test_corporate_actions.py`（新，primitive 单测 15 条）
- `tests/test_backtest_ca_multi_year.py`（新，4 年连续 + 存根必败）

文档：`knowledge/contracts/interface.md` §4 loader + §6 CA Gate v2；
`knowledge/design/platform/specs/2026-09-07-factorlab-daily-closeout-design.md`
§8.4 R07-DATA-I8 追加。

## 4. 验收产物（本目录，`ca_products.py` 一键重跑）

| 文件 | 内容 |
|---|---|
| `hand_calc.txt` | primitive 手算对拍：分红 250、送转 1000→1500、卖 400→600；floor 120.75→120 与 100×1.01→101 精度锁；配股 warning 不变 |
| `multi_year_nav.csv` | 4 年（2021-01→2024-12）53 决策 / 4 事件的**单 run 连续 NAV** |
| `multi_year_events.txt` | 逐事件：股数/现金/NAV 手算对拍（事件 #12/#24/#37 连续 ≈；#48 配股不参与 Δ= 除权落差） |
| `multi_year_metrics.json` | span 1456 天；总收益 +57.06%；Sharpe（月度年化）5.60；最大回撤 −2.57%；returns 全 finite；1 条配股 warning |
| `stub_kill.txt` | 存根必败：`_apply_ca_adjustments` 恒等 → 事件 #12 手算对拍失败 |
| `real_fragment.txt` | 真实 CH 复现锚点 600519.SH@2026-06-26：800 股分红 800×280.2423/10 = 22,419.38 精确入账，最小复现从"硬阻断"→"连续跑通" |
| `adj_detail_semantics.txt` | 全表分布（负值 0 / 配股 994 / 现金 52,815 / 送转 12,345 / 分数送转 1,736）→ 决策依据 |
| `pytest_required_and_new.txt` | 必跑 6 文件 + 新增 2 文件：**220 passed** |
| `pytest_full_platform.txt` | 平台全量：**3204 passed, 13 skipped**（exit 0） |

## 5. 复现

```bash
cd stock
# 必跑套件 + 新增
cd platform && .venv/bin/python -m pytest tests/test_backtest_ca_gate.py \
  tests/test_backtest_runtime.py tests/test_backtest_fills.py \
  tests/test_execution_accounting.py tests/test_execution_valuation.py \
  tests/test_backtest_persistence.py tests/test_corporate_actions.py \
  tests/test_backtest_ca_multi_year.py -q
# 证据产物（真实片段腿需 CH 在线）
cd .. && env FACTORLAB_DATA_BACKEND=ch platform/.venv/bin/python \
  governance/evidence/verification/R24/17-r07-fixes/ca/ca_products.py
```

## 6. 残余 fail-closed（仍拦截，原因）

1. **事件命中停牌持仓**（exec 当日无 daily open）：冻结 mark 是除权前 basis，
   调整后股数/现金无法用真实价估值 → 拒绝（不发明）。
2. **明细缺失**（缺 `adj_detail` 表 / (code,date) 无行 / 全 0）：两源不一致，
   不能证明调整量 → 拒绝。
3. **缩股 / 负分红 / 负配股**：share-unit 缩减/负向现金流语义未支持 → 拒绝。
4. **事件表缺失（armed）**：无法证明窗口无事件 → 拒绝（WS5 原语义）。

以上四类之外的常见事件（现金分红、送股、转增、配股不参与及其组合）均已放行并
会计入账，多年连续回测可直接产出。
