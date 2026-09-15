# R03 工具链修复证据（R03-I1 ST 降级开关 + R03-M2 skill 文档漂移）

对象：`docs/reviews/r03-mining-2026-09-16/report.md` §R03-I1 / §R03-M2、
`docs/reviews/findings.md` R03 段。
修复提交：I1 `79d256a`（platform）、M2 skill `55e754c`、M2 模板 `dde8aae`；
本目录为 docs 树证据提交。`platform/docs/interface.md` §4.2 ST 降级段已随
`0455502`（另一 R03 agent 的 I3 提交，staged 时一并带上）落入 HEAD（见
`I1-interface-doc.txt`）。

## R03-I1 — exclude_st 缺 stock_st 的显式降级开关（默认仍 fail fast）

根因：`resolve_universe_frame` 在 `exclude_st=true` 且库中无 `stock_st` 表时
直接 ValueError（fail fast，正确）；但 CH 是当前唯一可用后端且无 `stock_st`
（`SELECT name FROM system.tables WHERE database='factorlab'` 实测无此表），
152/152 库内 spec 原样不可跑，挖矿被迫写无 ST 影子 spec（ST 语义敏感因子被污染）。

修法：新增 `Settings.st_degrade`（env `FACTORLAB_ST_DEGRADE`，默认 `"fail"`）。
`allow` 且缺表且 `exclude_st=true` 时显式降级：`warnings.warn`（`STDegradedWarning`，
文案含"ST 未知按非 ST 处理，结果为无 ST 口径"）+ `is_st=null`（unknown ≠ false）+
`in_universe` 不做 ST 过滤；开关关闭仍 ValueError。空 `stock_st` 表 / 请求日期在
coverage 外（后两种 unknown）不受开关影响，仍 fail fast。降级事实审计：
`st_degrade_active(spec, rd, override=...)` + `run_factor`/`run_factor_minute`
summary 恒写 `st_degrade: true/false`；CLI 运行同时由 Python 默认 warning 通道
打印告警（真实 CH 输出见下）。

| 证据 | 内容 |
|---|---|
| `I1-real-ch-default-fail.txt` | 真 CH（`FACTORLAB_DATA_BACKEND=ch`）跑 `rules: {exclude_st: true}` spec、**不开开关** → `错误: exclude_st 需要 stock_st 表…不能默认所有股票非 ST`，exit=1 |
| `I1-real-ch-allow-success.txt` | 同 spec 加 `FACTORLAB_ST_DEGRADE=allow` → STDegradedWarning ×2（signal/label uf）+ 正常评估（5530 候选、112141 行、n_weeks=3）；`summary.json` 审计字段 `st_degrade: true` |
| `I1-red-universe.txt` | HEAD 源码（`/tmp/opencode/r03_i1/head2`，src 为 git HEAD 副本）+ 新测试 → collection `ImportError: cannot import name 'STDegradedWarning'`（rc=2） |
| `I1-red-run-minute.txt` | HEAD run.py + 修复后 config/universe + 新测试 → run 级 6 failed（`KeyError: 'st_degrade'`）+ 分钟 1 failed（同因）；默认 fail 的 2 例守卫通过 |
| `I1-green-required.txt` | `cd platform && pytest -q tests/test_pit_universe.py tests/test_run_factor.py tests/test_e2e.py` → **188 passed, 3 skipped**（e2e 因真实 duckdb 库不存在 skip） |
| `I1-green-minute.txt` | `pytest -q tests/test_minute_engine.py` → **16 passed** |
| `I1-interface-doc.txt` | interface.md §4.2 降级段（含挖矿口径）已随 `0455502` 入库的 diff 摘录 |
| `gates-structure.txt` / `gates-full.txt` | `bash scripts/gates.sh` → 结构门全绿 + 数据接口门 ENFORCED 全绿（rc=0） |

测试（新增，均双腿 duckdb|ch）：
- `platform/tests/test_pit_universe.py`：
  `test_st_degrade_allow_warns_is_st_null_no_st_filter`（告警 + is_st 全 null +
  本应 ST 的 000001 照常 in_universe）、`test_st_degrade_switch_off_still_fails_fast`、
  `test_st_degrade_allow_no_effect_when_st_table_present`（有表不告警且 ST 真过滤）、
  `test_st_degrade_not_active_without_exclude_st`。既有
  `test_st_table_missing_exclude_st_fails`（默认 fail）继续通过。
- `platform/tests/test_run_factor.py`：
  `test_run_factor_st_degrade_allow_summary_and_warning[None|outputs1 × duckdb|ch]`
  （单/多输出 summary 标记 + warning）、`test_run_factor_st_degrade_default_fails`
  （默认入口 fail fast）、`test_run_factor_st_degrade_noop_when_table_present`
  （开关无副作用：候选集不变、ST 股 000001 被剔出面板、st_degrade=false）。
- `platform/tests/test_minute_engine.py`：
  `test_minute_st_degrade_allow_summary_and_warning`（分钟 summary 同样审计）。

存根判别：把降级判定换成硬编码（如 summary 恒写 true 或 `resolve_universe_frame`
恒返回）时，"有表无副作用"用例（ST 股必须被剔出面板）与"真 CH allow"用例
（真实评估数字）都会失败——断言的是数据行为而非字段存在。

## R03-M2 — factor-mine skill / 档案模板文档漂移

根因（3 处 + 同族扩展）：种子选择脚本仍按平铺 `docs/factors/*.md` glob；路径示例
缺族目录；duckdb 指南过期（平台 duckdb 库不存在，现为 CH）。同族漂移还存在于
`assumption-review.md`/`code-review.md` 的契约文档前缀、`factors/_template.md`
的档案/spec 路径、skill 参照设计路径。`research/README.md`/`CLAUDE.md` 已是
单仓单树 + CH 口径，核对无同类漂移（未改）。

| 证据 | 内容 |
|---|---|
| `M2-doc-sync.txt` | 3 个修复文件的完整 diff；遗留漂移模式扫描 skill 目录为空；全部引用路径存在性 OK；种子脚本真实运行（seed candidates=157 族/stem，无档案缺 spec）；CH `system.columns` 查询实测可用 |

## 未解决点

- **真实 `stock_st` 源仍不可得**：本机 CH 无 ST 快照（teajoin 环境注入表），
  `TOOLS-A` 侧灌入是完整口径闭环的唯一路径。平台侧开关 + summary 审计 + 文档
  保证的是：库内 spec 可**原样**（保持 `exclude_st: true`）显式降级跑通且不静默；
  降级 run 与未来 ST 过滤 run 口径不同、不得混比（已写进 interface.md）。
- **分钟链**同样支持降级与 summary 标记（共用 PIT resolve），但降级 run 的
  数据口径说明以 interface.md §4.2 为准。
- 根 `AGENTS.md` "已知的别踩"仍有 "exclude_st 一类依赖平台库的 universe 规则
  不可用"（现已可选显式降级）——按本轮 docs 边界（只改 interface.md 该段）未动，
  留给工作区文档例行同步。
