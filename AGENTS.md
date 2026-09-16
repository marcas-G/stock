# 工作流与技能（stock 单仓单树）

## 挖因子循环（skill: `.claude/skills/factor-mine/`）

1. **假设**：写清经济逻辑与预期方向（`docs(factors)` 档案的「假设」节）。
2. **实现**：`research/factor/<族>/<短名>.yaml`。自定义处理函数优先写在 `formula` 里的 `def`
   （零注册、本因子专用）；稳定后再提升为 `ts_`/`cs_` 前缀的插件算子（`factorlab op add`）。
3. **自检**：`platform/.venv/bin/factorlab lint <spec>`（秒级）。全库 lint 必须 152/152 通过。
4. **跑**：`FACTORLAB_DATA_BACKEND=ch platform/.venv/bin/factorlab run <spec>`；看 IC/分层/换手。
5. **归档**：同族同名档案 `research/docs/factors/<族>/<短名>.md` + 重生成 `docs/index/factors.md`。
6. **记账**：把结论（含负结论）写进档案；未决项进 `docs/pending-items.md`。

## 提交与证据纪律

- 提交信息：`<type>(<scope>): <做了什么>`；**一次提交只动一棵树**（platform / research / docs）。
- 每轮结构/行为改动都要留**可复现证据**：`docs/verification/<轮次>/`（命令 + 原始输出 + 门结果）。
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
| 常驻门 | `make gates` |

## 已知的"别踩"

- 平台 duckdb 库不存在 → 用 `FACTORLAB_DATA_BACKEND=ch`。`exclude_st` 在 CH 无 `stock_st`
  表时默认 fail fast；需要临时跑库内 spec 可用 `FACTORLAB_ST_DEGRADE=allow` 显式降级
  （warning + `is_st=null` + summary `st_degrade: true`，结果为**无 ST 口径**，不可与 ST 过滤
  run 混比；真实口径需补 `stock_st` 灌入——见 interface.md §4.2）。
- 写算子必须带分区前缀（`ts_`/`cs_`），裸名注册会被拒（静默退化为元素级 = 跨资产泄漏）。
- `platform/tools/lob_fact/core/config.py` 的校准常量与 `fixtures/pins.sha256` 是冻结金样。
