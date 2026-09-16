# R06 台账与验收证据对抗性复查（证据/报告）

- 轮次：R06（2026-09-16 post-migration）
- 范围：① `governance/evidence/reviews/findings.md` 完整性；② R24 验收证据独立复跑；
  ③ R27/R28 证据复跑；④ `governance/ops/check_reviews.py` 门对抗实验；⑤ `reviews/README.md` 流程一致性。
- 方式：只读评审；对抗实验/复跑产物一律落 `/tmp/r06-gate-ledger/`、`/tmp/r06-strategy-rerun/`；
  本目录仅存命令与原始输出。
- 结论：**R24/R27/R28 的可复验声明（映射、零残留、单解释器、runs 单点、data 零写、策略首例逐帧一致）全部复现通过**；
  发现 **3 条 Important（台账闭环状态失真、门对证据列零覆盖、门不区分"有证据/有文字"）+ 4 条 Minor**。
  未发现 Critical。

## 0. 复现命令（入口）

```bash
cd /data/students/gaolei/stock
platform/.venv/bin/python governance/evidence/reviews/r06-2026-09-16-post-migration-review/evidence/ledger/analyze_ledger.py        # 台账分析
platform/.venv/bin/python governance/evidence/reviews/r06-2026-09-16-post-migration-review/evidence/ledger/verify_migration_map.py # 映射复核
platform/.venv/bin/python governance/evidence/reviews/r06-2026-09-16-post-migration-review/evidence/ledger/gate_adversarial.py     # 门对抗
platform/.venv/bin/python governance/ops/check_reviews.py [--selftest]                                                            # 门基线
```

## 1. 台账状态分布（脚本实计；analyze_ledger.out.txt §A）

| 轮次 | open | fixed-claimed | verified | reopened | wontfix | deferred | 空/非法 | 行数 |
|---|---|---|---|---|---|---|---|---|
| R01 | 0 | 63 | 0 | 0 | 0 | 0 | 0 | 63 |
| R02 | 0 | 12 | 0 | 0 | 0 | 0 | 0 | 12 |
| R03 | 0 | 13 | 0 | 0 | 0 | 0 | 0 | 13 |
| R05 | 0 | 5 | 0 | 0 | 0 | 0 | 0 | 5 |
| **合计** | **0** | **93** | **0** | **0** | **0** | **0** | **0** | **93** |

- 状态为空/非法行：**0**；列数不足（cells < header）行：**0**。
- **复查列为空：92/93**；唯一有内容的是 R05-C1（复查列写"**verified（2026-09-16 对抗性复查）**"，
  但状态列仍是 `fixed-claimed`）。
- R02 复查（findings.md:8/124 + R02 report §1）声称"63 行中约 56 行 verified、7 行 reopened/partial"，
  该判定**未回填**到任何行的「复查」列/状态列；例外口径只存在于文末「R21 复查例外」表（无状态列，
  门自动排除）。
- R21 例外表两行现状：
  - `R01-DATA-C1`（reopened）：原行（:37）修复说明尾部注明【R02 reopened 已闭环】da8bba0 + f55befa，
    状态仍 `fixed-claimed`；R02-C2（关联项）也仅 `fixed-claimed`。
  - `R01-TOOLS-I5`（partial）：原行（:84）注明【R02 partial 已闭环】620940a（市场级覆盖率门）；
    状态仍 `fixed-claimed`，复查列空。
- 严重度分布（门实计）：R01 13C/50I；R02 2C/9I；R03 0C/8I **+5 行 M**；R05 1C/3I **+1 行 M**。
  README:63 规定"M 只登记在各轮 report，不进台账"——实际台账含 6 行 M，规则与事实矛盾。

## 2. fixed-claimed 证据路径抽查（10 条等距样本 + 全量扫描）

样本（line 25/39/53/67/81/95/109/143/152/170），逐 token 解析（含 `X-before/after.txt` 台账简写展开）：

