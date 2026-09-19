# 同花顺模拟炒股接入 实施计划（Plan T）

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** FactorLab 策略 → 同花顺模拟炒股（`mncg.10jqka.com.cn`）前向模拟闭环：盘后算单、次日开盘下单、回执与台账对账。

**Architecture:** research 侧独立工具包 `research/tools/paper_broker/`；客户端 → 订单计划 → 执行 → 台账 四层；
外部依赖仅"网页 POST 接口"一处（薄适配器，将来可换 Futu/QMT）；约束语义与 M8 对齐。

**Tech Stack:** Python 3.13（平台 venv）/ requests / polars（读目标与 CH 数据）；pytest。

**Spec:** `knowledge/design/research/specs/2026-09-17-ths-simulated-api-design.md`
**参考**：`Cfu4536/ths_simulated_API`（只参考接口形状；**不得复制其硬编码 Cookie**）

## Global Constraints

- **凭据红线**：Cookie/账号只从 `~/.config/factorlab/ths_session.json`（0600）或 `THS_COOKIE` 读取；
  仓库、日志、异常、测试夹具中**不得出现会话字面量**（Task 1 加静态门）。
- **只 mock 真正的外部依赖**：HTTP 层用 fake session，测试必须断言**请求 URL 与表单参数**（防存根）。
- **整手/T+1/涨跌停/停牌/资金**：与 M8 语义一致；计划与执行分离（`plan` 可 dry-run）。
- **一次提交一棵树**；证据落 `governance/evidence/verification/R31/`。
- 仅模拟盘；不做登录逆向（会话由用户浏览器导出）。

---

### Task 1：会话加载 + 探活 + 泄漏门

**Files:** Create `research/tools/paper_broker/session.py`、`tests/test_session.py`、静态门（并入现门）

- [ ] **Step 1: 失败测试**：`load_session()` 从 env/文件读取；缺凭据 → `ValueError` 含获取指引；日志/异常文本断言**不含** cookie 片段；仓内扫描测试：`grep` 无 `u_ukey=` 等字面量（负向断言）。
- [ ] **Step 2-3: 红→实现**（`THS_COOKIE` 优先 → 文件 → 明确报错；`redact()` 工具）。
- [ ] **Step 4: 提交** `feat(paper): 会话加载与泄漏门（Plan T1）`

### Task 2：ThsSimClient（网页接口薄客户端）

**Files:** Create `research/tools/paper_broker/ths_sim_client.py`、`tests/test_ths_sim_client.py`

- [ ] **Step 1: 失败测试**（fake session，逐项断言 URL+表单）：`buy/sell`（`cgiwt/delegate/tradestock/`）、`cancel`（`cancelDelegated/`）、`get_orders/get_positions/get_fund`（`updateclass/`）、`qry_stock`（`qrystock/`，含五档字段解析）；错误码映射（`errorcode != 0` → `ThsOrderRejected`，附 `errormsg`）；超时/网络错 → 可重试异常。
- [ ] **Step 2-3: 红→实现**（纯 requests，无隐式重试；返回 dataclass `Position/Order/Fill`）。
- [ ] **Step 4: 提交** `feat(paper): ThsSimClient 薄客户端（Plan T2）`

### Task 3：目标 → 订单计划（order_plan）

**Files:** Create `research/tools/paper_broker/order_plan.py`、`tests/test_order_plan.py`

- [ ] **Step 1: 失败测试**（表驱动，输入 target/positions/funds/limit/suspend）：
  整手向下取整；T+1 当日买入不可卖；涨停不买/跌停不卖（CH stk_limit）；停牌跳过；资金不足按比例缩减并告警；
  白名单与单票/总额上限；输出确定性（排序稳定）。
- [ ] **Step 2-3: 红→实现**；`--dry-run` 仅产出 `orders_plan.json`。
- [ ] **Step 4: 提交** `feat(paper): 目标→订单计划（Plan T3）`

### Task 4：执行器（幂等/回执/撤单/审计）

**Files:** Create `research/tools/paper_broker/execute.py`、`tests/test_execute.py`

- [ ] **Step 1: 失败测试**：逐单提交断言调用顺序与参数；失败单记录回执不无限重试（≤2）；当日计划哈希幂等（重复执行拒绝，`--force` 例外）；审计日志字段（时间/代码/数量/价格/回执/耗时）；**禁止行为**：计划为空时不得发任何请求。
- [ ] **Step 2-3: 红→实现**（含未成交撤单策略：按计划 `cancel_unfilled`）。
- [ ] **Step 4: 提交** `feat(paper): 执行器与幂等（Plan T4）`

### Task 5：台账镜像与对账

**Files:** Create `research/tools/paper_broker/ledger.py`、`tests/test_ledger.py`

- [ ] **Step 1: 失败测试**：账户查询 → `runs/paper/<strategy>/<date>/{positions,funds,orders}.parquet`；
  reconcile：账户 vs 本地镜像差异=0（合成数据）；差异>阈值 → 非零退出 + 报告。
- [ ] **Step 2-3: 红→实现**。
- [ ] **Step 4: 提交** `feat(paper): 台账镜像与对账（Plan T5）`

### Task 6：CLI + 调度 + 安全开关

**Files:** Create `research/tools/paper_broker/run_paper.py`、`tests/test_run_paper_cli.py`

- [ ] `plan|execute|reconcile|status` 子命令；`--strategy/--date/--dry-run/--max-notional/--whitelist/--force`；
  systemd timer/cron 示例（T 15:30 plan、T+1 09:31 execute、T+1 15:35 reconcile）；`status` 探活失败给出"重新导出 Cookie"指引。
- [ ] 测试：CLI 参数与退出码；dry-run 不触网。
- [ ] 提交 `feat(paper): CLI 与调度（Plan T6）`

### Task 7：验收（≥5 交易日小额真跑）

- [ ] 用一只已入库策略（如 `low_lottery_top30_weekly`）连续跑 ≥5 交易日：计划/回执/台账/对账差异=0；
- [ ] 证据 `governance/evidence/verification/R31/`（命令+原始输出+对账表）；安全门复跑（无凭据字面量）；
- [ ] 归档到 `knowledge/design/research/`，并在 reviews README 方案表登记状态。

## Self-Review（对 spec）

§1 链路→Task 3/4/5/6；§2 模块→Task 1-6；§3 安全→Task 1（门）+Task 6（探活）；§4 订单语义→Task 3；
§5 验收→Task 7。风险：接口变更（薄客户端隔离）、会话过期（status 指引）、非官方接口（仅模拟盘）。

## 风险

| 风险 | 处置 |
|---|---|
| 网页接口变更/风控 | 薄适配器 + 明确报错；回退：自建 EOD 模拟盘（survey §3） |
| Cookie 泄漏 | Task 1 静态门 + redact；凭据仅本地 0600 |
| 误下实盘 | 仅 mncg 模拟域名；域名白名单断言（Task 2 测试） |
| 重复下单 | 计划哈希幂等 + `--force` 显式 |
