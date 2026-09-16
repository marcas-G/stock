# R07 工程/质量侧缺口审计 — 证据索引（eng）

- 审计时间：2026-09-16 18:28–18:35（+08:00）
- 审计 HEAD：`639bf3f`（工作区只读；本目录为唯一写入）
- 方法：逐条复跑命令 + 原始输出留档；G-LEGACY 另做 /tmp 副本注入实验

| # | 缺口 ID | 现状判定 | 证据文件 |
|---|---|---|---|
| 1 | R06-M1 档案旧坐标 | 部分修（2 处），残余 app/results 6 文件 + 根 results/ 164 处；根因 `_template.md:75` | `01-m1-dossiers-oldcoord-scan.txt`、`01-m1-target-existence.txt`、`01-m1-tracked-status-template.txt` |
| 2 | R06-M2 README 陈旧 | 结构/状态/命令三处已修（10a7767）；残余：evidence 索引说明与 M 规则（见 #3） | `02-03-readme-verification.txt` |
| 3 | R06-M3 M 行矛盾 | 未处置：README:64「M 不进台账」vs 台账 6 行 M（R03-M1..M5、R05-M1）全 verified | `02-03-readme-verification.txt` |
| 4 | R06-M4 R24 顶层 README | 已闭环：`R24/README.md` 存在（7544B，bcfe38d）；注：R28 亦无顶层 README | `02-03-readme-verification.txt` |
| 5 | R06-M9 G-LEGACY 盲区 | 部分：门对 tracked 生效（当前实测 RED，抓到 extcnt.md:85）；盲区=untracked / 裸 `results/` / `research/tools/lib/` / 整文件豁免 | `04-glegacy-structure-gate.txt`、`04-glegacy-probe.txt`、`04-glegacy-blindspots.txt` |
| 6 | pending #11④ catalog 拆分 | 开放：单模块 853 行；`core/catalog_model`、`adapters/catalog_docs` 均不存在 | `05-catalog-split-status.txt` |
| 7 | pending #12① 表名常量单点 | 开放；门实测 **70 处**（登记 68）；工具 docstring/gates 注释仍写「460 处」 | `06-dataiface-report.txt`、`06-dataiface-drift.txt` |
| 8 | pending #12② 工具改名 | 开放且阻塞点仍在：仓内旧名未改；技能副本已过期（sha 不同、缺 quark_client/quark_share）；SKILL.md 旧名 | `07-quark-rename-status.txt` |
| 9 | pending #12③ run.py 子命令 | 开放：0 个 run.py；仅 `run_1m_feature.py` 有 subparsers；未找到独立命名规范文档 | `08-tools-runpy-status.txt` |
| 10 | pending #17 行序不定 | 开放：`on_result` 完成序回调 + `MonthPartitionSink` append-only，无排序 | `09-converters-roworder.txt` |
| 11 | pending #18 venv 复现 | 开放：声明 19（登记 20）vs venv 72 包；全仓无 uv.lock/requirements（uv 可用） | `10-venv-repro.txt` |
| 12 | pending #21 compact_lob 自举 | 开放：直跑 `ModuleNotFoundError: core`（docstring 内自举）；`-m store.compact_lob` 正常 | `11-compact-lob-direct-run.txt` |

## 关键现场数字
- `bash governance/ops/gates.sh --structure` → **exit 1**（G-LEGACY 1 处 + G-INDEX 漂移 + G-ANNOTATE 1 份，后两者挖矿在途）。
- G-LEGACY 活引用：`knowledge/dossiers/factors/volatility/max_effect_20d_extcnt.md:85`（tracked，门已抓）。
- untracked 未被门抓到的 `platform/results` 旧引用：`intraday_high_time.md:88`、`intraday_tail_amt_share.md:87`、`max_effect_20d_zmax.md:86`、`symrun_r30_flip.md:97`、`symrun_r30_streak.md:110`。
- 根 `results/` 引用：164 行 / 158 文件（其中 5 处日期为 2026-09-16，迁移后仍在新写）。
- `/tmp/opencode/glegacy-probe`：tracked=抓到；untracked=漏；整文件豁免=漏；`research/tools/lib/` 与裸 `results/` 无 pattern=漏。
