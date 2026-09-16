# R01 严格 Review 报告（2026-09-15）

- **对象**：`stock` @ HEAD `dd01bd9`（工作区干净）
- **方法**：7 个并行评审子代理（引擎/DSL、数据面、M7/M8、评估、数据工具、策略/LOB、证据完整性），
  coordinator 对全部 Critical 逐条独立复核（标记【实测】）；台账见 `../findings.md`（11 C / 37 I）
- **总评**：基础设施的自证能力是真的（门/证据/测试经得起复现），但**结果可信度有硬伤**：
  数据管道数值错误、PIT 完整性缺失、DSL 未来函数旁路、策略结论不可复现。此前"整体良好"的印象需修正。

## §0 基线实测（run by coordinator）

| 项 | 命令 | 结果 |
|---|---|---|
| 常驻门 | `make gates` | 全绿（含 4 个门 selftest） |
| 平台全量 | `cd platform && .venv/bin/python -m pytest -q` | **2570 passed / 13 skipped**，450s，exit 0 |
| 研究 T2 | `emb/bin/python -m pytest research/tools -q` | **245 passed / 4 skipped**，12.8s |
| 研究 T1 | `platform/.venv/bin/python -m pytest research/tools/{strategies,ch_ingest,factor_lib,1m_features,ashare_ingest,universe_stages}/tests -q` | **59 passed**，9.6s |

基线数字与文档一致，测试本身没有造假。

## §1 Critical 明细（11 项）

### ENG —— DSL 未来函数门两个旁路【实测】

1. **R01-ENG-C1（Subscript 语法糖）**：`signal = close[-1]` 过 `factorlab lint`（exit 0），
   E2E `run_factor` 实测 `signal(t)==close(t+1)`；`universe.formula` 同样可绕。
   根因：`ast_gate.py:30` 放行 Subscript；`reject_future_shifts` 只看 `ast.Call`
   （vendor `expr_codegen/codes.py` 把 `X[k]` 编译为 `ts_delay(X,k)`）。
   复现：`platform/.venv/bin/factorlab lint docs/reviews/r01-.../evidence/engine-dsl/future_subscript.yaml`
2. **R01-ENG-C2（Name 常量折叠）**：`_n = 3; signal = ts_delay(close, -_n)` 过 lint（exit 0），
   E2E 实测 shift(-3)；`-_n`、`1-_n`、`0-_n` 同理。`-1` 字面量反被正确拒绝（`partitions.py:136-140`
   只回退裸 Name；`_const_fold` 不解析 Name）。

### DATA —— 平台数据面【实测】

3. **R01-DATA-C1（退市股永不退市）**：`stock_basic` 无 delist/status 列（CH 实测），
   600005.SH 最后成交 2017-02-13 但 `resolve_universe_frame(...,2026-08-14).is_listed=True`，
   run 链 forward-fill 9 年死价格进截面。`FULL_HISTORY_PIT_GATE=BLOCKED_BY_DELIST_DATE` 仅文档。
4. **R01-DATA-C2（pre_close 语义）**：实测 300842.SZ 2024-04-10 `pre_close=70.9`（=前日 raw close）、
   `pct_chg=-28.1%`；catalog.md 却承诺"除权除息日为除权参考价"。`stk_limit` 由同一 raw pre_close
   派生 → 除权日涨跌停基准错（工具侧实测 2025 年 99.7% 除权日偏离）。

### TOOLS —— 数据生产【实测】

5. **R01-TOOLS-C1（单位错 1e4）**：实测 600519 2026-07-31 `total_mv=16883.6`
   （close×总股本=168,836,022 万元 → 实为"亿元"被当"万元"），`turnover_rate=4409.91`
   （vendor 自报 0.441%）→ 18.16M 行整体错 1e4（`ingest_daily.py:55-56`）。
6. **R01-TOOLS-C2（退市文件代码错位）**：`000018/000023/000024/000033/000038` 与 600811.SH
   各 7598 行、区间相同，且**1612 个交易日 close+vol 完全相等**（实测）；5 只真退市历史丢失。

### STRAT —— 策略【实测代码 + probe】

7. **R01-STRAT-C1（跌停过滤静默失效）**：`strategy_crash_bottom.py:507` 取 6 位 code，
   panel 在 M7-05 后为 canonical `000001.SZ`（`test_run_factor.py:36`）→ join 永空、
   `--no-limit-down` 变 no-op；文档 +20pp 的"跌停过滤"特性在重生成 panel 上消失。
8. **R01-STRAT-C2（段边界成本）**：holdings 跨段不清零、turnover 只算 `codes - holdings`
   （`:117-123,182-184`）→ 段落清仓/再入场无成本（probe：应为 3 次只收 1 次）。
9. **R01-STRAT-C3（死入口）**：`quark_download_server.py:95` 引用未定义 `COOKIES`，实测 NameError。

### EVAL —— 评估口径【实测】

