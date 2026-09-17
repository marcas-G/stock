# R30 评估指标 v2 — 批 3：Task 1-4（非破坏性修订）

- 计划：`knowledge/design/platform/plans/2026-09-16-factorlab-eval-metrics-v2.md`（执行顺序第 3-4 项）
- 设计：`knowledge/design/platform/specs/2026-09-16-factorlab-eval-metrics-v2-design.md` §2 D2/D3/D4/D5
- 环境：`platform/.venv`（Python 3.13.13）
- 提交（按任务序，一次提交一棵树）：
  1. `1cf4a92 fix(eval): layered 分档改 average-rank（D2 两处一致）`
  2. `7cc4984 feat(eval): dead-signal fail-loud（R07-D6 收口）`
  3. `0ee8454 feat(eval): 方向感知胜率字段（D4）`
  4. `8805284 feat(eval): 重叠标签不重叠采样评估（D3 本质）`
  5. 本证据 README/验收日志提交（hash 见 `git log`）

## 交付内容

| 任务 | 内容 | 关键代码 |
|---|---|---|
| Task 1（D2） | layered `_group_assign` 改 average-rank 对称分位 `floor((2·avg_rank−1)·G/(2N))` + direction 感知重排，与 kernel decile 同公式；重并列同档、行序无关；唯一值且 N 整除 G 逐值不变 | `core/eval/layered.py` |
| Task 2（D5） | `signal_null_ratio ≥ 0.99` → `evaluation.dead_signal=true` 先落盘 + `DeadSignalError` 非零退出（CLI run exit 1，消息含 signal_null_ratio）；正常因子零变化；多输出逐输出判定 | `core/eval/metrics.py`、`app/evaluate.py` |
| Task 3（D4） | `ic.direction_consistent_share = P(direction×IC>0)`（append-only；raw `sign_consistent` 不动）；`list` 加 `dir_consistent=` 标注 + 读法提示，`show` 加「方向一致率」行 | `core/eval/kernel.py`、`surfaces/cli/main.py` |
| Task 4（D3） | h>5 评估日期不重叠采样（weekly ⌈h/5⌉ 周 / daily h 日）；结果 `sampling={mode:non_overlap, stride_weeks}`；`ic.t_stat_nw`= 对**未采样** IC 序列的 Bartlett NW 诊断（lag=⌊h/5⌋）；5d/1d 路径零变更 | `adapters/ic_kernel.py`、`core/eval/kernel.py` |

每个字段变更同批更新 `knowledge/contracts/interface.md` 并新增防漂移断言
（`platform/tests/test_eval_docs.py`：D2/D4/D5/D3 四条 + 旧公式回潮守卫）。

## 证据索引