| line | ID | token 解析 | 说明 |
|---|---|---|---|
| 25 | R01-ENG-C1 | 7/7 | before/after 简写展开到实际文件 |
| 39 | R01-DATA-I3 | 4/4 | 同上 |
| 53 | R01-M8-I4 | 3/3 | |
| 67 | R01-EVAL-I4 | 0/0 | **该行未给任何证据路径**（仅 probe 名+case），证据实际在 `R21/EVAL/after/` |
| 81 | R01-TOOLS-I2 | 2/2 | |
| 95 | R01-STRAT-C1 | 3/3 | |
| 109 | R01-EVID-C2 | 5/5 | R12/R13 新树路径可解析 |
| 143 | R02-C1 | 3/4 | `repro-*-C1-probe11/7*.txt` 无对应文件（实为 probe11/probe7-alias 四个文件） |
| 152 | R02-I7 | 1/1 | |
| 170 | R03-M2 | 1/2 | `.md` 为文本截断碎片 |

全量（93 行、269 个路径 token）扫描结果：**265+ 可解析；3 处引用不可点**：
1. `findings.md:145`（R02-I1）：`R22/R03/tools/I1-doc-annotations.txt` → 实际在 `R22/R02/tools/`
   （轮次目录 R03/R02 错位）。
2. `findings.md:54/56`（R01-M8-I5/I7）：`R21/M8/I4-I5-before/after.txt`、`R21/M8/I7-before/after.txt`
   → 归档实际命名为 `I4-I5-before-test-red.txt`+`I4-I5-after-test-green.txt`、`I7-before-test-red.txt`+
   `I7-after-test-green.txt`（无无后缀版本）。
3. `findings.md:143`（R02-C1）：`repro-*-C1-probe11/7*.txt` → 实际 `repro-{before,after}-C1-probe11.txt`、
   `repro-{before,after}-C1-probe7-alias.txt`。
上述均因门不校验「修复说明」列（见 §4）而长期不可见；实际证据都在邻近路径，未影响复现。

## 3. 复验对照表（声明 vs 实测）

### R24（验收 `governance/evidence/verification/R24/`）

| # | 声明（来源） | 独立复跑 | 实测 | 判定 |
|---|---|---|---|---|
| 1 | `make test-research` 单解释器：platform/tools 337 + research/tools 58/2skip，exit 0（12-acceptance） | `make test-research`（reruns/rerun-make-test-research.txt） | platform/tools **337 passed**；research/tools **57 passed/2 skipped/2 failed**（`accel_inflow.yaml` 挖矿在途未归档+索引漂移） | ✅ 单解释器实装；数字按挖矿在途漂移（非 R24 回归） |
| 2 | 旧路径零活引用（12-acceptance §2；old-path-final.txt 2768 命中） | 同模式 `git grep` + 分类（rerun-oldpath-grep/live-hits） | 活文件命中仅：映射表（knowledge/README、migration-r04）、门内 MAP_PREFIX、工具树自有 `scripts/` 子目录、历史注记 | ✅ 吻合 |
| 3 | 旧→新映射 14 行全 ✅（migration-r04 §3） | `verify_migration_map.py` | 新根全部存在；18/18 旧路径消失；13 个提交 SHA 均在 | ✅ 14/14 |
| 4 | `platform/results` → `runs/platform` 单点；冒烟 show 可读（08-runs） | `cd /tmp && factorlab show r12_smoke`（reruns/rerun-runs-single-point.txt） | exit 0；产物 5 文件在 `runs/platform/r12_smoke`；旧根不存在 | ✅ |
| 5 | `data/` 零写：mtime+size+path 清单 sha256 前后一致、78120 文件、353G（00-baseline vs 12-acceptance） | `sha256sum` + `wc -l` + `du` | before==after==`1ed59103…`；78120 行；353G | ✅ 冻结证据自洽 |
| 6 | 平台全量 3150 passed / 13 skipped（00-baseline/12-acceptance） | 全量 pytest 复跑（reruns/rerun-platform-pytest.txt） | **3150 passed / 13 skipped / 803.92s**（并联评审另一次 809.91s 同数） | ✅ 精确复现 |
| 7 | `make gates` 除挖矿在途 G-ANNOTATE 全绿（12-acceptance） | 本轮同批（16:5x，`make gates` 由并联评审复跑） | G-ANNOTATE/G-INDEX 挖矿在途红、其余含 G-REVIEWS 全绿 | ✅ 归因一致 |

### R27（工具迁移 + 单解释器）

