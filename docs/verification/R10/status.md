# R10 未竟项收口的后半（2026-09-15）

用户指令："2，3都做了" = 上一轮列的两件后续：② #15 的费率口径与 spec 级接线；
③ #14 的"三份编排样板切到 BatchFlock"。推送按用户指示**晚些再同步**。

## 1. ② 调仓成本：spec 级接线（#15 收口）

| 项 | 内容 |
|---|---|
| 口径（定下并写进文档） | `cost_rate` = 每单位**单边换手**的买卖总成本（费率语义，例 A 股 ≈0.0007 = 0.1% 印花税 + 双边佣金 0.005%×2 + 少量冲击）；`net_t = gross_t − cost_rate × turnover_t`；`turnover_t = 1 − |S_t∩S_{t−1}|/|S_t|`（等权、忽略漂移；首期 0、档空期 0、long_short 两腿相加） |
| spec 字段 | `FactorSpec.cost_rate: float = Field(default=0.0, ge=0.0, lt=1.0)`——缺省 0.0 与历史逐值一致；非法值（负数 / ≥1 / 非数字）**加载即拒** |
| 接线 | `app/evaluate.evaluate_run` 单输出与多输出两条路径都透传 `spec.cost_rate`；结果里回显 `cost_rate` 与 `turnover`（可审计） |
| 顺带修的 UX | spec 字段非法原先抛裸 traceback → `run` 命令与 `lint` 同款捕获 `ValueError`（pydantic 的 `ValidationError` 是其子类）打印 `错误: …` |
| 测试 | `tests/test_spec.py` +4（缺省/显式/越界/非数字）、`tests/test_layered.py` +5（R9 已有）、`tests/test_cli_run.py` +2（spec→结果回显接线、非法费率拒绝） |
| 仍待研究决策 | 费率**数值**（是否含冲击成本）由写 spec 的人定——框架只保证"给多少就收多少、且一定收" |

## 2. ③ 编排样板切到 P-5（#14 后半）

**契约先扩 5 个可选缝**（`ports/batch.BatchOrchestrator`，缺省语义完全不变）——
每一项都由真实循环的需求驱动，不是预防性设计：

| 缝 | 驱动它的真实需求 |
|---|---|
| `max_inflight` | 三份样板都限流在飞（convert_tick 的 74k future 全量提交曾 OOM 122GB） |
| `mp_context='spawn'` | 三份样板都用 spawn——**fork 会连父进程已缓冲的大表一起复制**，16GB 无页面文件的目标机直接爆 |
| `initializer`/`initargs` | worker 预热（run_lob_batch 载入只读 manifest） |
| `stall_policy='requeue'` + `stall_strikes` | 2026-08-26 死锁兜底：停滞 → 杀 worker → 未完成单元重新入队 → 重建 executor，连续 3 次才放弃 |
| `on_result(task, result_or_exc)` | "worker 算、父进程写"的数据流（converters / extract_sz 的 buffered writer + manifest 回执） |

**已采用两份样板**（自建的"进程池 + 在飞窗口 + 停滞重启重试 + 逐结果处理"整段删除）：

| 工具 | 删除的自建代码 | 验证 |
|---|---|---|
| `converters/convert_tick_to_parquet` | spawn 自愈循环（含 SIGKILL/重入队/重建 executor/限流提交/里程碑打印） | 真实 20 code/日：trades 672,170 / orders 1,040,267 / snapshots 91,129 / manifest 20 —— **内容逐值等价**（`convert_tick-bytecheck.log`） |
| `lob_fact/extract_sz_cancels` | 同款循环 | 真实 30 code/日：cancels 341,940 行 + manifest 30 —— **内容逐值等价**（`extract_sz-bytecheck.log`） |

**遗留（已登记 #14）**：`run_lob_batch` 保留自有循环——它还额外要**内存低水位派单闸门**
（16GB 无页面文件的硬约束：派单前查 `MemAvailable`）与**周期性审计回调**（runbook 取证：
`rss_audit.csv`、每 date 完结算峰值），属该工具专有面，未纳入契约；强行套用会静默丢掉这两项。

## 3. 执行中的偏差与抓回

| 偏差 | 怎么发现的 | 处置 |
|---|---|---|
| 扩展缝 `on_result` 初版把"回调成功"当成"单元成功"——worker 失败的单元被回调改写成 ok | `test_on_result_sees_every_result_including_failures` 断言 `failed == 1` 失败 | 回调**只观察**，成败仍由 worker 决定；回调自身抛错才把单元降级为 failed（`_hook` 返回错误文本） |
| 契约测试原打算用局部 `worker` 直接跑真实现 → 子进程无法 pickle | 测试报 `AttributeError: Can't get local object` | 改为**签名比对**（`inspect.signature` 必须覆盖协议全部参数）——`runtime_checkable` 只查方法名，签名漂移它抓不到 |
| fork/spawn 若照抄默认值会静默改变内存行为 | 读样板代码时发现三者都显式 `spawn` + 注释"排除 fork 变量" | 契约补 `mp_context` 缝并在实现里注明为什么（fork 复制父进程缓冲） |

## 4. 验证（真跑）

| 项 | 结果 | 日志 |
|---|---|---|
| 平台全量（含 R10 新增：编排缝 8 + spec 成本 4 + 接线 2） | **2543 passed / 13 skipped / 0 failed** | `platform-pytest.log` |
| 研究侧全量（emb） | **225 passed / 3 skipped** | `research-emb.log` |
| T1 集合（平台 venv） | **42 passed** | `research-t1.log` |
| check-day × 真 CH（2024-01-15） | **PASSED**：5249 行 `max\|Δ\|=0` | `check-day.log` |
| 两份样板的切换前后对照（真实 zip） | **内容逐值等价**（行序随完成顺序，既有性质） | 两个 `*-bytecheck.log`（脚本同目录，可复跑） |
| 结构门 + 数据接口门 | 全绿 | `gates.log` |
| `data/` 零改动 | 全程只读；两个对照的输入用**符号链接**、输出落 `/tmp` 并在结束清理 | `df /data` 与基线同为 338G 可用 |
