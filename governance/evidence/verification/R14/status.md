# R14 三份编排样板全部切换（P-5 收口）（2026-09-15）

至此 `ports/batch.py`（P-5）的声明**全部兑现**：三份自建编排循环（converters、extract_sz_cancels、
run_lob_batch）都收敛到 `adapters/batch_flock.BatchFlock`，各自那份"spawn 池 + 在飞窗口 +
停滞重启 + 逐结果处理"的代码删除。`run_lob_batch` 是最后一块，因为它额外要**内存闸门**与
**周期审计**——本轮把这两条（外加观测池的第三条）做成契约的可选缝。

## 1. 契约再补三条缝（都由 run_lob_batch 的真实需求驱动）

| 缝 | 语义 | 对应工具里的东西 |
|---|---|---|
| `throttle()` | 派单闸门：返回 False → 本轮不派新单，等下一 tick 再试 | `_mem_avail_kb() < LOW_WATER_KB`（16GB 无页面文件目标机的内存纪律） |
| `on_tick` / `on_tick_s` | 等结果期间的周期回调 | 每 `AUDIT_S` 记一笔 `rss_audit.csv`（runbook 取证） |
| `pool_hook(pool)` | 池一创建（含停滞重建后的新池）就回调 | 审计要按 worker pid 读 RSS——池原先在循环里是局部变量，观测不到 |

同时定契约细节：**派单 FIFO（任务序）**（原先实现是 `pop()` 的 LIFO；历史工具靠预反转列表拿
正序，是个隐性约定，现改为契约明说）；**停滞判定按"距上次完成的时间"**——周期回调会把单次等待
切短，按"这一次没等到"判会误报（实现时实测：0.2s tick 立刻触发 strikes）。

## 2. run_lob_batch 切换（本文件 139 行变化：+56 / −83）

删除：spawn 池自建、`MAX_INFLIGHT` 在飞窗口、内存低水位派单、900s 停滞杀 worker 退队列重试、
`last_act/last_aud` 记账、每 date 的三分类打印与审计调用。改为：
`BatchFlock().run(tasks, _run_one_date, workers=…, stall_s=STALL_S, mp_context='spawn',
max_inflight=MAX_INFLIGHT, initializer=_init_worker, initargs=(cfg,), stall_policy='requeue',
stall_strikes=3, throttle=_mem_gate, on_tick=audit, on_tick_s=AUDIT_S, pool_hook=…,
on_result=on_result)`——`day_rows.jsonl` 写入、ok/hard/fail 三分类、每 date 收尾审计都在
`on_result`（父进程侧），与原来逐行等价。

## 3. 执行中的偏差与抓回（本轮两次，都是"真跑"暴露）

| 偏差 | 暴露方式 | 处置 |
|---|---|---|
| **`run_lob_batch.py` 根本不能被直接执行**：文件头部的 `sys.path.insert` 写在 **docstring 里**（是示例文本，不生效）→ `from core.config import GATE_PRES` 直接 `ModuleNotFoundError`。测试从没抓到，因为测试走 in-process 导入（conftest 已铺路） | R14 真数据对照跑基线时 `ModuleNotFoundError` | 在 docstring 之后补**真正的**自举（`sys.path` 插 `lob_fact/` 与 `tools/`）；`--dry-run` 直接执行验证通过。这是"单元测试全绿但工具跑不起来"的又一例（与 R8c 漏 import、R9 诊断路径崩溃同类） |
| 批量替换的锚点写成 `def process_date(d):` 而实际签名是 `def process_date(day):` → 新函数**静默没插进去**，e2e 报 `NameError: _run_one_date` | e2e 测试 | 补插并加断言；教训再记一次：**每次替换必须断言命中**（本轮已有第三起同类） |
| `audit()` 仍引用已删掉的局部 `ex` → 单元被记失败、审计 CSV 空 | lob_fact 测试（`rss_audit.csv` 断言） | 审计改读 `pool_hook` 交回来的当前池；并在起批前先记一笔（保持"首轮必 audit"的原语义） |

## 4. 验证（真跑）

| 项 | 结果 | 证据 |
|---|---|---|
| lob_fact 全量（含金样 pins 与 e2e mini-month） | **185 passed** | `research-emb.log` |
| 研究侧全量（emb） | **231 passed / 3 skipped** | 同上 |
| **真数据切换前后对照**（真实 tick 输入，`--month 202608 --only-day 20260803`，300 只票 / 52,414,821 行；两独立 LOB 输出根；基线 = HEAD checkout + 仅补自举） | **3 个 parquet sha256 全等（含行序）**，两侧均 `OK codes=300 rows=52414821 fail=0`，state.json 均生成，审计 CSV 非空（83 / 78 行，条数随计时抖动，属预期） | `lob-batch-bytecheck.log` |
| 平台全量（契约三条缝 + FIFO + 停滞判定改动） | **2559 passed / 13 skipped / 0 failed** | `platform-pytest.log` |
| 直接可执行性 | `python pipeline/run_lob_batch.py --month 202608 --dry-run` 正常输出计划（修复前是 ModuleNotFoundError） | 本节 §3 |

## 5. 仍未做（不静默）

- 研究侧 `sys.path` 样板的统一（R11 已登记：多数是入口脚本的合法自举，统一形态牵动每个工具的
  import 方式，风险 > 收益）；
- `test_e2e_web.py` 4 条仍需历史 results fixture（R12 登记）。
