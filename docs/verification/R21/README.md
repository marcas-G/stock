# R21 修复轮证据索引（R01 严格评审 63 行全部 fixed-claimed）

- 对象：`stock` @ R21 修复轮（起点 `dd01bd9`）；台账 `docs/reviews/findings.md`
- 每行的 commit / 复跑命令 / 原始输出路径见台账「修复说明」列；本目录按子系统归档全部前后对照

## 验收基线（R21 工作区，coordinator 实跑）

| 项 | 命令 | 结果 |
|---|---|---|
| 平台全量 | `cd platform && .venv/bin/python -m pytest -q` | **2696 passed / 13 skipped**，476s，exit 0（R01 基线 2570） |
| 研究 T2 | `emb/bin/python -m pytest research/tools -q` | **268 passed / 10 skipped**（R01 基线 245/4） |
| 研究 T1 | `platform/.venv/bin/python -m pytest research/tools/{strategies,ch_ingest,factor_lib,1m_features,ashare_ingest,universe_stages}/tests -q` | **116 passed**（R01 基线 59） |
| 因子 lint | `make lint-factors` | **152/152** |
| 常驻门 | `bash scripts/gates.sh` | 结构门全绿 + 数据接口门 ENFORCED（G-CONTRACT REPORT 68 处为登记未竟项） |
| CH 对账 | `platform/.venv/bin/python research/tools/ch_ingest/reconcile.py` | exit 0 全绿（含派生表扩面） |

## 提交映射

| 树 | commit | 范围 |
|---|---|---|
| platform | `ea2ebfe` | ENG：未来函数 C1/C2、chunk 累计算子、插件硬化、lint 语义门、cs_resid |
| platform | `fc2858c` | DATA：pit_qfq 全局 base、delist_date 兼容、staleness gate、rebuild 去重/原子 manifest、单位归一、refresh 原因 |
| platform | `0687914` | M8：artifact 失效协议+交叉校验、部分成交审计、typed empty、trailing 终止、manifest 校验/timing |
| platform | `87958b2` | EVAL：NaN 口径、真 Spearman/周频、均匀抽样、web target/降级、tie-aware decile、t 分母 |
| platform | `3af7275` | TOOLS-I5 平台侧：stk_limit 缺行=合法无限制 |
| platform | `08432ab` | 平台文档：interface 结构/语义、playbook、spec 状态、quant-core 契约勘误 |
| research | `5187030` | ch_ingest / ashare_ingest：退市 in-file code、除权参考价 pre_close、单位/空值/断点/gate、delist_date 灌入、reconcile 扩面、数据重灌 |
| research | `b192235` | converters / universe_stages：layer3 守卫、preflight、--only-day 隔离 |
| research | `0bb1b2f` | strategies / lob_fact / quark / tickkit：C1/C2/C3 + I4-I8 |
| research | `0d21e7e` | 因子档案历史快照标注 + 基线数字 |
| workspace | `e3d2454`（+ 台账回填提交） | 全轮证据、R12/R13 补录、R19 勘误、pending/ignore |

## 子系统证据目录

- `ENG/` R01-ENG-C1/C2/I1-I5（probe 前后 + pytest 红绿 + lint-factors）
- `DATA/` R01-DATA-C1/I3-I8（probe 前后 + 双腿测试 + 接线验证）
- `M8/` R01-M8-I1-I7（reviewer probe3/4 前后 + 目标测试红绿）
- `EVAL/` R01-EVAL-C1/C2/I1-I9（8 个 probe 前后 + 对抗测试）
- `TOOLS-A/` R01-TOOLS-C1/C2/I1-I4/I6/I7 + R01-DATA-C1/C2 生产侧（重灌日志、sha256、对账、逐点验证）
- `TOOLS-B/` R01-TOOLS-I8/I9/I10（红→绿 + T2）
- `STRAT/` R01-STRAT-C1/C2/C3/I4-I8（probe 前后 + T1/T2 + I6 数据可得性）
- `EVID/` R01-EVID-C1/C2/I1-I8（标注/补录/勘误 + 门结果）

## 数据重灌影响（R01-TOOLS-C1/C2、DATA-C1/C2、TOOLS-I1/I4）

- 新 `data/fact/daily_fact/daily_fact.parquet` sha256 `79f68fee4f73c877…`，18,124,805 行 / 5,861 codes（旧 18,162,795，备份 `*.bak-R21`）
- CH：daily/adj_factor/daily_basic/adj_detail 同步；stk_limit 17,854,764 行；stock_basic 5,861 行（含 delist_date）
- 关键复验：600519 单位、300842 除权参考价、600811 逐值、600005 退市 is_listed=False、adj_factor NULL 语义、stk_limit 事件日 band