10. **R01-EVAL-C1（layered NaN）**：`layered.py:105-107` 只滤 null；probe 实测 NaN signal 被排
    D1 首位（r=1）、NaN fwd 使 D10 净值此后全 nan（summary NaN）。
11. **R01-EVAL-C2（weekly_ic NaN）**：`ic_series.py:25` 只 drop_nulls；probe 实测 same panel
    kernel mean=1.0 vs `weekly_ic` raw mean=0.788 → Web 曲线与 summary 同页矛盾。

### EVID —— 证据完整性【实测】

- **R01-EVID-C1**：`platform/results/` 空、无 `*.duckdb`（实测 find），152 份档案数字不可复跑且未标注。
- **R01-EVID-C2**：R12/R13 status 宣称"真 CH 端到端/Web 冒烟"但目录无任何原始输出。

## §2 Important 汇总（37 项，明细见 `../findings.md`）

| 子系统 | 数量 | 代表项 |
|---|---|---|
| ENG | 5 | chunk-days 累计算子不一致；plugin 扫描绕过/静默覆盖算子；`cs_regression_resid` 不可用；lint 无语义门 |
| DATA | 6 | pit_qfq 分块依赖；CH delist_date 崩溃；rebuild 重复行/非原子 manifest；vol/amount 单位漂移；refresh 丢原因 |
| M8 | 7 | artifact 覆盖写崩溃窗口（建议升 C）；fill 行不可审计；trailing-unresolved 契约矛盾；加载校验宽松 |
| EVAL | 9 | Web 忽略 target；corr 非 Spearman/按日跑/有偏抽样；Web 500；periods≠n_weeks；t_stat 分母；ordinal tie；测试盲区 |
| TOOLS | 10 | adj null→0 破坏 qfq；checkpoint 竞态；失败分区 exit 0；stk_limit 除权基准；占位列广告；reconcile 盲区；layer3 编造窗口；--only-day 覆盖整月 |
| STRAT | 5 | T2 注入契约违反（假绿）；`--long --mc` 崩溃；结果不可复现；lob gate 只记录不拦；下载重试清空 URL |
| EVID | 8 | 基线数字过期（237/51 vs 245/59、183 vs 185）；interface 结构损坏；spec 状态过期；cookie 未 ignore；pending 过期 |

## §3 Minor（摘要，完整列表见各评审原始报告存档）

- 引擎：pool-def 报错与执行顺序矛盾；minute 常量折叠递归崩；inline 名可劫持；`params` 类型过宽；`_` 开头输出名晚失败；插件模块名注入；`polars*` 前缀过宽；属性读裸 TypeError；1 条空转测试。
- 数据：`stock_st` 空 → `is_st=false` 与文档"unknown≠false"矛盾；显式 codes 静默丢未知；`fetch_paged` 边界误报；audit 函数生产未接线；intraday 240 格未真验。
- M8：订单量不可审计；decision_range；manifest 字段未校验；timing 未持久化。
- 评估：spread/long_short 符号约定不一致；小组/负收益 max_drawdown 无意义；np.float64 不可 JSON；contract 勘误 0/1-based 标注反了；`quant-core>=0.1.0` 无法从 PyPI 解析。
- 工具：converters summary 键永不匹配；ddl 注释买卖方向反；import_index `--start` 无效；check_inputs 全量读；（多条）。
- 策略：lob gate 记录不拦；resume 只比 size；`walk→filter→download` 链路断裂；cost 30 vs 35bps 文本不一致；MC 硬编码 11.5 年。

## §4 强项（经复核保留）

- lob_fact：金样真 hash（14 文件）、M7 tamper 测试非空转、事故回归测试；converters 冻结 schema + 真实 e2e。
- 1m `check-day`：本轮复跑 value 级对拍 `max|Δ|=0`（5249/5249/5249）。
- 门体系：4 个 selftest 真实防死门；G-READ/G-MARK AST 化。
- 证据文化：R14/R15/R18/R19/R20 抽查为真；测试基线无造假。

## §5 修复优先级建议

1. **P0**：ENG-C1/C2 + lint 接语义门；TOOLS-C1/C2 + DATA-C2 + TOOLS-I1（数据重灌类，需先定口径）；
   STRAT-C1/C2 修复并重跑归档（或文档改标不可复现）。
2. **P1**：DATA-C1 退市/陈旧 gate + delist_date 灌入；EVAL-C1/C2 统一 NaN 口径 + Web target + corr Spearman；
   M8-I1 artifact staging/加载交叉校验；TOOLS-I4 stk_limit 除权基准。
3. **P2**：插件扫描加固、quark 死代码、EVID 文档漂移批量修正。

## §6 证据索引

`evidence/` 下按子系统分目录；文件 → finding 映射与运行命令见 `evidence/README.md`。

## §7 复查记录（reviewer 追加）

（待开发团队修复后追加；每条须含：findings ID、重跑命令、原始输出、结论）
