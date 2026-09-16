# R30 Task 4 证据：阶段编排 `stages.py`（续跑/失败上抛/单实例锁/日志）

- 需求源：`.superpowers/sdd/2026-09-16-pan-data-update-plan/task-4-brief.md`
- 上位设计：`knowledge/design/workspace/2026-09-16-pan-data-update-design.md` §3/§5/§8
- 代码：`platform/tools/pan_update/stages.py`、`platform/tools/pan_update/tests/test_stages.py`

## 复现命令与结果

| 证据 | 命令 | 结果 |
|---|---|---|
| `01-red.txt` | 暂移 `stages.py` 后 `platform/.venv/bin/python -m pytest platform/tools/pan_update/tests/test_stages.py -q` | 收集失败 `ImportError: cannot import name 'stages'`（功能缺失，红） |
| `02-green.txt` | `platform/.venv/bin/python -m pytest platform/tools/pan_update/tests -v` | **55 passed**（T1-T3 40 + stages 15：brief 2 条逐字 + 守卫 13 条） |
| `03-gtopo.txt` | `platform/.venv/bin/python governance/ops/check_tool_layering.py` | **0 处**（工具拓扑：不跨工具 import） |
| `04-mutation.txt` | `python3 governance/evidence/verification/R30/task4/mutation.py` | **19/19 突变被测试抓住**；恢复原文后 55 passed |
| `05-tools-suite.txt` | `platform/.venv/bin/python -m pytest platform/tools -q` | **399 passed**（T3 基线 375 + 新增；含在途轮次增量） |

## 行为要点（测试锁死）

- brief 逐字断言：`STAGE_CHAINS["toy"]` 两步依序跑；同 (类别, 阶段) 第二次 no-op；
  `s["stages"]["toy"]["build"]` 落标记；runner 抛 `StageError` 原样上抛。
- 失败语义：命令失败即停（后续命令不跑）、(类别, 阶段) 标记不动；修好后重跑整链重放、成功才落标记
  （链内每步幂等由设计 §3 保证）。
- 记账语义：`state["stages"][category][phase]` 为 ISO 时间戳；同类别不同 phase 各自独立标记。
- 未配置类别 → `KeyError`（显式失败，不静默空跑）。
- `run_cmd`：真实子进程（`sys.executable`），输出逐行喂 `log`；非零退出
  `StageError(stage, cmd, rc, tail)`，tail=末 20 行；env 合并 `os.environ` 且调用方覆盖；
  Popen `OSError`（可执行不存在）→ `StageError(rc=-1)`；stage 名取命令中首个 `.py` 的 stem。
- `single_instance`：`fcntl.flock(LOCK_EX|LOCK_NB)`；被占 → 立即 `RuntimeError`（含锁路径与持锁 pid）；
  退出释放可重入；父目录不存在自动建；跨进程真实持锁测试（子进程 flock + sleep）。
- `open_log`：`runs/platform/logs/pan_update-YYYYMMDD.log`（设计 §5 路径），追加不截断，
  行前缀 `[category]`；接受 `datetime.date`/ISO 串，`log_dir=` 可注入（测试用 tmp_path）。

## 接口与裁决（brief 未逐字覆盖处）

1. `run_category_stage(..., *, runner=run_cmd)` 追加可选 `log=None` / `env=None`：
   `runner` 以 `runner(cmd, log=log, env=env)` 调用（与 brief 测试的 `runner(cmd, log, env=None)` 兼容）；
   `log` 缺省为 no-op，T9 传 `open_log(...)` 返回值接生产日志；`env` 供内存护栏等透传。
2. `phase` 是记账键：成功后写 `state["stages"][category][phase]`；同 phase 幂等跳过，
   不同 phase 独立标记（T9 `build/publish` 调用约定自定；链内容由 T5/T6 填实）。
3. `STAGE_CHAINS` 初始为 `{}`（空 dict），由 T5/T6/T7/T8 按类别填实；未配置类别显式报错。
4. `StageError` 保留 `.stage/.cmd/.rc/.tail` 字段；`cmd` 拷贝为 list，便于调用方归因。
5. `tail` 粒度 = 行（末 `TAIL_LINES=20` 行），由测试断言“含末行、不含首行、行数有界”。

## 突变（`mutation.py`，19 个）

`run_category_stage`：链不跑只标标记 / 忽略标记 / 先标后跑 / 吞失败继续 / unknown 静默空链；
`single_instance`：不 flock / 恒拒绝 / 不建父目录 / 报错不含路径 / 去 `LOCK_NB`（阻塞）；
`run_cmd`：忽略 rc / 丢 rc / tail 不截断 / 忽略 env / 输出不喂 log / stage 恒空 / Popen 错不转 StageError；
`open_log`：截断重写 / 无类别前缀。全部被 `test_stages.py` 抓住。

说明：初版含一个"退出不释放锁（`finally: pass`）"突变，在 CPython 引用计数下与显式 close 等价
（生成器终结即回收 fh）而未被抓住；已替换为三个可观察突变（不建父目录/报错不含路径/去 LOCK_NB）。
`去 LOCK_NB` 以免死锁的线程+超时断言被抓住（阻塞等待 3s 超时判失败），harness 另设 120s 超时兜底。
