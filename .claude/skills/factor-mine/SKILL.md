---
name: factor-mine
description: 挖因子循环。随机选一个已入库因子为种子，分析其隐含假设（每个实现选择背后的"为什么"）、审核假设（语义矛盾/数据可实现）、在种子假设集合上变异/精确化（可多个，不引入无关新假设）、实现新因子、subagent 审核代码、跑结果、按模板入库。调用方式 /factor-mine [N轮数，默认1]。
---

# 挖因子（Factor Mining）

以因子库中现有因子的**隐含假设**为挖点，循环产出更精确的因子表达。
参照设计：`knowledge/design/research/specs/2026-08-17-factorlab-factor-mine-skill-design.md`（2026-09-15 单仓单树前的路径以该文为准；现行路径以本 skill 为准）。

## 输入

- `/factor-mine` — 跑 1 轮
- `/factor-mine 5` — 连续 5 轮（轮间不停顿，用户可中断）

## 前置检查

1. 因子库非空：`ls $QR/dossiers/factors/*/*.md`（家族子目录；模板在 `$QR/dossiers/factors/_template.md`），空则报错并停止。
2. 平台数据可用：`FACTORLAB_DATA_BACKEND=ch $FLAB list` 不报错。平台 duckdb 库（`data/factorlab.duckdb`）不存在，**当前唯一可用读后端是 ClickHouse**（`FACTORLAB_DATA_BACKEND=ch`）；CH 无 `stock_st` 表时 `exclude_st` 默认 fail fast，显式降级开关与挖矿口径见 `knowledge/contracts/interface.md` §4.2（`FACTORLAB_ST_DEGRADE=allow`）。
3. 每轮开工前向用户播报：`第 k/N 轮：种子=<seed>`，然后继续（不等待）。

## CLI 调用方式（重要）

`factorlab` 不在 bash PATH 中。用以下任一方式（本 skill 内所有 `factorlab`
命令均代换为 `$FLAB`）：

```bash
# 平台 venv 的 console script（Linux；Windows 路径为历史残留，2026-09-12 清理）
FLAB=/data/students/gaolei/stock/platform/.venv/bin/factorlab
# 研究产物区根（R37 Phase 2：spec/档案/索引已迁出主仓；本 skill 内所有 QR 均指它）
QR=${QUANTRESEARCH_ROOT:-/data/students/gaolei/quantresearch}
```

前置检查用：`$FLAB list`。

## 每轮流程（8 步）

### 1. 种子选择

```bash
python - <<'EOF'
import os, random, pathlib
qr = pathlib.Path(os.environ.get("QUANTRESEARCH_ROOT",
                                 "/data/students/gaolei/quantresearch"))
files = [f"{p.parent.name}/{p.stem}"
         for p in (qr / "dossiers/factors").glob('*/*.md')
         if p.name != '_template.md' and f"{p.parent.name}/{p.stem}" not in USED]
print(random.choice(files))
EOF
```

- 同一批连续轮次内种子互不重复（`USED` 为已用种子列表，逐轮累加；
  执行时把占位符替换成 Python 集合字面量，如 `USED = {'momentum_20d/reversal_20d'}`；
  种子 = `族/stem`——glob 已适配 `$QR/dossiers/factors/<族>/` 子目录布局）；
  所有种子都轮过一遍后循环回来（忽略 USED）。
- 读 `$QR/dossiers/factors/<族>/<stem>.md` 全文 + `$QR/factor/<族>/<stem>.yaml`（族见 `$QR/factor/_families.yaml`、索引见 `$QR/index/factors.md`）。

### 2. 假设分析（用 assumption-review.md 模板）

**深度分析范式（批次 4 教训固化）**：变异是假设分析的产物，不是已知模式的复用。

自由分析（不分类框、创造力为主）：**对种子因子的每个实现选择追问"为什么"**——
窗口长度、数据字段口径、权重/聚合方式、分子/分母结构、对股票的同质性假设、
调仓/持有期、处理链、符号方向、缺失处理……每个"为什么"背后的"隐含相信什么"
是一条隐含假设。**开工前自查**："我的变异是不是在复用已知有效模式
（掩码/条件化/窗口微调）？"若是，回到深挖——种子一定还有没被显式化的
隐含假设。重点找**通常没人显式关注的**假设——那里是挖点。

深挖的常见维度（每选择可能隐含其一侧）：状态 vs 事件、对称 vs 非对称、
整体 vs 部分、分子 vs 分母、强度 vs 方向、无时效性 vs 时效性——详见
assumption-review.md §0。

