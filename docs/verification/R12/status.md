# R12 平台侧 results 单点收口（2026-09-15）

主线（用户指令）："现阶段……重要的是**代码功能结构的清晰解耦，流程的可复用性、可维护性**"。
本轮把平台侧"results/ 产物读写"这一处**绕过单点**的病收干净——同一个毛病在 app/surfaces 里
散着四处（其中一处还是"非原子写"，本来单独挂在 pending #14 上）。

## 1. 先立门（门的第一次运行就列出全部绕过点）

新架构规则 `tests/test_architecture.py::test_results_io_only_in_adapters`（AST）：
**`app/` 与 `surfaces/` 不得出现 `read_parquet/scan_parquet/write_parquet/read_csv`，
也不得出现 results 布局字面量**（`panel.parquet`/`weekly.parquet`/`labels.parquet`/
`signal.parquet`/`summary.json`）。首跑报出：

| 绕过点 | 问题 |
|---|---|
| `app/analysis/correlation.py:43` | 抽样周探针自己 `scan_parquet(.../panel.parquet)`——同文件其它地方却用 `ParquetPanelStore`（自相矛盾） |
| `surfaces/web/app.py:146` | 详情页直接 `read_parquet(weekly.parquet)` |
| `app/evaluate.publish_run:79-80` | 发布**直写** two 文件且**非原子**（崩在中途留半截 `summary.json`，而 list/show/web 都按"存在即已发布"消费） |
| `surfaces/cli/main.py:238/282` | `results_dir.glob("*/summary.json")` + 手拼 `results_dir/name/summary.json` |

## 2. 收口（单点长出新面，调用点全部改道）

`adapters/results_fs.py`（results 布局与 I/O 单点）新增：
- 布局常量 `PANEL_NAME/WEEKLY_NAME/SUMMARY_NAME` + `panel_path/weekly_path/summary_path`；
- `read_weekly(results_dir, name)`；
- **`write_run_outputs(out_dir, *, weekly, summary)`：发布单点**——tmp + `fsync` + `os.replace`
  原子落盘两个文件，失败不留目标、不留 tmp（与 `writekit`/`parquet_artifacts` 同协议）。

`adapters/panel_store.py` 新增 `load_dates(results_dir, name)`（**只读 date 列**，抽样周不再整张
panel 载入；缺失语义与 `load_panel` 一致，文案单点仍在 `ports.panel_store.panel_missing`）。

四个调用点改道：`correlation` 探针 → `load_dates`；`publish_run` → `write_run_outputs`；
`web` 两处 → `summary_path` + `read_weekly`；`cli` 两处 → `summary_path` + `list_result_dirs`。
`interface.md` 增"results 布局单点"一节（API + 门位置）。

## 3. 验证（真跑）

| 项 | 结果 | 证据 |
|---|---|---|
| 架构门（新规则）+ 单点行为测试 | **全绿**（新 `tests/test_results_single_point.py` 5 条：布局路径、read_weekly 缺失语义、**原子写失败不留目标/tmp**、`load_dates` 只读 date、缺失文案同源） | `platform-pytest.log` |
| 平台全量（最终态，含本轮 +6：单点行为 5 + 架构规则 1） | **2549 passed / 13 skipped / 0 failed** | `platform-pytest.log` |
| 首轮全量的 7 个 `test_web` 失败 | 是**中间态**（web 局部导入未提级时跑的）误报：当前代码复跑 `test_web + test_correlation + test_cli_run` = **48 passed**，全量已重跑 | §4 第一行 |
| 真 CH 端到端（`r12_smoke`：2026-06 一个月 × 4 码，`cost_rate=0.0015`） | `run` 成功；布局 = `<root>/<name>/{panel,labels,signal,weekly}.parquet + summary.json`；**无 tmp 残渣**；`list`/`show` 走新单点路径正常输出 | 本节 §4 |
| Web 冒烟（`factorlab serve` + curl） | 首页 200、详情页 200 且含 IC 曲线/分层区块（`read_weekly` 单点路径生效） | §4 |

## 4. 执行中的偏差与抓回

| 偏差 | 暴露方式 | 处置 |
|---|---|---|
| `web/app.py` 里 `results_fs` 原本是 `_load_summary` 内的**局部导入**，我给另一函数加了 `results_fs.read_weekly(...)` → 详情页 **HTTP 500（NameError）** | 两处同时抓到：① `factorlab serve` + `curl` 冒烟；② 平台全量里 **7 条 `test_web`** 失败（`test_factor_detail_*` 系列正好覆盖 weekly/相关性路径）。当时我先把全量那 7 条误读成「跑在中间态上的假报」，curl 才让我确认是真 bug | 改为模块级导入；curl 两页 200、相关 48 条测试复跑全绿；全量重跑 |
| 冒烟第一次把 `--output-dir` 指到了因子目录本身（而非 results 根），`list`/`show` 因而"查不到"——看着像回归 | 对比 `find` 出的布局才发现是调用方式问题 | 按标准 `<root>/<name>/` 复跑，`list`/`show` 正常；**这不是产品缺陷**，记录以免误判 |
| `pkill -f "factorlab serve"` 把自己的 shell 一起杀了（命令行里含同样字符串） | 复合命令中途退出、退出码异常 | 改用 `ps | grep [f]actorlab serve` 核对；无残留进程 |

## 5. 仍未做（不静默）

- `adapters/execution_store.save_*` 的原子写（pending #14 剩余）；
- `run_lob_batch` 切 P-5（它额外需要内存低水位派单闸门与周期性审计回调两个专有缝）；
- `test_e2e_web.py` 4 条因缺历史 results fixture 而 skip —— 需要一批固定产物或改造成自造 fixture
  （本轮已用 serve+curl 冒烟覆盖同一路径，但没把它固化成测试）。
