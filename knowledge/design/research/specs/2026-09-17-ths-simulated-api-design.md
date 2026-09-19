# 同花顺模拟炒股网页接口 接入设计（2026-09-17）

**目标**：把 FactorLab 的日频/周频策略接到**同花顺模拟炒股**（`mncg.10jqka.com.cn` 网页版），
实现"盘后算目标 → 次日开盘下模拟单 → 回执与台账对账"的前向验证闭环。

**参考实现**：`Cfu4536/ths_simulated_API`（POST 构造；买/卖/撤单/持仓/资金/委托/五档）。
**已知风险（必读）**：① 该仓库**硬编码了真实会话 Cookie**（u_ukey/ticket/u_dpass）——**严禁复用**，
必须用自己的会话并放本地 gitignored 配置；② 非官方接口（网页 POST），页面/参数可能变更；
③ 仅模拟盘，不触实盘；用于研究验证。

## 1. 链路

```
T 日 15:30  run_strategy（或 YAML 策略）→ target_portfolio（目标权重）
                │
                ├─ 读 同花顺模拟账户（positions/funds，ThsSimClient）
                ├─ 目标 vs 持仓 → 订单计划（100 股整手；T+1 可用；资金约束；
                │   涨跌停/停牌过滤用 CH daily/stk_limit；白名单/单笔上限/总仓上限）
                └─ 落 runs/paper/<strategy>/<date>/orders_plan.json（不提交）
T+1 09:31   execute（市价单，price=实时价）→ 逐单 POST → 回执（errorcode/errormsg）
                 → 撤单重试（未成交且需要）→ 台账（fills/positions/nav 镜像）
T+1 收盘后  reconcile：账户持仓/资金 vs 本地镜像；日终 NAV 记录
```

## 2. 模块与边界（放 research 侧）

- `research/tools/paper_broker/`（新）：
  - `ths_sim_client.py`：网页接口薄客户端（会话注入、超时/重试、错误码映射、**绝不硬编码凭据**）；
  - `order_plan.py`：目标→订单（与 M8 同语义的约束：整手/T+1/涨跌停/停牌/资金/限额）；
  - `execute.py`：提交、回执、撤单、幂等（当日计划哈希，重复执行跳过）、审计日志；
  - `ledger.py`：账户查询→本地镜像（orders/fills/positions/nav）+ 对账报告；
  - `run_paper.py`：CLI（`plan|execute|reconcile|status`）。
- 从平台侧**只读**消费：CH daily/stk_limit（过滤）、`runs/platform/strategies/<name>/target_portfolio.parquet`（目标）。
- 执行器边界：`ths_sim_client` 之后可换 Futu/QMT/PTrade，`order_plan/ledger` 不动。

## 3. 会话与凭据（安全红线）

1. 凭据**只放** `~/.config/factorlab/ths_session.json`（0600，gitignored）或环境变量 `THS_COOKIE`；
2. 模块启动即断言：仓库内不存在任何会话字面量（门）；日志/异常**不得打印 Cookie**；
3. 会话探活：`run_paper.py status`（失败 → 明确提示"重新登录 mncg 复制 Cookie"）；
4. 可选便利：Playwright（Linux 可跑）登录一次导出 storage_state（后续再定，不在 V1）。

## 4. 订单语义（与回测对齐）

| 项 | V1 口径 |
|---|---|
| 交易时点 | 信号 T 收盘 → T+1 09:31 提交（市价单，`price=cur_price`） |
| 整手 | 买入向下取整到 100 股；卖出按可用持仓 |
| 约束 | T+1（当日买入不可卖）；涨跌停不买/不卖（CH stk_limit）；停牌跳过（CH 缺行） |
| 资金 | 可用资金 ≥ 计划买入金额 × (1+缓冲 1%)；否则按比例缩减并告警 |
| 限额 | 单笔 ≤ X 元、单票 ≤ Y% 总资产、白名单（默认持仓+目标并集） |
| 幂等 | 当日已 execute（计划哈希一致）→ 第二次直接拒绝，除非 `--force` |
| 失败 | 逐单回执记录；失败单不自动无限重试（≤2 次 + 告警） |

## 5. 验收

- 单元：order_plan 的表驱动用例（整手/T+1/涨跌停/停牌/资金缩减）；
- 集成：模拟账户真实小额跑 ≥5 个交易日，产出：订单/回执/持仓镜像/对账差异=0；
- 证据：`governance/evidence/verification/R31/`；
- 安全：凭据泄漏扫描门（仓库无 Cookie 字面量）；dry-run 全链路演练。
