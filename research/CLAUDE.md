# 研究树项目指南（单仓单树，2026-09-15 起）

本目录 = `stock/research/`。旧的双分支纪律（main=平台 / research=研究、独立 worktree）已退役——
现在按**目录**分权：本目录只收研究内容；平台代码在 `../platform/`；工作区文档在 `../docs/`。
（工作区级总纲见仓库根 `CLAUDE.md`，此处只写研究树特有约定。）

## 硬性要求

### 目录与共享核

- **本目录只收研究内容**：`factor/`、`tools/`、`docs/`（factors/strategies/playbook/研究独有 spec）。
  提交前缀用研究语义：`feat(factor)` / `feat(tools)` / `docs(factors)` / `refactor(tools)`。
- **平台代码只有一份**：`../platform/src`。研究工具不得复制平台代码；引用平台一律经
  `tools/_env.py::ensure_platform()`（注入 + 落位断言；解析到别处即 RuntimeError）。
  **emb 装不了 factorlab（requires-python≥3.13）** → 注入是唯一通道。
- **数据接口只用单点**（R4 收敛）：读 tick/lob/bars 经 `adapters.tick_read` / `adapters.lob_read` /
  `adapters.bars_read`（研究侧薄封装 `tools/lib/tickdata.py`）；写/标记/锁/断点经
  `tools/lib/writekit.py`（`_SUCCESS` + state JSON 两种形态，禁止新形态）。
  分区路径一律取 `core.factio.partitions`，**不得自拼 `year=/month=`**（G-CONTRACT 门）。

### 文档和测试

- TDD：先写失败测试再实现；覆盖正常/边界/错误路径；断言真实行为，不用 mock 糊弄。
- 依赖外部资源（CH / 本地事实库）的测试：环境缺失时 **skip 而非假通过**（T1 用例用
  `pytest.importorskip`，在 emb 下 skip、平台 venv 真跑）。
- 提交前跑：`emb -m pytest research/tools -q`（T2）+ `platform/.venv/bin/python -m pytest
  research/tools/{strategies,ch_ingest,factor_lib}/tests -q`（T1）。
- 因子新增/改名/归档：**必须**同步档案（`docs/factors/<族>/<短名>.md`）并重生成索引
  `../docs/index/factors.md`（`build_index.py --check` 是常驻门）。
- 数据位置与血缘以 `../docs/data-map.md` 为唯一权威；目录约定以 `../docs/directory-conventions.md` 为准。

## 环境事实

- **解释器双轨（刻意不统一**，统一会改 polars 补丁版本、威胁 lob_fact 字节级重跑）：
  T1 = `../platform/.venv/bin/python`；T2 = `emb`（`/data/students/gaolei/anaconda3/envs/emb`）。
- CH：`127.0.0.1:8123`（HTTP；19000 是 tcp client 端口）。库 `factorlab`；对账用
  `platform/.venv/bin/python research/tools/ch_ingest/reconcile.py`（`make reconcile`）。
- 数据根：`../data/{raw,fact,calib,ref}`（单点在平台 `core.factio.paths`）；**`data/` 零改动**（只读消费）。
- 16GB 内存无页面文件（目标机）：批算单进程 + 流式 + 及时释放；`lob_fact` 校准常量与
  `fixtures/pins.sha256` 金样**不可改**（改动即让 183 测试与历史结论失效，需走再校准流程）。
- 判读口径：因子"同公式多假设"（direction/params/process 差异）是**研究变体**，不是重复条目——
  索引的「变体组」章节成组展示（R5 实测 152 个里 0 个真重复）；归档与否属研究者判断。
