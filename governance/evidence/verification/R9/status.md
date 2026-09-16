# R9 未竟项收口（2026-09-15）

用户指令："先解决问题2" = 上一轮列的未竟项（`docs/pending-items.md` #12①/#13/#14/#15/#16）。
本轮完成 #13、#16、#15（函数级）、#14（平台侧），#12① 按登记判断保持未竟（理由见 §4）。

## 1. 逐项结果

| 项 | 结果 | 证据 |
|---|---|---|
| **#13 G-READ 转强制** | ✅ AST 判据落地：取直读目标表达式——① 硬规则：目标含 `tick_fact`/`lob_fact`/`bars_1m`/`year=`/`month=` 即违规（无豁免）② 登记制：6 个直读点写进白名单并注明理由，**新增未登记失败、登记点消失也失败**（白名单不腐） | `scripts/check_dataiface.py::check_g_read` + `--selftest`（造违规必抓、登记项不误伤）；`R9/gates.log` |
| **#16 MonthWriter 并入 writekit** | ✅ `MonthWriter` 移入 `research/tools/lib/writekit.py`（研究侧唯一写模块），三项事故能力逐字保留；新增 `blocks_complete()` 判据（稀疏文件判 False）与 3 条**事故模式**测试；**顺带修掉诊断路径自身的崩溃**（`sorted(os.listdir('/proc'), key=int)` 遇 `/proc/fb` 先炸，会掩盖原始错误） | `R9/monthwriter-move-bytecheck.log`；`research/tools/lib/tests/test_writekit.py`（18 passed） |
| **#15 调仓成本**（函数级） | ✅ `layered_backtest(..., cost_rate=0.0)`：`net = gross − cost_rate × turnover`，`turnover = 1 − |S_t∩S_{t−1}|/|S_t|`（等权、首期 0、档空期 0、long_short 两腿相加），返回值披露 `cost_rate`/`turnover`；默认 0.0 与历史逐值一致。**待研究决策**：费率数值口径 + spec 级接线 | `tests/test_layered.py`（17 passed，含"静态面板换手=0→费率不起作用""轮换面板可手算""费率单调""空档期不收费"）；`platform/docs/interface.md` §layered_backtest |
| **#14 batch_flock**（平台侧） | ✅ `adapters/batch_flock.py` 兑现 `ports/batch.py` 声明（No Orphan 缺口闭合）：契约与 `InlineOrchestrator` 对齐 + 四条真实现语义——锁占用**零执行**、断点保留既有 key、停滞看门狗按时收敛并 SIGKILL 回收、有失败**不落** `_SUCCESS` | `tests/test_batch_flock.py`（8 passed；含并行性实测、空批仍落标记）；**工具切换未做**——见 §4 |
| **#12① 平台表名 460 处** | 保持未竟（登记判断：风险 > 收益） | `check_dataiface.py` REPORT 现以 AST 口径报 **68 处**（原 grep 口径不区分 SQL 子串），指向 #12① |

## 2. 本轮抓到的真问题（R8c 遗留）

`convert_tick_to_parquet.py` 在 R8c 把 flock 收敛到 `lib.writekit` 时**漏了 `from lib import writekit as W`**：
模块能 import、纯函数与路径测试全绿，但 `main()` 一跑就 `NameError`——**已提交到 HEAD**（R9 实测复现）。
三件事一起兜住这类"接线漏了但测试看不到"：

1. **端到端测试**（新增 `research/tools/converters/tests/test_convert_tick_e2e.py`）：合成最小原始 zip
   把 `main()` 真跑起来（CLI → spawn worker → parquet → `_SUCCESS` → manifest），并断言重跑跳过；
2. **门规则**：`check_dataiface.py` 新增"用了 `W.` 却没导入 writekit"检查（ENFORCED + 自检）；
3. **交付前真跑**：`main()` 级端到端（不是只看单元测试）。

## 3. MonthWriter 搬家的字节级对照（#16）

同一天（20250812）、同一批 20 个 code 的**真实原始 zip**，两个独立输出根：

| 对象 | 结果 |
|---|---|
| 4 个 parquet 文件 sha256 | 搬家前 vs 搬家后**不同** |
| trades / orders / snapshots / manifest | **全列排序后逐值相等**（内容等价；manifest 内容亦相同） |
| 行序来源 | 多进程 worker **完成顺序**决定 append 次序 |
| 是否本次引入 | **否**：基线代码（HEAD + 补漏 import）**自身连跑两次**也 sha256 不同、全列排序后相等 |

→ 内容等价已证；"字节不可复现"是该工具的既有性质（与 1m_features 那次不同：这里月产物是流式
append，排序需整月驻留），已登记 `pending-items.md#17`。

## 4. 未竟项（不静默）

- **#14 后半**：把三份编排样板（converters / extract_sz_cancels / run_lob_batch）切到 `BatchFlock`。
  未做原因（登记原文的口径）：它们的 worker **把大表回传主进程**写（converters/extract_sz）或自带
  月门/manifest/审计 jsonl（run_lob_batch），切换要先改数据流（worker 自己落盘）再逐工具**真实批算 +
  字节级重跑对照**——属专项轮次，不塞进本轮。
- **#15 后半**：费率数值口径（是否含冲击成本）与 spec 级接线（写进因子/策略 spec）待研究决策。
- **#12①**：平台表名字面量（AST 口径 68 处）与 460 处 SQL 内字面量单点化——按登记判断需与
  位级门 + 全库 reconcile 配套，风险 > 收益。

## 5. 验证（真跑）

| 项 | 结果 | 日志 |
|---|---|---|
| 平台全量（含 R9 新增：批算编排 8 + 成本 5） | **2527 passed / 13 skipped / 0 failed** | `platform-pytest.log` |
| 研究侧全量（emb） | **225 passed / 3 skipped** | `research-emb.log` |
| T1 集合（平台 venv） | **42 passed** | `research-t1.log` |
| check-day × 真 CH（2024-01-15） | **PASSED**：5249 行 `max\|Δ\|=0` | `check-day.log` |
| 结构门 + 数据接口门（4 条 ENFORCED + 自检） | 全绿 | `gates.log` |
| `data/` 零改动 | 全程只读；临时输出一律落 `/tmp` 并在结束清理 | `df /data` 与基线同为 338G 可用 |

## 6. 本轮新增测试

平台：`test_batch_flock.py` 8（契约 + 锁/断点/看门狗/标记四语义 + 并行性）、`test_layered.py` +5（成本）；
研究：`test_convert_tick_e2e.py` 2（main() 端到端 + 重跑跳过）、`test_writekit.py` +3（月写入器三项事故能力）、
`test_ch_ingest_layout.py` +2（daily 源路径单点，子进程换根验证）。