### 3. 假设审核（每条判定：成立 / 可疑 / 证伪 / 可精确化）

- **语义矛盾**：假设间互斥？与平台语义冲突？（TS/CS 分区、防未来、方向语义——
  见 `knowledge/contracts/interface.md` §DSL 语义与防未来、`knowledge/handbooks/factor-mining-playbook.md` §3.3）
- **数据可实现**：字段存在性（`knowledge/contracts/interface.md` §数据字段；可查 CH 临时库/生产库
  `platform/.venv/bin/python -c "from factorlab.adapters import ch_read; print([r[0] for r in ch_read.query_rows(\"SELECT name FROM system.columns WHERE database='factorlab' AND table='daily'\")])"`）、
  窗口长度 vs 历史（数据自 2000-01-04）、缺失率预估（种子档案 signal_null_ratio 参照）。
- **证据**：种子档案 §4 验证数据（IC/t/近 26 周/分层）+ 已知市场异象知识。

### 4. 变异设计

- 在种子假设集合上变异/组合，**可一次变异多个**；不引入与种子无关的全新假设。
- 目标是**隐含假设的显式化与精确化**——更详细准确的因子表达，
  **不追求表达式深度**（不加复杂度）。
- **变异必须能回溯到 §2 深挖出的某个隐含假设**（记录里写明对应关系：
  "假设 X → 变异为 X'：为什么"）；若变异无法回溯（在套用已知模式），
  回到 §2 重新深挖。
- 记录：保留哪些假设、精确化/变异哪些（变异成什么、为什么）、
  变异后假设集合的语义一致性（重新过 §3 矛盾检查）。
- 新因子名 `<seed>_<variant>`（小写蛇形，如 `reversal_20d_lowturn`）。
- 变异点清单写入一个临时记录（`runs/platform/_mine_rounds/_mine_round_<n>.md`，
  运行产物单点；目录不存在则先建），
  供实现与代码审核使用——它是对照物，之后不入库。

### 5. 实现

- 写 `$QR/factor/<族>/<name>.yaml`（族见 `$QR/factor/_families.yaml`），结构变异 = 新 spec（**不用 `--set`**；
  `--set` 仅用于同结构参数扫描）。
- 语义↔代码映射表：每条变异语义 → 公式行（写在变异点记录里）。
- 沿用平台自由代码公式（def/参数化，见 `knowledge/contracts/interface.md` §formula 与
  `$QR/factor/vol_run_energy/vol_run_energy.yaml` 范例）。direction 语义要与变异后假设一致。

### 6. 代码审核（独立 subagent）

按 `.claude/skills/factor-mine/code-review.md` 提示词 dispatch 一个
general-purpose subagent，输入：变异点记录 + `$QR/factor/<族>/<name>.yaml` +
`$QR/factor/<族>/<seed>.yaml`。审核不通过则修复后重审（修复后必须再次审核）。

### 7. 运行

**正式运行 = 工作流 / 服务目录（现状：工作流为主）**：工作流入口
`make xpipe CFG=research/tools/xscore/pipeline/configs/<cfg>.yaml`（Prefect 3；目录
`research/tools/xscore/pipeline/`）；服务目录 `governance/ops/service/` 仅留档（R39
容器化执行器已退役，生产路径 = 工作流 + 宿主 CLI）。宿主直跑（重任务经
`governance/ops/heavy.sh`）：

```bash
FACTORLAB_DATA_BACKEND=ch FACTORLAB_ST_DEGRADE=allow \
  $FLAB run $QR/factor/<族>/<name>.yaml [--no-backtest]
```

**R40 锁箱（碰箱必声明）**：spec 评估窗口与锁箱相交时必须带
`--lockbox exploration|final --lockbox-reason <理由>`，否则 `LOCKBOX_INTENT_REQUIRED`
拒跑（无产物）；`exploration` 自动登记、不限额，`final` 每候选每窗一次 + 每窗配额
M=20（入库/结论依据）。`date.end ≤ is_end`（`factorlab lockbox status --json` 的 `is_end`
= `window_start` 前一交易日）在 IS 内，无需 flag。锁箱运维入口 = 顶层
`factorlab lockbox status|roll`（即 `$FLAB lockbox …`；`flab` = `factorlab research`
门面，**无 lockbox 子命令**），契约见 `knowledge/contracts/interface.md` §10。

### 8. 入库

