# R8 死代码 / 冗余代码清理（2026-09-15）

用户指令："继续重构，清理死代码，冗余代码"。范围 = 平台包 + 研究树 + 文档/门，
**不动数据接口契约本身**（R4 已收口），只把"同一件事多份实现/无人使用的声明"收干净。

## 1. 做了什么（按树）

### 平台树（R8a 归位 + R8b 死代码）

| 类别 | 内容 | 依据 |
|---|---|---|
| 包内归位 | `process/`（空壳）删除；`strategy/` facade 删除（核心归 `core/strategy/`，落盘在 `adapters/strategy_artifacts`）；`execution/`→`app/backtest/`；`eval/` 三分（`core/eval/` + `app/analysis/`）；`catalog.py`→`adapters/catalog.py`；`adapters/read/execution.py`→`adapters/read/market_open.py` | 分层纪律见 `platform/CLAUDE.md`；改名后 8 个测试文件同步改名对齐所测模块 |
| 死代码 | `surfaces/web/app.py` 未使用的局部 `from starlette.responses import Response`；`core/domain/execution.py` 收集后从未使用的 `bad_disp = []`（循环是首个非法值即 raise）；`adapters/strategy_artifacts.py` 三元组里未被使用的 `enum` 元素；`core/factio/boards.py` 零消费者的 `BOARDS` | vulture 100% 置信 + 逐处人工复核（协议形参/Pydantic 字段等假阳性保留并注明） |
| 静默 no-op | `core/eval/layered.py::layered_backtest` 的 `cost: float = 0.0`（签名收下、计算不用）→ **删除形参**并同步 `platform/docs/interface.md`；改成本 建模登记 `docs/pending-items.md#15` | 传 `cost=0.002` 会拿到零成本结论且无提示——比没有该参数更危险 |
| 文档同步 | `interface.md` 6 处旧模块路径（`factorlab.eval` / `.eval.cross_section` / `.catalog` / `.strategy` / `.execution`×2）改为 R8a 后的新路径 | **doc 路径门抓到**（`tests/test_doc_paths_exist.py` 红 → 修 → 绿），非人工排查 |
| 保留并标注 | `PANEL_ROOT`（`docs/data-map.md` A12 的代码侧预留区，零消费者属预期）；`ports/*.py` 协议方法形参（vulture 假阳性，已加说明） | 保留理由写在代码注释里，不让下轮再判一次 |

### 研究树（R8c 写侧/分区/兼容形参收敛）