| # | 声明 | 独立复跑 | 实测 | 判定 |
|---|---|---|---|---|
| 1 | `platform/tools` 337 passed；`research/tools` 35（迁移后） | make test-research（§3 R24#1） | 337 passed；research/tools 57+2skip（用例增加，2 fail 挖矿在途） | ✅ 337 精确复现 |
| 2 | 裸跑 T2：quark 9 / converters 11 / lob_fact 192（`env -u PYTHONPATH`） | 同命令（rerun-r27-bare-t2.txt） | **212 passed**（9+11+192），exit 0 | ✅ |
| 3 | `data/` 元数据 sha256 前后一致（3cb50cea…） | `hashes.txt` 交叉核对 | before/after 同为 `3cb50cea…` | ✅ |
| 4 | `make reconcile` exit 0（CH 只读对账） | 未复跑（本轮 CH 只读任务由并联评审覆盖） | — | 未复核 |

### R28（策略配置化首例）

| # | 声明 | 独立复跑 | 实测 | 判定 |
|---|---|---|---|---|
| 1 | 首例 2025-03：decisions 5、NAV 9,992,407.37→10,214,807.42（+2.2257%）、fills 176、fees 18,290.52、DD -0.3140%、target 150 | `run_strategy.py --out-dir /tmp/r06-strategy-rerun/...`（reruns/rerun-r28-strategy*.txt） | **逐项精确一致** | ✅ |
| 2 | 与落盘产物逐帧相等（task5-parity-frame-equal） | 从 /tmp 产物读 11 个 frame 与 `runs/platform/strategies/...` 逐一比较 | **ALL_FRAMES_EQUAL: True**（nav/target/schedule + 8 artifacts） | ✅ |
| 3 | 平台 171 targeted passed；research 58/2 failed（挖矿在途） | 未复跑（research 腿在 R24#1 同因漂移） | — | 部分 |

## 4. G-REVIEWS 门对抗实验（gate_adversarial.py；原始输出 `gate-adv-*.txt`）

- 门基线：真实台账 exit 0；`--selftest` 7 类场景 exit 0（reruns/gate-baseline.txt）。
- 注入方式：在 `/tmp/r06-gate-ledger/<case>.md` 上对真实台账副本逐案改 1 处（victim = line 25）。

| case | 注入 | 门输出 | 判定 |
|---|---|---|---|
| m1 | 状态改 `done` | RED：非法状态 | ✅ 抓到 |
| m2 | `fixed-claimed` + 修复说明清空 | RED：修复说明为空 | ✅ 抓到 |
| m3 | 行只保留前 5 列（缺修复/复查列） | RED：修复说明为空（间接） | ✅ 抓到 |
| m4 | 追加重复 ID 行 | RED：ID 重复 | ✅ 抓到 |
| m8 | 状态清空 | RED：非法状态 '' | ✅ 抓到 |
| m9 | 统计声明 11→12C | RED：统计≠实计 | ✅ 抓到 |
| m6 | 修复说明 = **反引号内**死路径 | RED：路径不存在 | ✅ 抓到 |
| **m5** | 修复说明 = `已修复，无证据。`（非空、无 commit/路径） | **GREEN（exit 0）** | ❌ 门牙缺口（见 I3） |
| **m7** | 修复说明 = **无引号**死路径 | **GREEN（exit 0）** | ❌ 门牙缺口（见 I2） |

- 量化门覆盖（gate-backtick-coverage.txt）：全 finding 行路径 token **反引号内 134 / 反引号外 275**；
  其中**「修复说明」列 0 / 269** —— 即门对"证据列"的路径校验为零。

## 5. Finder：findings 清单

### I（Important）

- **R06-I1 台账闭环状态失真（唯一状态源不可信）**
  - 位置：`governance/evidence/reviews/findings.md` 全表；:8/:124（R02 prose）、:131-137（例外表）、:204（R05-C1）。
  - 复现：`analyze_ledger.py` §A/§C/§F；`grep -c verified findings.md` = 3（均非状态列）。
  - 实测 vs 期望：93/93 `fixed-claimed`、0 `verified`、复查列 92/93 空；R02 已判定的 56 verified/
    7 reopened/partial 未落入状态列；R05-C1 复查文本自称 verified 而状态未改。期望：README §流程 3 与
    状态词表要求 reviewer 结论落「复查」列并改 `verified`/`reopened`。
  - 影响：门打印"口径自洽"只是词表合法，不能证明任何一行闭环；对外"台账状态"读取会高估完成度。
  - 证据：`analyze_ledger.out.txt`、`gate-baseline.txt`。