0. **冗余/增量检查（D10，入库前强制）**：候选按 scale（daily|minute）对照参考库
   `$QR/factor/_reference.yaml`（`factorlab ref list`）与同批全部候选：
   - `factorlab corr <候选们>`：任一对 |ρ|≥0.9 → 冗余，同族只留最强一只入库；
     |ρ|≥0.7 不得同时入库；跨 scales 不混用对照。
   - `factorlab resic`（组内互评或 `--target <候选> --against <库成员名单>`）：
     残差 |t|≥2 且 retention≥50% → 可加入；否则观察（近共线对先跑 corr 排除，
     避免 resIC 数值噪声）。
   - 结果写候选档案 §备注；正式入 **库** 时同步 `_reference.yaml`
     （name/style/reason/added/entry_corr_max/entry_resic_t），空 scale 首只即种子
     （两项 null）。
1. 对照 `knowledge/handbooks/factor-mining-playbook.md` §4.1 阈值判定（显著/边际/无效）。
   **R40 要件**：入库依据的评估窗口若碰锁箱（`date.end > is_end`），该评估必须为
   `--lockbox final` 且 `lockbox_access` 有对应 final 登记；`admit`/`ref add` 缺登记 →
   `LOCKBOX_FINAL_REQUIRED`（契约 `knowledge/contracts/interface.md` §10）。
2. 复制 `$QR/dossiers/factors/_template.md` → `$QR/dossiers/factors/<族>/<stem>.md`，逐节填写：
   验证数据快照自 `runs/platform/<name>/summary.json`（注明快照日期）；
   状态按判定（候选/观察中/无效）；§2 逻辑写变异后的假设表达。
3. 种子档案 `$QR/dossiers/factors/<族>/<stem>.md` §5 迭代历史加一行（日期/新因子/变异点/结果/结论）。
4. 互链：新档案 §6 备注链接 `[<seed>.md](<seed>.md)`；种子档案对应行注明新档案。
5. **入库前跑门（R07-MIG-I1/I2 纪律；R37 起对产物区）**：
   `platform/.venv/bin/python research/tools/factor_lib/build_index.py`（重生索引）+
   `make index-check` + `bash governance/ops/gates.sh --structure --topo`
   （G-INDEX 索引 / G-ANNOTATE snapshot / G-LINT）——红了先修：档案 §4 快照指针一律
   `runs/platform/<name>/summary.json`（不要旧结果根坐标 `results/…`）、缺 snapshot
   用 R21 脚本补齐（脚本按 `QUANTRESEARCH_ROOT` 解析档案根）。**门未绿不得宣布入库完成**。
6. **产物区不是 git 仓（R37）**：spec/档案/索引不再随主仓提交（历史提交在 stock git
   历史里）；工具/平台改动才提交主仓（按目录分权）。
   `git commit -m "feat(factor): <name> — <变异点一句话>"` 适用于**修改了主仓工具**的场景。
7. **轮末收尾（R31 起；R37 适配）**：跑 `make index`（一次重生 `factors.md` + `strategies.md`
   + composites）；确认本轮每个新 yaml 的同名档案已落盘——"yaml↔md 镜像"门对缺档有 **72h
   宽限**（`research/tools/factor_lib/dossier_freshness.py`：git 仓内按提交时间，产物区
   按 yaml mtime），超期即红。

## 全局规则

| 规则 | 内容 |
|------|------|
| 隐含假设优先 | 挖点是"没被关注到的隐含假设"——改进表达精度，不加深公式复杂度 |
| 聚焦变异 | 变异限于种子假设集合；可多个；不引入无关新假设 |
| 可归因 | 变异点逐一记录，结果优劣回溯到具体假设 |
| 负结果入库 | 不显著也建档案（判定"无效/证伪"），种子档案同样记录 |
| 审核分工 | 假设审核主 agent 做；代码审核独立 subagent（code-review.md） |
| 轮间状态 | 种子互异列表、轮数计数仅当批内有效；批次结束归档 |
| 资源 | 每轮 ~1 次全市场 run（30-60s）+ 1 个 subagent 审核；`runs/platform/<name>/` 每轮数十 MB；16GB 内存护栏（SQL-first）不变 |

## 明确不做

- 不引入种子无关的全新假设方向。
- 不自动参数扫描（那是手动 `--set` 研究）。
- 不改平台代码（若演练暴露平台缺口，另开 spec）。
- 不做多因子组合。

## 模板文件

- `assumption-review.md` — §2/§3 假设分析与审核工作模板（本 skill 目录内）
- `code-review.md` — §6 subagent 代码审核提示词（本 skill 目录内）
- `$QR/dossiers/factors/_template.md` — 入库档案模板
- `knowledge/handbooks/factor-mining-playbook.md` — 评估阈值与方法论