| 类别 | Before | After | 证据 |
|---|---|---|---|
| 单写者锁 | 4 个工具各自 `open(...)+fcntl.flock`，其中 1 份实现用**裸 fd**（对象被回收也不释放 → 同进程第二次 `main()` 假"锁占用"） | 4 处统一 `W.acquire_lock`；`FileLock` 改为持文件对象（**锁生命周期 = 对象生命周期**，与历史 `lock_f = open(...)` 语义一致） | `test_writekit.py::test_acquire_lock_released_when_object_dropped`（先红后绿）+ `test_run_lob_batch.py::test_main_cli_lock_onlyday_and_finalize` |
| 完成标记 | `convert_tick`/`convert_minutes` 自拼 `_SUCCESS` 路径；`run_lob_batch` 自拼 `_batch/SUCCESS_<YYYYMM>`（带 JSON 回执） | 统一 `W.mark_success/has_success/success_marker`，**名字是参数、回执是参数**（`name=` / `payload=`），机制单点 | `test_writekit.py` 两个新用例（月级标记、回执标记） |
| 断点 state | `1m_features` 自建 `state.json` 读写 + **本地 `_atomic_write_json`/`_atomic_write_df`**；`run_lob_batch` 自建 `state.json` + tmp/replace | 全部走 `W.load_state/save_state/atomic_write_df`；本地两份原子写实现删除 | `test_1m_feature.py::test_batch_e2e_writes_sorted_month_part_and_resumes`（跑真批算 + 断点跳过 + 产物 sha 不变） |
| 分区字面量 | 研究侧 13 处自拼 `year=/month=`、2 处以 `entry[5:]` 位置切片解析目录名 | 全部取 `core.factio.partitions`；新增 `YEAR_PREFIX/MONTH_PREFIX` 常量（平台测试锁），切片偏移由前缀长度派生 | `docs/verification/R8/path-parity.txt`（90 个对照点逐字符相等，含 `compact_lob.plan_files` 真实函数产清单） |
| 绝对路径 | 两个转换器硬编码 `/data/students/gaolei/stock/...`（6 个常量） | 取 `core.factio.paths`（`convert_minutes` 补 `_env.ensure_platform()`） | `test_converters.py::test_paths_follow_overridden_stock_root`（子进程覆盖 `FACTORLAB_STOCK_ROOT`，硬编码实现必败） |
| 兼容形参 | `is_done/mark_done(state_dir_arg)`、`convert_day(out_dir)`、`day_tokens_and_links(day_fid, prefix_map)`——形参被收下却不用（文档写着"保留为兼容调用点"） | 删除形参并改全部调用点（含测试） | 研究侧全量 + T1 集合（见下） |
| 隐藏非确定性 | `1m_features` 月产物行序**跨进程不定**（同码同输入连跑两次 sha256 不同） | 落盘前 `sort(["date","code"])` → 连跑两次 sha256 一致；`merge` 本就 sort，产物口径不变 | 本目录 `status.md` §3「非确定性实测」 |
| 可测性 | `quark_download_v2` 模块顶层 `COOKIES = open("/tmp/quark_cookies.txt")`——import 期 I/O、路径不可配、机器换环境即 import 失败 | 懒读 `_cookies()` + `QUARK_COOKIE_FILE` 可配；缺 cookie 显式 `FileNotFoundError` | `test_quark_download.py::test_import_does_not_read_cookie_file`（子进程断言 import 不碰文件 + 路径跟随配置） |

### 新增测试（此前 3 个工具 0 测试）

| 工具 | 新增 | 有牙齿的点 |
|---|---|---|
| `converters/` | 7 | 240 行 minute-end 网格逐点、OHLC float64/float32 双 schema、市场判定**信 zip 目录不信代码前缀**（920xxx 事故规则）、`parse_ms` 与平台标量入口同值、路径随 `FACTORLAB_STOCK_ROOT` 迁移 |
| `quark_download/` | 4 | import 期不碰文件系统（子进程）、cookie 路径可配、缺 cookie 显式报错、cookie 首尾空白剥离 |
| `1m_features/`（T1） | 5 | `YYYY-MM` 月份口径、枚举以 part 文件为准、注入列**不跨 code 泄漏**（prev_close/adv20 组内滚动）、端到端小批算（合成事实）+ 断点跳过 + 落盘顺序 |
| `lib/writekit` | +3 | 锁生命周期、月级标记名、带 JSON 回执的标记 |
| `lob_fact/tests/test_config_paths` | +2 | `tick_month` 字符串合同（含**尾斜杠**——调用方是 `f'{...}part-*.parquet'`） |

## 2. 门（`docs/verification/R8/gates.log`）

- **新门 `scripts/check_dataiface.py`（AST 判据 + `--selftest`）**：R6 起的报告门是 grep 计数，
  把注释/docstring/报错文案/keyword 实参全算进去（`_SUCCESS` 30 处里真正写标记的只有 writekit 一处），
  计数不降也不说明问题。新门只认**代码里的字符串常量**（AST 排除 docstring）与被调方（排除 `print`/argparse 文案）：
  - ENFORCED：① 研究侧分区字面量（`year=` 只许 `partitions` 产出）② 标记路径构造（只许 `lib.writekit` 单点 API）→ **均 0**；
  - REPORT：③ 平台表名字面量 68 处（未竟 `pending-items.md#12①`）④ 研究侧直读 7 处（未竟 #13，需判"事实表 vs manifest"）。
  - 负向自检：造违规文件必被抓到、docstring/writekit API/`print` 文案不误伤（门不是死的）。
