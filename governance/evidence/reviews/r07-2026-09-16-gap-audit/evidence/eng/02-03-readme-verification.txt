# item2/3: reviews README + R24 README verification
# date: 2026-09-16T18:31:36+08:00 HEAD: 639bf3f

## reviews README key sections (lines 6-21, 33-37, 60-79)
## 结构

```
governance/evidence/reviews/
├── README.md                本文件（约定）
├── findings.md              台账 = 唯一状态源（reviewer 与开发团队都写这里）
└── rXX-YYYY-MM-DD-<标题>/    每轮评审
    ├── report.md            该轮完整报告（reviewer 只追加；勘误加在文末）
    └── evidence/            可复跑 probe 脚本 + 原始输出（索引见目录内 README.md）
```

> R24（2026-09-16）：原 `2026-09-15-open-operators/`、`2026-09-15-minute-execution/`、
> `2026-09-16-strategy-decomposition/` 三个**设计/实施方案**目录已迁
> `knowledge/design/workspace/`（非评审轮次，不占台账结构）。
> **本台账目录** `docs/reviews/` → `governance/evidence/reviews/`（同一搬迁）；历史报告/证据正文不改，按此映射解析。

**执行注意（2026-09-16 R06 更新）**：
- 序 1/2/3 **均已落地**（R24 批次 + R27/R28），原"不同窗口"约束解除；
- R06 复查结论：迁移面无 C、R24/R27/R28 声明全部实证通过；**门红（G-INDEX/G-ANNOTATE）与台账/门问题**
  见 `r06-2026-09-16-post-migration-review/report.md` §2/§6；
- `make test-research` 已为**单解释器单腿**（平台 venv 3.13）。
## 严重度

- **C（Critical）**：错误结果 / 数据损坏 / 未来函数 / 资金安全类，必须修。
- **I（Important）**：正确性风险、契约违反、测试盲区，应修。
- **M（Minor）**：文档、风格、优化项（只登记在各轮 report，不进台账，避免稀释主线）。

## 复查命令（基线见各轮 report §0）

```bash
make gates                                                    # 常驻门
cd platform && .venv/bin/python -m pytest -q                  # 平台全量（~7.5min）
make test-research                                            # 工具/研究测试（单解释器：平台 venv 3.13）
```

## 给开发团队的提示

- 修复前先跑当轮 `evidence/` 里的对应 probe 复现；修完再跑一遍，前后输出都存档。
- probe 大多需 `cd platform` 后用 `.venv/bin/python` 运行；涉及 CH 的只读且限流；m8 的 probe
  会自建 `/tmp` 中间产物。
- 数据类修复（重灌）请在「修复说明」里写清影响行数、重灌命令与对账结果。

## ledger M rows (6)
169:| R03-M1 | M | 误 import 平台宏（`from polars_ta.prefix.wq import returns`）报 expr_codegen 深层裸 traceback，无"裸用"指引【实测】 | `core/factor/ast_gate.py` 或 lint 前校验 | verified | ba59430；平台宏名单单点（PLATFORM_MACRO_NAMES），ast_gate 对 Import/ImportFrom 宏名前置拒绝（'平台宏 returns 请裸用'，含别名）；compute_formula 与 lint 均清晰 FactorDSLError（before expr_codegen 裸堆栈）；测试 test_ast_gate.py +5、test_compute.py +2、test_cli_lint_v2.py +1（red 8 failed→green 51 passed） | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
170:| R03-M2 | M | `factor-mine` skill 文档漂移：种子 glob 未适配族子目录、路径缺族、duckdb 指南过期 | `.claude/skills/factor-mine/SKILL.md` | verified | 55e754c + dde8aae；factor-mine skill 三处漂移同步（种子 glob '*/<stem>.md'、路径补族目录、duckdb→CH 指南/字段核对/ST 降级开关），同族 assumption-review/code-review 与档案 _template 一并校准；实测 157 候选；证据 R22/R03/tools/M2-doc-sync.txt | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
173:| R03-M3 | M | `evaluation.decile_returns.spread` 符号随 direction 翻转：同一因子 direction -1→+1 时 raw IC 不变（+0.0756）而 spread 从 +0.00676 变 -0.00676，易误读【实测】 | `kernels/quant_core` spread 语义；`docs/catalog.md` | verified | 98feec6；contract 语义不改（spread=(g0−g9)×direction）；interface 评估段补精确公式与读法（spread<0 = 与声明方向一致；有效性看 ic/long_short）、CLI list 表尾加同款提示；测试 tests/test_cli_list_show.py::test_list_spread_sign_hint；probe R22/R03/misc/probe_m3_spread_sign.txt | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
175:| R03-M4 | M | `TargetPortfolio` 校验用 `map_elements` 触发 `PolarsInefficientMapWarning`（每次策略构建都刷警告；性能与"测试输出 pristine"纪律双违背）【实测】 | `core/domain/portfolio.py:151` | verified | bd86c46；TargetPortfolio 两处校验向量化（str.contains(CANONICAL_TS_CODE_PATTERN) / is_in），消除 PolarsInefficientMapWarning；测试 tests/test_portfolio_domain.py::test_no_inefficient_map_warning_on_construct + null 拒绝向量化 2 条；证据 R22/R03/misc/r03_m4_red.txt → r03_m4_green.txt | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
176:| R03-M5 | M | `adapters/intraday.py` docstring 写 "datetime = minute-end"，与实测（bar 起点标注：tick 对拍 delta=0 在起点）不符——仅文档漂移，索引→时钟映射不受影响【probe】 | `adapters/intraday.py` docstring | verified | 98feec6；实测对拍（000021.SZ@2025-08-12）：bar 起点标注（15:00 竞价 bar 与 time_ms=15:00:00 成交 delta=0）；修 adapters/intraday.py docstring（bar 起点口径）+ interface 分钟面 + funnel spec 勘误；行为零变；证据 R22/R03/misc/probe_m5_bar_time_labeling.txt | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
203:| R05-M1 | M | 新解锁面不可发现：`op list` 仍 57、`catalog.md` 仅 `ts_corr`，512 条分类面无 CLI/文档检索入口（团队 R22 summary §偏差 5 已记账，归 Plan 2） | `surfaces/cli/main.py`（op list）；`platform/docs/catalog.md` | verified | 1c54ea4；最小发现入口：factorlab op list --catalog 列分类表全集（528 条：name/partition/window/source/returns），默认 op list 注册面视图不变；op doc <name> 未注册但分类表有 → 回退打印元数据与来源（未知名 exit 1）；interface.md 标注入口；测试 test_cli_op_registry.py::test_cli_op_catalog_and_doc_fallback；证据 R23/usage/r05-m1-op-discovery.txt；完整 op/catalog 同源仍归 Plan 2（文档注明） | verified（R06 回填复查：op list --catalog 528 条可用） |

## findings.md line 213 M-not-in-ledger note for R06
- M 级 10 条只登记在报告 §3（按 README 约定不进台账）；证据 `r06-2026-09-16-post-migration-review/evidence/`

## R24 README exists + size
-rw-rw-r-- 1 gaolei gaolei 7544 9月  16 17:37 governance/evidence/verification/R24/README.md

## R27/R28 README
-rw-rw-r-- 1 gaolei gaolei 10505 9月  16 12:55 governance/evidence/verification/R27/README.md
ls: 无法访问'governance/evidence/verification/R28/README.md': 没有那个文件或目录
