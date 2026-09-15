# R13 原子写收成单点 + 修掉 R12 引入的权限回归（2026-09-15）

主线不变："清晰的解耦 + 可复用/可维护"。本轮把平台里**同一条原子写协议的四份实现**（外加
`execution_store` 缺的第五份）收成 `adapters/atomicio.py`，并在收的过程中抓出并修掉一个
**R12 自己引入的真回归**——产物权限。

## 1. 收口：协议实现单点

| 原实现 | 处置 |
|---|---|
| `adapters/parquet_artifacts._atomic_write`（内联 sibling-temp + `os.replace`） | 委托 `atomicio.atomic_write` |
| `adapters/strategy_artifacts._atomic_write_file`（M7-04A 版，含失败清理） | 同上（公开名与调用点不变，语义更强） |
| `adapters/results_fs.write_run_outputs`（R12 自己写的 mkstemp 版） | 同上（**并修权限**，见 §2） |
| `adapters/batch_flock._write_state` / `_write_marker`（R10 版） | 同上 |
| `adapters/execution_store.save_backtest_result`（**原先直写 10 个 parquet + manifest，无原子性**） | 全部改经单点（pending #14 的最后一处非原子写） |

单点语义：同目录 tmp（`.<name>.<rand>.tmp`）+ 文件 `fsync` + `os.replace` + **目录 fsync** +
失败清理（目标不出现、不留 tmp）+ **权限按 umask 设定**。`atomic_write(path, writer)` 形态与
M7-04A 历史签名兼容，历史调用点零改动。

残留检查：`platform/src` 里 `os.replace|mkstemp` 只剩 **`atomicio.py` 本身**
（其余命中都是注释）——"单点"不是自称。

## 2. 抓回：R12 的 mkstemp 把产物权限变成了 0600

| 项 | 内容 |
|---|---|
| 症状 | R12 之后 `results/<name>/summary.json`、`weekly.parquet` 权限 = **`-rw-------`**，而同目录由旧路径写出的 `panel/labels/signal.parquet` = `-rw-rw-r--`（umask 0002 下的正常值） |
| 根因 | `tempfile.mkstemp` 创建的文件固定 0600，`os.replace` 把 tmp 的权限**带给了产物** |
| 影响 | 单用户环境无感，但**跨用户/组共享（web 服务、同组同事、容器挂载）会直接读不到**——是潜伏的交付级问题 |
| 发现 | 收口时顺手 `ls -l` 冒烟产物（不是测试报的；测试当时没覆盖权限） |
| 处置 | 单点按 `0o666 & ~umask` 显式设权；新增测试 `test_permissions_match_plain_created_file`（与"普通写出的文件"比对权限，回归即红）；真 CH 冒烟复验：五个产物全部 `-rw-rw-r--` |

## 3. 验证（真跑）

| 项 | 结果 | 证据 |
|---|---|---|
| 平台全量（含本轮 +6 原子写测试） | **2555 passed / 13 skipped / 0 failed** | `platform-pytest.log` |
| 新单点测试 | `tests/test_atomicio.py` 6 条：bytes/text/parquet 往返、**权限与普通创建一致**、失败不留目标/tmp、覆盖写、`atomic_write(path, writer)` 兼容形态 | 同上 |
| 真 CH 端到端（`r12_smoke`：2026-06 一个月 × 4 码） | `run` 成功；**权限 0664 全部一致**；无 tmp 残留 | 本节 §3 |
| 相关既有测试 | `test_strategy_artifacts` / `test_backtest_persistence` / `test_artifact_persistence` / `test_canonical_artifact_handoff` / 执行链共 **144 条**全绿 | 同上 |

## 4. 仍未做（不静默）

- `run_lob_batch` 切 P-5（它额外需要内存低水位派单闸门与周期性审计回调两个专有缝）——
  pending #14 至此只剩这一项；
- 研究侧 `writekit` 与研究工具自身的原子写（跨树，不属本轮平台侧范围；研究侧已由
  `lib/writekit` 单点覆盖）。