- 结构门全绿（G-COPY/G-BOUNDARY/G-LEGACY/G-PATHS/G-IMPORTS/G-INDEX/G-VENV）。

## 3. 非确定性实测（1m_features 月产物）

同代码同输入、连跑两次 `batch --only 2020-01`（各自独立 `--out`）：

| 运行 | part.parquet sha256 | 与基线排序后逐值 |
|---|---|---|
| 历史产物（2026-09-09） | `1aa0e180…` | — |
| 修前 run-a | `34cdcb94…` | `sorted equal: True`，`vwap30_bias max|Δ|=0.0` |
| 修前 run-b | `fe0445aa…` | 同上 |
| **修后 run-a / run-b** | `148d2d28…`（两次相同） | 行序也一致 |

结论：引擎输出的**行序**跨进程不定（值稳定）；`merge` 本来就 sort，故下游产物口径未变。
落盘前加 `sort(["date","code"])` 后月产物**字节可复现**（新证据：本表最后一行两跑同 sha）。

## 4. 验证（真跑，非声称）

| 项 | 结果 | 日志 |
|---|---|---|
| 研究侧全量（emb；T1 用例 skip 不假通过） | **220 passed / 3 skipped** | `research-emb.log` |
| T1 集合（平台 venv：strategies/ch_ingest/factor_lib/1m_features） | **40 passed** | `research-t1.log` |
| 平台全量（含 R8 新增分区前缀用例） | **2514 passed / 13 skipped / 0 failed** | `platform-pytest.log` |
| check-day × 真 CH（2024-01-15） | **PASSED**：交集 5249 行，`vwap30_bias`/`open30_amt_share` **max\|Δ\|=0** | `check-day.log` |
| 分区/路径对照 | **90 个对照点，0 差异** | `path-parity.txt` |
| 结构门 + 数据接口门 | 全绿（ENFORCED 两项 0 违规） | `gates.log` |
| `data/` 零改动 | 全程只读消费；`git status` 无 `data/` 条目、`df /data` 可用空间与基线同为 338G | — |

## 5. 执行中的偏差与抓回（不隐去）

| 偏差 | 怎么发现的 | 处置 |
|---|---|---|
| 删 `strategy_crash_bottom.py` 里未使用的 `import statistics` 时，替换串没带前导缩进 → 下一行 `n = len(rets)` 被并成 8 空格，**语义变成"只在 per_episode 分支里赋值"**（`py_compile` 仍通过：缩进是自洽的，只是不再是函数级语句） | 事后 `git diff` 复核 | 立即修回 4 空格；重跑 strategies 24 测试 + 研究侧全量（220/3）确认 |
| `run_lob_batch.main()` 的锁改成 `W.acquire_lock` 后，同进程第二次调用 `main()` 报"锁占用"（旧实现靠 `lock_f` 局部变量被回收释放，新实现用裸 fd 不会释放） | 研究侧全量测试 2 failed | 修 `FileLock` 为**持文件对象**（生命周期 = 对象生命周期），先写失败测试锁住该语义 |
| 首次改写后研究侧分区字面量计数从 21 → 22（新增注释里写了 `year=/month=`） | 门计数复核 | 揭示 grep 门的不可靠 → 改为 AST 门（`scripts/check_dataiface.py`），注释/docstring 不再计数 |

## 6. 未竟项（不静默）

- `pending-items.md#15` 调仓成本建模（原 `cost` 形参已删，等口径决策）；
- `pending-items.md#16` 平台 `MonthWriter` 与研究 `lib.writekit` 落盘实现合并（`MonthWriter` 三项
  独有能力来自真实事故——物理块完整性校验/追加大小单调性/亿行级流式 row-group，须并入并配回归）；
- `pending-items.md#12①/#13`（报告档两条）保持未竟登记。
