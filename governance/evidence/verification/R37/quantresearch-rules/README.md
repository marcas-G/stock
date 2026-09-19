# R37 · quantresearch 目录公约与现状整理（Phase 1）

范围：`/data/students/gaolei/quantresearch`（工具仓之外的研究产物区）。
本批只动 quantresearch、`governance/ops`、测试与本证据；主仓 `research/`、`knowledge/` 内容、
挖矿在途文件、平台全量均未动。

## 交付

| 项 | 位置 |
|---|---|
| 目录公约（正本） | `quantresearch/CONVENTIONS.md`；仓内冻结副本见本目录 `CONVENTIONS.md` |
| 检查器 | `governance/ops/research_tidy.py`（只报告，无 `--fix`）+ `governance/ops/tests/test_research_tidy.py`（13 用例） |
| 现状整理 | 17 个根散落 `*.py` → `scratch/`（按文件 mtime 加 `YYYYMMDD_` 前缀，原名保留）；根/`lab` 的 `__pycache__` 删除；`results/` 28 个散文件 → 11 个 campaign（27 移动 + 1 在途保留），每组 `README.md` + `manifest.json`（含输入/口径/平台 commit/时间） |

## 命令与原始输出

| 文件 | 内容 |
|---|---|
| `00-stock-commit.txt` | 整理时工具仓 HEAD（写入各 manifest 的 `platform_commit`） |
| `01-before-{tree,ls-R,du}.txt` | 整理前目录清单（移动前留痕） |
| `02-inflight-{processes,fd-bw-log}.txt` | 在途 bw3.py 池与 `results/bw.log` fd 证据 |
| `04-group-results.txt` | 归组输出：`moved_total=27 leftover_files=['bw.log']` |
| `05-tests-red.txt` | TDD 红：`ModuleNotFoundError: No module named 'research_tidy'` |
| `06-tests-green.txt` | TDD 绿：13 passed |
| `07-mutation-kill.txt` | 突变（`findings` 恒空）：6 failed（恒通过必败） |
| `08-tidy-run-real.txt` | 真跑现目录：`errors=1 warnings=0`（唯一 error=在途 `results/bw.log`） |
| `09-ops-tests-full.txt` | `pytest governance/ops -q`：111 passed（无回归） |
| `10-after-{tree,ls-R}.txt` | 整理后目录清单 |
| `11-file-conservation.txt` | 守恒核查：28→50（27 移 + 1 在途 + 11 README + 11 manifest），17 脚本无丢失 |
| `12-frozen-copy-cmp.txt` | 正本与仓内冻结副本逐字节一致（`cmp` identical） |
| `gates-before/after.txt` + `gates-diff.txt` | `make gates` 前后逐字节相同：唯一红 = G-INDEX（挖矿在途，非本批引入） |

检查器默认 root：`--root` > `QUANTRESEARCH_ROOT` > `/data/students/gaolei/quantresearch`；
root 不存在打印 SKIP 并 exit 0；warning（`__pycache__`/`--allow-missing-manifest`）不改退出码。

## 偏差与未竟

- `results/bw.log` 为在途产物（bw3.py 进程池仍持有 fd），按"不动在途文件"保留原位，
  故 tidy 真跑保留 1 个 error；该进程完成后应移入 `results/backward/`。
- 归组后历史脚本内的相对路径（如 `results/audit_v1.json`）仍指向旧位置；脚本已按公约归档到
  `scratch/` 且内容未改，复跑须按 manifest 记录重新指参。
- quantresearch 无 git 仓库，公约正本无法在该树提交；本目录 `CONVENTIONS.md` 为冻结副本，
  正本与副本内容一致（`cmp` 可验）。若后续在该目录建版本控制，先按公约 §1 忽略 `data/cache`。
- Phase 2（`factor/`、`strategy/`、`dossiers/`、`index/`、`evidence/` 迁入与主仓瘦身）未开始。