- **R06-I2 G-REVIEWS 对「修复说明」证据路径零覆盖**
  - 位置：`governance/ops/check_reviews.py:299-306`（只扫反引号 span）；`findings.md` 修复说明列。
  - 复现：`gate_adversarial.py` 的 m7（未加反引号死路径 → exit 0）；`gate-backtick-coverage.txt`（0/269）。
  - 实测 vs 期望：门宣称"引用路径存在"，实际 269 个证据 token 全部跳过；反引号内注入（m6）才能抓到。
  - 证据：`gate-adv-m7-deadpath-plain.txt`、`gate-adv-m6-deadpath-backticked.txt`、`gate-backtick-coverage.txt`。

- **R06-I3 门不区分"有证据"与"有文字"**
  - 位置：`check_reviews.py:295-296`（仅 `not get("修复说明")`）。
  - 复现：`gate_adversarial.py` m5 → exit 0。
  - 实测 vs 期望：docstring 称"`fixed-claimed` 行的「修复说明」非空（须含证据）"；`已修复，无证据。`
    可通过。期望至少校验含 commit SHA / 证据路径之一，或把"无证据 token"计数上报。
  - 证据：`gate-adv-m5-fixdesc-no-evidence.txt`。

### M（Minor）

- **R06-M1 证据引用不精确 3 处**：`findings.md:145`（R22/R03→实际 R22/R02）、`:54/:56`（before/after
  文件名与归档名不符）、`:143`（`repro-…-probe11/7*.txt` 无对应）。证据存在，均可人工定位。
- **R06-M2 `reviews/README.md` 过时 3 处**：
  ① 结构块仍写 `docs/reviews/`（:9；与 :20 的 R24 注记矛盾）；
  ② 「方案批次与执行序」(:24-28) 三行"待执行"均已落地——工具迁移=R27（`04e9f9e`，platform/tools 存在）、
     Plan S=R28（Tasks 1-7 ✅，`research/strategy/low_lottery_top30_weekly.yaml` `7e03acb`）、
     R24 目录重整=已完成（migration-r04 全 ✅）；
  ③ 「复查命令」(:70) `make test-research  # T2 (emb) + T1 (平台 venv)` 与单解释器事实不符（EMB_PY 已删）。
- **R06-M3 README 严重度规则与台账矛盾**：README:63 "M 不进台账"，实际 6 行 M（R03-M1..M5、R05-M1）；
  门统计忽略 M（`R03 0C/8I`）。
- **R06-M4 R24 验收目录无顶层索引**：`R24/` 根无 README（`12-acceptance/README.md` 代行），
  与 R27/R28 目录惯例不一致。

### C（Critical）

- 无。

## 6. 证据文件清单（`evidence/ledger/`）

| 文件 | 内容 |
|---|---|
| `R06-REPORT.md` | 本报告 |
| `analyze_ledger.py` / `analyze_ledger.out.txt` | 台账解析脚本 + 状态分布/列数/路径/抽样原始输出 |
| `verify_migration_map.py` / `.out.txt` | R24 映射 14 行独立复核（新路径/旧路径/commit SHA） |
| `gate_adversarial.py` / `gate-adv-summary.txt` | 门对抗驱动 + 汇总 |
| `gate-adv-m{1..9}-*.txt` | 9 个注入场景的逐案命令/输出/exit |
| `gate-backtick-coverage.txt` | 门可检查 token 数（反引号内/外）量化 |
| `reruns/gate-baseline.txt` | 门与 selftest 基线 |
| `reruns/rerun-make-test-research.txt` | R24 单解释器 + R27 337 复跑 |
| `reruns/rerun-r27-bare-t2.txt` | R27 裸跑 T2 复跑（212 passed） |
| `reruns/rerun-r28-strategy*.txt` | R28 首例独立复跑（dry-run/真跑/metrics/逐帧对照） |
| `reruns/rerun-runs-single-point.txt` | R24 runs 单点复跑（/tmp cwd show） |
| `reruns/rerun-oldpath-grep.txt` / `rerun-oldpath-live-hits.txt` | R24 零残留断言复跑 + 活文件命中分类 |
| `reruns/rerun-platform-pytest.txt` | 平台全量复跑 = **3150 passed / 13 skipped / 803.92s**（精确复现基线；并联评审 regression/platform_pytest.log 809.91s 同数） |
