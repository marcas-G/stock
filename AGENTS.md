# 工作流与技能（stock 单仓单树）

> 单一 agent 入口：原 `platform/AGENTS.md` 与 `research/AGENTS.md` 已并入本文件（R24，2026-09-16）。
> 硬性纪律见根 `CLAUDE.md`；目录/数据权威见 `governance/workspace/{directory-conventions,data-map}.md`。

## 技能（Skills）

- **用户级**（仓外 `~/.claude/skills/`）：`factorlab-dsl`（spec/DSL）、`factorlab-data`、
  `factorlab-ch-pipeline`、`factorlab-backtest`、`factorlab-evaluate`、`quark-share-download`。
- **仓内**：`.claude/skills/factor-mine/` —— 挖因子循环（种子→假设审核→变异→实现→审核→入库）。
- 新增技能放对应位置并在此登记。

## 通用工作流（任何树）

1. **需求澄清**：写代码前先厘清需求与边界（不明确就问，不猜）。
2. **计划**：多步骤任务先出实施计划（含验收标准），再动手。
3. **TDD**：红-绿-重构——先写失败测试（断言来自设计文档/规格，不是实现），
   再写最小实现；替换为存根必败的测试才算有效。
4. **执行**：逐任务推进；每个任务收尾跑相关测试。
5. **审查**：关键改动做代码审查（正确性/复用/简化）。
6. **收尾**：全量 `pytest -q` 通过 + 文档同步 + 按目录分权提交。

## 挖因子循环（skill: `.claude/skills/factor-mine/`）

1. **假设**：写清经济逻辑与预期方向（`docs(factors)` 档案的「假设」节）。
2. **实现**：`research/factor/<族>/<短名>.yaml`。自定义处理函数优先写在 `formula` 里的 `def`
   （零注册、本因子专用）；稳定后再提升为 `ts_`/`cs_` 前缀的插件算子（`factorlab op add`）。
3. **自检**：`platform/.venv/bin/factorlab lint <spec>`（秒级）。全库 lint 必须全过
   （`make lint-factors` 现测；2026-09-16 快照 167 条，数量随挖矿增长，不在此写死）。
4. **跑**：`FACTORLAB_DATA_BACKEND=ch platform/.venv/bin/factorlab run <spec>`；看 IC/分层/换手。
5. **归档**：同族同名档案 `knowledge/dossiers/factors/<族>/<短名>.md` + 重生成 `knowledge/index/factors.md`。
6. **记账**：把结论（含负结论）写进档案；未决项进 `governance/workspace/pending-items.md`。

## 研究侧补充

- **假设先行**：新因子/策略先写清"市场行为假设 + 为什么可交易"，再动手实现。
- **对拍**：与平台内核共享入口的批算（如 1m 特征）必须过 `check-day` 单日对拍（max|Δ|=0）。
- **留证**：运行产出（state.json、sha256 摘要、对拍报告）落对应工具 output/notes；
  新增/改名后重生成索引（有 byte-equality 门）。

## 提交与证据纪律

- 提交信息：`<type>(<scope>): <做了什么>`；**一次提交只动一棵树/一个主题**。
- 每轮结构/行为改动都要留**可复现证据**：`governance/evidence/verification/<轮次>/`（命令 + 原始输出 + 门结果）。
- 门优先于断言："测试全绿" ≠ "链路能跑"——涉及入口/装配/数据接口的改动，**必须真跑一次**
  （真 CH 或真 parquet）并留下输出。
- 破坏性/不可逆操作（删除、强推、覆盖远端）**先备份后执行**，并把退路（bundle/tag/路径）写进证据。

## 重任务运行协议（R05-C1；2026-09-16 主机 OOM 事故后）

- 重任务（全市场/长窗 `factorlab run`、分钟链、灌库/回测批跑）**必须**设进程内存护栏：
  `FACTORLAB_MAX_MEMORY=8GB`（16GB 机推荐；显式设置时 CLI 同时落 RLIMIT_AS 硬上限）
  + 可选 `FACTORLAB_MIN_AVAILABLE_MEMORY=2GB`。超限 → `MemoryLimitExceeded` 干净中止
  （exit 1、不落半成品，产物 dir 无可加载 summary）。
- **禁止与 LLM 服务（llama-server）/多 agent 会话并发重任务**。事故教训（2026-09-16）：
  21GB llama-server + 6 个 opencode 会话 + 平台分钟链叠加 → 主机内存耗尽、SSH 卡死、
  ClickHouse 一度无响应（进程 D 状态零输出）。
- 分钟链保持默认 20 交易日/块；显式超大 `--chunk-days` 按估算告警/拒绝。
  语义/推荐值/报错：`knowledge/contracts/interface.md` §1「进程内存护栏」。

## 工具链速查

| 目的 | 命令 |
|---|---|
| 平台测试 | `cd platform && .venv/bin/python -m pytest -q` |
| 工具/研究测试（单解释器） | `make test-research`（= `platform/tools` + `research/tools`，均平台 venv） |
| 分钟面 × 本地 parquet 对拍 | `FACTORLAB_DATA_BACKEND=ch platform/.venv/bin/python platform/tools/1m_features/run_1m_feature.py check-day 2024-01-15` |
| CH 灌入对账 | `platform/.venv/bin/python platform/tools/ch_ingest/reconcile.py`（`make reconcile`） |
| 常驻门 | `make gates`（= `governance/ops/gates.sh`） |

## 调试

- 修 bug：先复现（最小用例）→ 定位根因 → 修复 → 回归测试锁死。
- 数据问题先查 `governance/workspace/data-map.md`（哪个表是谁生产的、生产者在哪）。
- 批算异常先看 flock 单写者门与 `_SUCCESS` 事务边界（半成品分区不入库）。
- 数字不对时先确认用的是**哪个内核**（`_env.py::ensure_platform()` 落位断言就是为此存在）。

## 已知的"别踩"

- 平台 duckdb 库不存在 → 用 `FACTORLAB_DATA_BACKEND=ch`。`exclude_st` 在 CH 无 `stock_st`
  表时默认 fail fast；需要临时跑库内 spec 可用 `FACTORLAB_ST_DEGRADE=allow` 显式降级
  （warning + `is_st=null` + summary `st_degrade: true`，结果为**无 ST 口径**，不可与 ST 过滤
  run 混比；真实口径需补 `stock_st` 灌入——见 interface.md §4.2）。
- 写算子必须带分区前缀（`ts_`/`cs_`），裸名注册会被拒（静默退化为元素级 = 跨资产泄漏）。
- `platform/tools/lob_fact/core/config.py` 的校准常量与 `fixtures/pins.sha256` 是冻结金样。
- 工具内的模块名要全局唯一（会与 `lob_fact/core/config.py` 等撞名被 G-TOPO 判跨工具 import）。
