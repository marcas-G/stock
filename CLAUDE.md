# 研究 worktree 项目指南（research 分支）

本工作树 = `quant-platform` 仓库 research 分支。**只承载研究内容**：
`tools/`（lob_fact / ch_ingest / converters / 1m_features / strategies / quark_download）、
`factor/`、`docs/factors/`、`docs/strategies/`、`.claude/skills/`。

## 硬性要求

### 单一共享核（最高优先级，2026-09-12 起）

- 平台源码/测试/平台文档的**唯一副本在 main worktree**（`../quant-platform-main/`）；
  本分支**不携带** `src/`、`tests/`、`docs/interface.md`、`docs/catalog.md`、
  `docs/data-ops-playbook.md`、`docs/teajoin-guide.md`（曾有副本，已实测漂移后退役）。
- 研究工具 import 平台代码**必须**经 `tools/_env.py::ensure_platform()`
  （注入 main/src + 落位断言；解析到别处即 RuntimeError）。禁止任何新的
  `sys.path` 手写注入或本地副本。
- **平台改动一律提交到 main worktree 的 main 分支**（提交前缀 `feat(...)`/`fix(...)`/
  `docs(...)` 平台语义）；research 分支提交前缀用 `feat(tools)`/`refactor(tools)`/
  `docs(factors)` 等研究语义。**本分支不再与 main 合并**（共享核经安装/注入到达）。

### 文档和测试（最高优先级）

- TDD：先写失败测试再实现；测试覆盖正常/边界/错误路径；断言真实行为，不用 mock 糊弄。
- 依赖外部资源（CH / 本地事实库）的测试：环境缺失时 **skip 而非假通过**
  （T1 用例经 `pytest.importorskip` 在 emb 下 skip，用平台 venv 跑真验）。
- 提交前跑本 worktree 相关测试：`pytest tools/ -q`（emb）+ T1 用平台 venv 补跑。
- 文档：研究侧文档进本分支；平台契约文档在 main worktree（引用写
  `../quant-platform-main/docs/...`）；数据位置以 workspace `docs/data-map.md` 为准。

## 环境事实

- **解释器双轨（刻意不统一**，统一会改 polars 补丁版本威胁 lob_fact 字节级重跑）：
  - T1（`1m_features/`、`strategies/`，需完整 factorlab）= `../quant-platform-main/.venv/bin/python`（3.13，uv）；
  - T2（`lob_fact/`、`converters/`、`ch_ingest/`）= emb（3.11）`/data/students/gaolei/anaconda3/envs/emb/bin/python`。
- CH：`127.0.0.1:8123` db=factorlab（`ch_ingest/reconcile.py` 需平台 venv——emb 缺
  clickhouse_connect）。
- 数据根：`../../data/{raw,fact,calib,ref}`（单点在平台 `core.factio.paths`；本分支旧
  config 常量逐步收敛）。
- 16GB 内存无页面文件：批算单进程 + 流式 + 及时释放；长任务 nice，禁止并行重活。
- lob_fact 校准常量（W1 冻结值 + `pins.sha256` 金样）**不可改**：改动即让 183 测试与
  历史结论失效，需走再校准流程。
