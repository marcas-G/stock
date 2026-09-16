# R02-I9 证据：文档/证据漂移（4 项）

对象 = 4 处 R21 归档漂移（R02 报告 §2.9）。默认 cwd = 仓库根；原始输出现场文件不改，只增说明。

| 项 | 修正 | 文件 |
|---|---|---|
| (a) quant-core 契约正文旧口径 | 按"正文不改写、勘误加头块"约定，在旧口径行**就地加指针旁注**（不删改原文）：`t_stat`/`sign_consistent` → 勘误 7a（分母=可计算周 n_ok）；`ordinal_rank` → 勘误 7b（average-rank 对称分位）；JSON 结构块后加一条 7a 指针 | `platform/docs/superpowers/specs/2026-08-26-quant-core-contract.md` |
| (b) `R21/ENG/pytest-before/after.txt` 命令含占位符、目录无 README | 新建 `R21/ENG/README.md`：**逐 finding 真实测试节点 ID** 复跑命令 + 重建整组 `-k` + 证据清单 + 计数口径（38→39）；原始输出不改 | `docs/verification/R21/ENG/README.md`（新） |
| (c) `R21/DATA/README.md` 仍称未改 app/run.py | 更新为接线后状态：`fc2858c` 已接线（`assert_no_stale_listed` 进 run 链）；`verify_wiring_run.py/txt` 标注为接线前中间态保留；run_factor 级回归由 R02-C2 补充（工作区） | `docs/verification/R21/DATA/README.md` |
| (d) `R21/EVID/README.md` 称 interface.md 零改动 | 更正归属：零改动仅指 EVID 子系统自身；R21 全轮由 `08432ab` 改 interface.md 等、`ea2ebfe` 同步 catalog.md | `docs/verification/R21/EVID/README.md` |

另（可选）：`R21/README.md` 增加 R02/R22 关系节。

## 复跑验证（命令 + 原始输出）

| 文件 | 内容 | 结果 |
|---|---|---|
| `i9-doc-diff.txt` | 4 份 tracked 文档的 git diff + 新 ENG README 说明 | 全部为增补，原文未删改 |
| `i9-rerun-eng.txt` | `R21/ENG/README.md` 每一条逐 finding 命令 + 重建整组 `-k` 的实际复跑 | 7/7/9/5/1/2/8 passed；整组 39 passed |
| `i9-doc-paths.txt` | `platform/.venv/bin/python -m pytest -q platform/tests/test_doc_paths_exist.py`（cwd=仓库根） | 2 passed |
| `i9-gates.txt` | `bash scripts/gates.sh` | 结构门全绿 + 数据接口门 ENFORCED 全绿，exit 0 |

整组 `-k` 重建口径：归档原始输出为 38 selected（27 failed/11 passed → 38 passed）；
重建表达式多出 `test_cumulative_ops_used_resolves_import_alias`（原始输出捕获后才加入
`ea2ebfe`），故今天为 39 passed；deselected 数随测试面增长（98 → 106）。

## 未解决点

- `pytest-before/after.txt` 的 `<R21 ENG new tests>` 占位符按"原始输出不改"原则保留；
  精确命令以 `R21/ENG/README.md` 为准。
- (c) 的 run_factor 级 staleness 回归由 R02-C2 agent 在 `platform/tests/test_pit_staleness.py`
  补充（截稿时工作区未提交）；本目录不复制其证据，避免越界。