| 文件 | 内容 | 命令（按原样执行） |
|---|---|---|
| `01-task1-red.txt` | Task 1 先红：5 failed（重并列组号/行序不变/kernel 对拍） | `cd platform && .venv/bin/python -m pytest tests/test_layered_groups.py -q` |
| `02-task1-green.txt` | Task 1 绿：80 passed（layered/eval/notes/docs 相关面） | 见文件首行选择集 |
| `03-task1-unique-invariance.txt` | **关键对拍**：monkeypatch 旧 ordinal 实现 vs 新实现，7 面板 × 2 方向 = 14 组 `net_values/summary/turnover/dates/empty_groups` 逐值相等 | `platform/.venv/bin/python governance/.../unique_invariance.py` |
| `04-task2-red.txt` | Task 2 先红：测试文件导入失败（`DEAD_SIGNAL_NULL_RATIO` 未实现） | `pytest tests/test_dead_signal.py -q` |
| `05-task2-green.txt` | Task 2 绿：188 passed（含 CLI run 非零退出 e2e、--set 默认死信号用例） | 见文件首行选择集 |
| `06-task3-red.txt` | Task 3 先红：7 failed（字段缺失/CLI 标注缺失） | `pytest tests/test_eval_sign_fields.py -q` |
| `07-task3-green.txt` | Task 3 绿：91 passed + 方向对拍表（base +1/+−1、signal-negated −1） | 见文件内命令 |
| `08-task4-red.txt` | Task 4 先红：3 failed（`sampling` 缺失）+ 2 passed（5d/1d 零变更锚点） | `pytest tests/test_eval_overlap_sampling.py -q` |
| `09-task4-green.txt` | Task 4 绿：157 passed（全部 eval/CLI/layered 相关面） | 见文件首行选择集 |
| `10-task4-real-data.txt` | **关键对拍（真数据 934k 行）**：5d 逐值不变；20d `n_weeks 175→44`、全周频简单 t=4.489（复现 R08 4.49）、采样后简单 t=2.527、NW(lag4)=2.673（复现 R08 2.67，相对差 5.8%） | 见文件内脚本 |
| `11-platform-fullsuite.txt` | 平台全量：**3137 passed / 11 skipped / 0 failed**（批 2 基线 3109/11 → 无回退且 +28） | `cd platform && .venv/bin/python -m pytest -q` |
| `12-gates.txt` | `make gates`：唯一失败 = **预存 G-INDEX**（挖矿在途 spec 未入索引/未归档；与批 1 `eval-v2-task0-5/00-baseline-gates.txt` 同款），其余全绿（G-LINT 196/0、G-IMPORTS 435 文件、G-VENV 内核并入断言） | `make gates` |
| `13-mutation.txt` | **突变检验**：ordinal 回退/dead 恒 False/sign 复制/无采样 四突变各自杀测试（exit 1），还原 sha256 逐字节一致且四任务用例全绿 | `platform/.venv/bin/python governance/.../mutation.py` |
| `14-test-research.txt` | `make test-research`：2 failed（`factor_lib/test_index.py`，与批 2 `eval-v2-task13-15/08-task15-tools.txt` 同款预存——挖矿在途新增 spec 未入索引/缺档案；**本批未动 research/**） | `make test-research` |

脚本：`unique_invariance.py`（Task 1 旧实现对拍）、`mutation.py`（四突变 + 还原 sha256 断言）。

## 口径要点（验收锚）

- **D2**：n=12、G=10、signal=1..6 各两只 → 手算 D 映射 {1,1,2,2,3,3,4,4,5,5,6,6} →
  D10/D10/D8/D8/D6/D6/D5/D5/D3/D3/D1/D1；旧 ordinal 实现必败（红 5）。
- **D4**：25 周（16 正 / 9 负）→ `sign_consistent=0.64`；`dir=+1 → dcs=0.64`、
  `dir=−1 → dcs=0.36`（仅翻参数）；信号取负 + 方向取负 → `dcs=0.64` 不变、
  `sign_consistent=0.36`（raw 随数据）。
- **D3**：真实 low_vol_20d 20d 标签——旧全周频简单 t=4.489 虚高 → 采样后 2.527，
  与 NW(4) 诊断 2.673 相差 5.8%（两种校正收敛）；5d 与旧路径逐键逐值一致（rel≤1e-9，
  polars 聚合非确定性 ~1e-18）。
- **D5**：200 行 198 null（0.99）→ dead；197 null（0.985）→ 不判死；CLI run
  非零退出且 `summary.evaluation.dead_signal=true` 已落盘。

## 未竟 / 风险

- **存量 20d 产物**仍为旧口径（无 `sampling`/`t_stat_nw`，t 虚高）：按 D7 由
  Task 11 重跑/清理，本批不动 `runs/`。
- **预存门失败**（G-INDEX / factor_lib 索引 2 例）系挖矿在途文件（`research/factor/
  intraday/`、`max_effect_20d_*` 等未归档/未入索引），本批按约束未动；批 1/2 已有
  同款记录。
- `sampling.stride_weeks` 为周数口径标注；daily h>5（仅扩展研究直接调桥接）实际
  步长为 h 交易日（interface 已注明；产品路径 daily 固定 1d 不触发）。
