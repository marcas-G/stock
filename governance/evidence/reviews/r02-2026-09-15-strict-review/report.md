# R02 严格 Review 报告（2026-09-15 第二轮）

- **对象**：R21 修复（起始 HEAD `dd01bd9`；评审期间开发团队已提交，HEAD 现 `9a5c3d3`）+ R01 未覆盖新区
- **方法**：4 路并行审查（① 平台 R21 修复 ② 工具链 R21 修复 ③ 新区深挖：分钟链/process/池掩码/批算 ④ 证据与遗留审计）+
  coordinator 对全部新 Critical 亲自复核（标【实测】）
- **总评**：R21 修复**整体可信**（63 行几乎全部有代码+测试+证据，未发现伪造）；**新发现 2 Critical + 10 Important**；
  台账头部统计与 quant-core 契约文档仍有漂移。

---

## §0 协调者复核（实测）

| 复核项 | 命令/方法 | 结果 |
|---|---|---|
| R21 已提交 | `git log --oneline -8` | HEAD `9a5c3d3`；含平台/工具/文档/证据/台账回填共 8+ 提交 |
| 台账回填 | `grep` 统计 | 63 行 `fixed-claimed`（13C+50I），头部保留 11/37 并加口径说明（append-only） |
| 分钟门旁路 | 复跑 reviewer probe11 | `day_sum(im_delay(close, 2**2-5))` == `im_delay(close,-1)`（用未来分钟）；`im_mean(close, 2**0-1)` 窗口 0 静默输出 0.0 |
| industry 覆盖率 | CH 查询 | `stock_basic` industry NULL/空 = **5861/5861（100%）** |
| staleness 短窗机制 | 读 `app/run.py:100-140` + `read/staleness.py` | gate 在 fill-seed 之前、以面板窗口为基准；docstring 自认"窗口 <250 交易日不触发" → 短窗/分块可绕过 |
| universe_stages T2 裸跑 | `env -u PYTHONPATH emb/bin/python run_layer3_tick.py --help` | **exit 1**，`ModuleNotFoundError: No module named 'factorlab'` |

---

## §1 R21 复核结论（对 R01 63 行）

**总体**：证据可信、修复真实、测试非空转；独立复跑与团队声称一致（平台定向 51/67/211/210/40 passed；T2 268 passed / 10 skipped；
定点数据验收：600519 单位=1.688e8 万元与 0.4410%、300842 pre_close=50.07、2025 年 4,925/4,925 除权日公式一致、退市 5 假 code 0 行 600811 归位 7,598 行）。

**例外（reopened / partial，需继续修）**：

| R01 ID | 判定 | 原因 → 联动新发现 |
|---|---|---|
| R01-DATA-C1 | **reopened** | staleness 判定窗口依赖；短窗/分块 + 无 delist_date 的库仍会 forward-fill 死价格 → **R02-C2** |
| R01-TOOLS-I9 | **partial** | universe_stages 三 CLI 在 T2 裸跑 import 崩溃（preflight 根本没执行）→ **R02-I6a** |
| R01-TOOLS-I3 | **partial** | `import_daily.main()` 解析错误不改变退出码（新引入的 code 冲突分支命中后 exit 0）→ **R02-I6b** |
| R01-ENG-I3 | **partial** | 插件经 import 别名/动态名字时，副作用先执行、registry 被覆盖且无回滚（字面装饰器形态已堵）→ **R02-I7** |
| R01-TOOLS-I5 | **partial** | 改 fail-open（缺 stk_limit=无限制）后，平台侧无 CH 覆盖率门兜底 |
| R01-EVAL-I7/I8 | **partial** | 实现已改（n_ok 分母 / average-rank），但 `2026-08-26-quant-core-contract.md:90,93` 未同步、无勘误头 |
| R01-EVID-C2 / R01-STRAT-I6 | 按"标注不可复现"闭环 | 原始输出永缺/重跑仍不可复现（已如实标注，残余风险保留） |

---

## §2 新发现

### Critical

**R02-C1（分钟未来门旁路）【实测】**
- 位置：`core/engine/minute_gate.py:32-62`（`_try_num` 不折叠 Pow/IfExp/Call）、`:88-91`（`_call_names` 不解析 import alias）、`:103-127`（仅在可折叠且 k/w 非 None 时拒绝）；`core/ops/minute_ops.py:25-27,62-64`（**无运行时校验**）；`tests/test_minute_gate.py:128,139-144` 注释声称"运行时另层兜底"——不存在。
- 复现（coordinator 亲跑）：`day_sum(im_delay(close, 2 ** 2 - 5))` 过门且输出 == `im_delay(close, -1)`（用了"未来"分钟，折日）；`day_sum(im_mean(close, 2 ** 0 - 1))` 窗口 0 静默输出 `0.0`；别名 `from ...minute_ops import im_delay as imd` 同样绕过。跨日不泄漏（`.over(["code","date"])` 限日内），但分钟面唯一防线名不副实。
- 修法：`im_*` 运行时硬校验（k>=1、window>=1、int 且拒 bool）作为最后防线；门解析 alias；`_try_num` 折叠 Pow；删掉测试中的错误注释。

**R02-C2（staleness 短窗/分块洞）**
- 位置：`app/run.py:178`（gate 在 seed 前、只看面板窗口）→ `run.py:185` `_inject_fill_state_seed`（取窗口前最后价格）→ `core/engine/compute.py:321` `fill_null(forward)`；`adapters/read/staleness.py` docstring 自认窗口 <250 交易日不触发。
- 证据（probe）：同一退市 fixture，300 日窗口 gate fired；**240 日窗口 OK 且死价格 14.0 被 fill 到 2024-02**；30 日分块同。
- 影响：对未灌 delist_date 的库（gate 存在的理由），短窗/分块长跑仍产出死价格截面；full 失败 / chunk 成功，破坏分块承诺。
- 修法：staleness 判定窗口无关（全历史 last non-null close 日期或 seed 返回日期；超阈值 code 拒绝 seed），补 240 日/30d 分块回归。

