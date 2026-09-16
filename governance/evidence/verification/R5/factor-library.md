# R5 因子库重排（2026-09-15）

## 做了什么

| 项 | 结果 |
|---|---|
| 族表 SSOT | `research/factor/_families.yaml`（14 族 → 名字前缀规则） |
| 改名计划器 | `research/tools/factor_lib/plan_rename.py`（产出 rename-map，**不移动文件**；`--tsv` 审计用） |
| 搬迁 | `git mv` **152 yaml + 152 档案**，全部落在 `factor/<族>/<短名>.yaml` 与 `docs/factors/<族>/<短名>.md`（**同族同短名镜像**，验证：14 族全部 yaml 数 == md 数） |
| 去冗余前缀 | 例如 `reversal_20d_intraday_turn` → `factor/reversal_20d/intraday_turn.yaml`；**`name:` 字段不变**（results/<name>/ 稳定） |
| 冲突回退 | 同族内 stem 不唯一 → 该组**保留全名**（实测 8 条：`low_vol_20d`/`max_effect_20d` 去前缀后都成 `20d`；以及 `momentum_20d`/`reversal_20d` 等 name==前缀者） |
| 索引 | `research/tools/factor_lib/build_index.py` → `docs/index/factors.md`（309 行：族总览 + 逐族表 + **变体组**）；`--check` 为 byte-equality 门，已接入 `scripts/gates.sh`（G-INDEX） |
| 门测试 | `research/tools/factor_lib/tests/test_index.py`（5 用例）：索引一致 / yaml↔md 镜像 + `xname==name` / 族前缀规则 / 同族 stem 唯一 / **变体组必须在索引中出现** |
| 引用面同步 | 技能 `SKILL.md`（7 处）、`docs/factor-mining-playbook.md`（4 处）、`research/README.md`、`research/AGENTS.md`、`Makefile`（index/lint 目标） |
| lint | 全库 **152 通过 / 0 失败** |

## ⚠️ 与计划不同的事实：**5 组"重复公式"里 0 个是真重复**

计划（我之前的盘点转述）说"5 组 15 个文件公式完全相同 → 历史变体移 `_archive/`"。
R5 逐个检查了这 15 个的完整定义，**每一组都在 direction / params / process 链上有差异**：

| 组 | 成员 | 实际差异 |
|---|---|---|
| 1 | momentum_20d, reversal_20d, reversal_20d_nowin, …_nowin_fill0, …_ranknorm | 方向 +1/−1；process 链 4 种（standardize / +winsorize / +fillna / +csranknorm） |
| 2 | vol_run_energy_symrun{,_r30,_r60,_w100} | **params** 不同（rl_win 30/60/120、win 100/200） |
| 3 | reversal_20d_drawdown, reversal_20d_highmomentum | 方向 −1/+1 |
| 4 | reversal_20d_volconf, reversal_20d_volconf_fwd | process 差异（后者多 `fillna(forward)`） |
| 5 | rsi_oversold_14, rsi_reversal_14 | 方向 +1/−1 |

**结论：不归档、不删除**（那会删掉合法研究记录）。改为**在索引的「变体组」章节成组展示**
（成员 + 差异说明），并由测试 `test_variant_groups_are_reported_not_silently_dropped` 锁住
"必须出现、不得静默消失"。归档与否属研究者判断——若你要归档某几组，告诉我组号即可。

（登记：该判断已写入 `docs/pending-items.md` 说明口径。）

## 门

- 索引一致 ✓ · 族规则 ✓ · 镜像 ✓ · 同族 stem 唯一 ✓ · 变体组在索引中 ✓（5 passed）
- 全库 lint 152/152 ✓；G-INDEX（新增常驻门）✓
