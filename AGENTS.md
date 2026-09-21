# 工作流与技能（stock 单仓单树）

> 单一 agent 入口：原 `platform/AGENTS.md` 与 `research/AGENTS.md` 已并入本文件（R24，2026-09-16）。
> 硬性纪律见根 `CLAUDE.md`；目录/数据权威见 `governance/workspace/{directory-conventions,data-map}.md`。

## 技能（Skills）

- **用户级**（仓外 `~/.claude/skills/`）：`factorlab-dsl`（spec/DSL）、`factorlab-data`、
  `factorlab-ch-pipeline`、`factorlab-backtest`、`factorlab-evaluate`、`quark-share-download`。
- **仓内**：`.claude/skills/factor-mine/` —— 挖因子循环（种子→假设审核→变异→实现→审核→入库）。
- **研究员入口 = `flab`**（R31）= `factorlab research`：固定 ch 后端、单 JSON、重命令自动过 heavy 闸；
  自描述 `flab describe --json`；一页手册 `knowledge/handbooks/research-agent-manual.md`。
  文档防漂移门：手册/registry 示例中的 flab 命令必须存在（`platform/tests/test_doc_paths_exist.py`）。
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

> **研究产物区（R37）**：spec/档案/索引/结果不在主仓——根 = `QUANTRESEARCH_ROOT`
> （缺省 `/data/students/gaolei/quantresearch`；平台侧 `settings.research_root`，
> 工具侧 `research/tools/factor_lib/quantresearch_paths.py`）。主仓只留工具。

1. **假设**：写清经济逻辑与预期方向（`docs(factors)` 档案的「假设」节）。
2. **实现**：`$QUANTRESEARCH_ROOT/factor/<族>/<短名>.yaml`。自定义处理函数优先写在 `formula` 里的 `def`
   （零注册、本因子专用）；稳定后再提升为 `ts_`/`cs_` 前缀的插件算子（`factorlab op add`）。
3. **自检**：`platform/.venv/bin/factorlab lint <spec>`（秒级）。全库 lint 必须全过
   （`make lint-factors` 现测；2026-09-16 快照 167 条，数量随挖矿增长，不在此写死）。
4. **跑**：`FACTORLAB_DATA_BACKEND=ch platform/.venv/bin/factorlab run <spec>`；看 IC/分层/换手。
5. **归档**：同族同名档案 `$QUANTRESEARCH_ROOT/dossiers/factors/<族>/<短名>.md` + `make index`
   重生成索引；缺档门有 72h 时效宽限（git 仓内按提交时间，产物区按 yaml mtime），
   超期红（`research/tools/factor_lib/dossier_freshness.py`）。
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

## 重任务运行协议（R05-C1 + R30 主机内存保护；2026-09-16/18 主机内存事故后）

三层防护（2026-09-18 R30 上线，根因证据 `governance/evidence/verification/R30/memory-guard/`）：

1. **memguard（用户级常驻守护；systemd user service `memguard.service`，Restart=always）**
   ——2s 采样 `/proc/meminfo` + 本用户进程 RSS；阈值 avail<10GB warn / <5GB term /
   <2.5GB kill；**R30.1 swap 前置触发**（机械盘 swap 是 freeze 主因）：swap_free<10GB
   且 avail<15GB → term、swap_free<6GB 且 avail<8GB → kill，与原阈值取更严重者
   （心跳带 swap 用量与 source 标注）；只杀 gaolei 且 RSS>=2GB 的重任务候选（cmd 匹配 python|pytest|vllm|
   run_pipeline|factorlab|convert|ingest|polars|jupyter），保护 sshd/systemd/opencode/
   code/vscode-server/clickhouse/bash/memguard 自身；llama-server 默认保护，RSS>38GB
   才转候选（模型重载爆内存场景）；SIGTERM→3s→SIGKILL，动作后 30s 冷却，`--dry-run`
   只看不杀。代码 `governance/ops/memguard.py`；安装 `governance/ops/install_memguard.sh`
   （linger 不可用则回退 crontab，路径记录在 `~/.local/state/memguard/install-method`）；
   测试 `governance/ops/tests/test_memguard.py`。
   **取证日志**：`~/.local/state/memguard/memlog.tsv`（10s 粒度、7 天轮转，
   time/avail/swap_free/load1/top5 RSS）+ `memguard.log` / `journalctl --user -u memguard`。
2. **`governance/ops/heavy.sh` 重任务闸（含自牺牲优先级）**——重任务一律经它跑：
   flock 限 2 并发（HEAVY_MAX_CONCURRENT 可调）+ 启动前可用内存 <8GB 拒绝 + 默认
   注入 `FACTORLAB_MAX_MEMORY=8GB`、`FACTORLAB_MIN_AVAILABLE_MEMORY=6GB`、
   `OMP_NUM_THREADS=8`、`POLARS_MAX_THREADS=8` + `nice -n 10`（已显式导出的值优先）
   + exec 前写 `/proc/self/oom_score_adj=${HEAVY_OOM_SCORE_ADJ:-700}`（R30.1：内核
   OOM 时优先杀本重任务而非 sshd；仅允许 0..1000，非法值拒绝启动）。
   例：`governance/ops/heavy.sh platform/.venv/bin/factorlab run <spec>`。
3. **FactorLab CLI 默认护栏（R30）**——`factorlab run` 在 env 未设时自动：
   `FACTORLAB_MIN_AVAILABLE_MEMORY=6GB`（安全预检）+ `FACTORLAB_MAX_MEMORY=
   min(16GB, 12% 物理内存)`（本机 125GB → ≈15GB；显式 `off`/`none` 关闭；run 结束
   语义复原）。`RLIMIT_AS` 硬上限仍只跟显式设置；API 直调 `run_factor` 不变。超限 →
   `MemoryLimitExceeded` 干净中止（exit 1、不落半成品，产物 dir 无可加载 summary）。
   语义/推荐值/报错：`knowledge/contracts/interface.md` §1「进程内存护栏」。

- **禁止与 LLM 服务（llama-server）/多 agent 会话并发重任务**（memguard 的
  llama>38GB 例外是最后兜底，不是许可）。事故教训（2026-09-16）：21GB llama-server
  + 6 个 opencode 会话 + 平台分钟链叠加 → 主机内存耗尽、SSH 卡死、ClickHouse 一度
  无响应（进程 D 状态零输出）。
- ClickHouse 进程上限已收紧（2026-09-18）：`max_server_memory_usage` 48→**28GiB**、
  `max_memory_usage_for_all_queries` 24→**12GiB**（`/data/students/gaolei/clickhouse/config/config.xml`）。
- 主机级加固（sysctl `vm.swappiness=10`/`vm.min_free_kbytes=1GB` + earlyoom）需
  sudo：脚本已生成 `governance/ops/memory-hardening-sudo.sh`，申请单一页版
  `governance/ops/memory-hardening-request.md`，**待用户执行**
  （本机 user cgroup MemoryMax 不可用——memory 控制器未委派，实测 900MB 分配在
  512M 限制下仍成功）。
- 分钟链保持默认 20 交易日/块；显式超大 `--chunk-days` 按估算告警/拒绝。

## 研究实验流水线（R37）

- 入口：`make xpipe CFG=research/tools/xscore/pipeline/configs/<cfg>.yaml`（Prefect 3）
- 阶段：`data_prep`（缓存幂等）→ `xscore`（分数）→ `porteval`（组合评估）→ `report`
- 服务：`install_prefect_server.sh`（UI :4200）/ `install_prefect_runner.sh`（UI 可触发）
- 文档：`research/tools/xscore/pipeline/README.md`；产物 manifest 强制

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