### Important

1. **R02-I1 industry 100% NULL 静默退化【实测】**：CH `stock_basic.industry` 5861/5861 空；`adapters/process_ops.py:162-169` `fillna(industry_mean)` join 全 null → `.over(["date","industry"])` 变单组全市场均值（实测回填 34.6667）；`gp_rank/gp_mean(industry,…)` 同塌成单组。`neutralize(by=industry)` 反而 loud fail。建议：属性/process 加覆盖率 gate；`fillna(industry_mean)` 报错；catalog/interface 明示"恒 NULL"。
2. **R02-I2 process 链无有限值门**：`process_ops.py:100-145` 不处理 ±Inf；一个 `inf` 经 `standardize()` 毒化整日截面（9 个有限值全 NaN），接 `winsorize` 后整帧全 null。建议处理器统一"非有限→null"或 artifact 边界校验。
3. **R02-I3 BatchFlock throttle 永久关闭 → 看门狗失效挂死**：`adapters/batch_flock.py:175-191` 无 future 且 throttle=False 时只 sleep+continue，到不了 stall 判定；probe `stall_s=1` 12s 超时。生产 `run_lob_batch._mem_gate` 即该 throttle（内存不回升时语义完全失效，与 docstring 相反）。建议无 future 分支也记账 stall。
4. **R02-I4 adv20 左窗按日历交易日**：`app/run.py:585-603,659-664` 固定 `spec.start−20` 交易日窗口；长停牌股窗口内行情行 <20 → adv20 恒 null（契约：20 个**有行情**交易日均值）。
5. **R02-I5 240 网格断言不查 index 范围/session_type**：`core/engine/minute.py:98-108` 只查行数与唯一性；index 1..240、session_type 全 7 均被接受（契约 0..239、0/1/2）。
6. **R02-I6 工具链两处未闭合**：(a) `universe_paths.py:19` 模块级 import factorlab 而三 CLI 无 `_env.ensure_platform()` → T2 裸跑崩溃【实测】；测试用 PYTHONPATH 注入掩盖；(b) `import_daily.py:178-189` 有非空 code 却零解析时静默回退文件名；`:354-360` 解析错误不改退出码。
7. **R02-I7 插件别名/动态导入副作用先于拒绝**：`adapters/plugins.py:79-109` 不解析别名；`:155-170` 动态形态 import 后才发现冲突，副作用已执行、builtin 已被替换、无回滚（probe：alias 形态 registry poisoned）。
8. **R02-I8 run 分块路径无并发互斥**：`app/run.py:424-474` 单进程顺序循环，不用 BatchFlock；两个 `factorlab run` 并发写同一 output_dir 无锁；崩溃在 signal 落盘后 → 新 signal+旧 labels/summary 混合（与 M8-I1 同类，建议升 C 并接 staging/lock）。
9. **R02-I9 文档/证据漂移**：quant-core 契约未同步（n_ok/average-rank）；`R21/ENG/pytest-before/after.txt` 命令含占位符（不可按原样复现，8 目录唯一无 README）；`R21/DATA/README.md` 仍称"未改 app/run.py"（实际已接线，接线无 run_factor 级测试）；`EVID/README` 称 interface.md 零改动（实际已改）。

### Minor（择要）

- 平台：`rebuild.py:35-42` 只捕 JSONDecodeError（UnicodeDecodeError 冒泡）；`rebuild.py:678-684` integrity skipped 静默过；weekly_ic MIN_STOCKS=3 vs kernel 2 残留双口径；staleness 对合法长停牌误伤（文档化）；CLI lint 编程调用依赖组回调注册；插件扫描仍可 `globals()['__builtins__']` 绕过（非沙箱已声明）。
- 工具：ch_ingest README 断点文档旧形态；ddl 注释与实现不符；止损退出仍无卖出成本（C2 未覆盖该路径）；tick 读跨日守卫依赖目录名；converters symlink 守卫；5 只真退市 code 现存 0 行（历史缺口待登记）；`strategy_long_backtest` limit_down 参数未用。
- 新区：`cs_stable_rank` canonical 裸名 NameError（extra_codes 只 import cs_mean/cs_rank）；分钟 scope 接受 `close[1]` 下标糖（无跨日泄漏但穿透 B3.2）；`cs_resid` 共线输入 vendor panic 文案不干净；显式 `universe.codes` 未知 symbol 仍静默取交（R01 已记）；BatchFlock 文档漂移（已 4 工具接入却写"待专项"）。

---

## §3 建议优先级

1. **P0**：R02-C1（分钟门运行时硬校验 + alias）；R02-C2（staleness 窗口无关 + 分块回归）。
2. **P1**：R02-I1（industry gate）、R02-I2（Inf 统一）、R02-I3（flock throttle 记账）、R02-I6（T2 自举 + 退出码/回退）、R02-I7（插件冲突前拒绝/回滚）。
3. **P2**：R02-I4/I5/I8；文档同步（quant-core 契约勘误、ENG 证据补 README、DATA README superseded 标注、EVID 归属）；Minor 清单。

> 证据脚本：`/tmp/opencode/reviewer-r21-platform/`、`reviewer-r21-tools/`、`reviewer-r02-newturf/`（probe1-11）、`auditor-r21/`
> （均为一次性实验，未入库；关键命令与输出已在上文引用）。
