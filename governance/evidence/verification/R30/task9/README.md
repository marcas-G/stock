# R30 Task 9 证据：pan_update CLI / Makefile / 定时器 / 手册（含补链与护栏收口）

- 需求源：`.superpowers/sdd/2026-09-16-pan-data-update-plan/task-9-brief.md`（+ 任务指令的 9 条转接收口）
- 上位设计：`knowledge/design/workspace/2026-09-16-pan-data-update-design.md` §3/§4/§5/§8
- 实现：
  - `platform/tools/pan_update/cli.py`（sync|build|publish|verify|all；cookie/内存护栏/flock/
    freshness/workers/env 透传/prune/汇总与退出码）
  - `platform/tools/pan_update/stages.py`（补链：`fund_flow`、`financials`）
  - `platform/tools/pan_update/parse_fundamentals_xlsx.py`（可执行入口 `--raw-dir/--out/--prev`；
    `write_fact(prev=)`）
  - `platform/tools/pan_update/README.md`（目录映射/命令/cookie/manual/定时器/限制）
  - `governance/ops/install_pan_timer.sh`（systemd --user timer 每日 08:10；crontab 回退）
  - `Makefile`（`data-update`：`FACTORLAB_MAX_MEMORY=8GB … cli.py all`）
- 测试：`platform/tools/pan_update/tests/test_cli.py`（32，离线：fake listdir/transport/runner）

## 复现命令与结果

| 证据 | 命令 | 结果 |
|---|---|---|
| `01-red-cli.txt` | `platform/.venv/bin/python -m pytest platform/tools/pan_update/tests/test_cli.py -q`（实现前） | **ImportError**: cannot import name 'cli'（红·功能缺失） |
| `01b-red-parse.txt` | `fp.main(["--raw-dir",…,"--prev",…])`（实现前） | argparse `unrecognized arguments: --raw-dir … --prev`，SystemExit 2（红） |
| `02-green-panupdate.txt` | 同 01（实现后，整个 pan_update 套件） | **130 passed** |
| `03-green-cli.txt` | 同 01 | **32 passed** |
| `04-green-tools-suite.txt` | `platform/.venv/bin/python -m pytest platform/tools -q` | **502 passed**（T8 基线 470 + 32） |
| `05-gates.txt` | `make gates` | G-TOPO 0 处 + 自检绿、G-READ 0 违规、G-IMPORTS 441 文件通过、G-LEGACY 零残留；**唯一失败 G-INDEX 因子索引属挖矿在途（会话开始即存在，见 T8 报告）** |
| `06-mutation.txt` | `platform/.venv/bin/python governance/evidence/verification/R30/task9/mutation.py` | **27/27 突变被抓**；恢复逐字节一致；恢复后 rc=0 |
| `07-platform-suite.txt` | `cd platform && .venv/bin/python -m pytest -q` | **3257 passed / 13 skipped**（与 T7/T8 基线一致，无回归） |
| `08-smoke.txt` | `cli.py --help` / `sync --categories bogus`（rc=2）/ `make -n data-update`（含 8GB 护栏）/ `bash -n install_pan_timer.sh` / `install_pan_timer.sh status`（rc=0） | 全通过；本机 `systemctl --user` 可用（status 读到 0 timers），crontab 回退路径由单测覆盖 |

## 突变清单（27，全部 CAUGHT）

workers 忽略/恒 1、dry-run 仍真下载、闩锁失效（不清/清语句删除）、state_path 不传、
manual 当失败、failed 被吞、未知类别不校验、跳过类别目录 find_dir、缺省 listdir 空树存根、
cookie exit 1/不检查、未设内存上限也落 RLIMIT_AS、env 白名单清空、publish 换 phase 重放链、
verify 退出码吞掉/带表名参数、all 不跑 verify、fund_flow 链缺失、financials 顺序颠倒、
parse `--raw-dir`/`--prev` 忽略、fact 不轮换、Makefile 去护栏/只 sync、timer 08:10→09:10、
crontab 行时间错。

## T9 裁决（brief 未逐字覆盖处；写入 cli docstring/README/报告）

1. **build 与 publish 共用阶段标记**（phase="build"）：T5–T8 为每类别收口一条完整链
   （含 CH 灌入=发布面）；T4 转接项明确「T9 应在同一 phase 下跑完整链」，故 publish 是
   等价动词（幂等跳过），`all` 顺序 = sync → build → publish(跳过) → verify，不重放链。
2. **cookie 检查作用域**：仅 `sync`/`all`（触网命令）启动校验；`build`/`publish`/`verify`
   离线可跑（T10 手工恢复场景需要）。
3. **--prune 语义（最小实现）**：删除本地 raw 中不在本次分享清单内的残留；
   日K 旧全量快照轮换（设计 §4）留待后续/T10，README 有限制声明。
4. **verify 不做阶段标记**：reconcile 是全局（非按类别）对账，每次 all 重跑（只读，无副作用）。

## 限制/残留

- 本任务不真跑网盘/CH（T10）：全部单测离线；真实 listdir/取链/灌入留 T10。
- `make gates` 唯一失败（G-INDEX）为挖矿在途文件未重生成索引，与 T9 无关（T8 同记录）。
- 本机 `systemctl --user` 可用，但 T9 未执行真实 `install`（无必要副作用）；T10/运维按 README 安装。
