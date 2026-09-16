# R05 使用验证报告（2026-09-16）

- **目的**：使用开发团队的新工作（开放算子、ST 降级开关、分钟链改动）做真实 CLI 验证，找出问题（非执行方角色）
- **方法**：真实 `factorlab lint/run`（CH 后端）；全部证据为实测命令与输出
- **快照**：HEAD 约 `45fcd0e`（团队并发提交，R23/R04 快速项进行中）

## 验证结论（正/反）

| 项 | 结论 | 证据 |
|---|---|---|
| ST 降级开关 `FACTORLAB_ST_DEGRADE=allow` | ✅ 正常：原库 spec（带 exclude_st）直跑成功，summary 含 `st_degrade: True` | `max_effect_20d: n_weeks=178`；`summary.st_degrade=True` |
| 开放算子真实可用面 | ✅ `ts_arg_max(close,20) + ts_corr(close,volume,20)` 端到端跑通 | 6 个月窗 `n_weeks=20, ic=+0.0235`（R03 前两者写不出来） |
| `BBANDS` 结构化返回 | ✅/⚠️ 无 process 时 artifact 边界报错**清晰**：`signal 列 dtype 必须为 numeric，实际 Struct({upperband,...})`；带 process 链时错误晦涩（Struct+clip 天书） | `cap_bb` / `cap_real3` 输出 |
| 我的计划文档举例 `ts_quantile` | ❌ 不存在于 polars_ta 0.5.17 → **已给 design/plan 加勘误**（团队 R22 已记录偏差并替换） | `ImportError`；`R22/open-operators-summary.md` §偏差 1 |

## 新发现

**R05-I1 分类表未标注"返回形态"（struct/多列）**
- `BBANDS(close,20)` 过 lint（分类为 ts/arg:1），但返回 `Struct(upperband/middleband/lowerband)`；
- 无 process：artifact 边界清晰拒绝（好）；**带 process（winsorize 等常规链）：报错为 Struct 上的 clip/quantile 天书**（用户难以定位"这是多列返回"）；
- 建议：分类/conformance 增加返回形态标注（scalar/multi/struct），lint 对已知 struct 函数给提示；catalog 注明字段访问方式。
- 证据：`cap_bb`（清晰版）、`cap_real3`（晦涩版）。

**R05-I2 spec 顶层未知字段静默忽略（含 op_meta 被吞）**
- 实测 `bogus_field: 123` 过 lint（无任何提示）；同理 `op_meta:` 被静默忽略；
- 而引擎报错文案明确引导"未知算子请补 op_meta"（semantics.py:217-218）——**指引了尚未实现的机制（Plan 2），且用户照做后无声失败**；
- 建议：spec strict（extra=forbid）或未知字段 warn；报错文案注明"op_meta 暂未支持/上市时间"，避免误导。
- 证据：`cap_bogus.yaml` OK；`cap_opmeta.yaml` 报未知算子；`semantics.py:217` 的指引文本。

**R05-M1 新解锁面不可发现（已知，团队口径）**
- `factorlab op list` 仍为注册面视图（57）；`docs/catalog.md` 仅含 `ts_corr`，没有 512 条分类面的检索入口；
- 团队已在 R22 summary §偏差 5 记账（归 Plan 2：op list/catalog 同源）；本条登记跟踪，不重复计责。
- 影响：用户/AI 无法从 CLI/文档发现"现在能写什么"，只能读生成表源码。

## 勘误（reviewer 自身）

- R01 时代口头结论"ts_quantile 库里明明有"有误——库中只有 `cs_quantile`/`cs_quantile_zscore`（截面），无时序 quantile；
- `docs/reviews/2026-09-15-open-operators/{design,plan}.md` 已加勘误节（不改历史正文）。

## 附：实测命令

```bash
# ST 降级
FACTORLAB_ST_DEGRADE=allow FACTORLAB_DATA_BACKEND=ch factorlab run research/factor/volatility/max_effect_20d.yaml
# 开放算子
factorlab run /tmp/opencode/mine/cap_real4.yaml      # ts_arg_max+ts_corr → n_weeks=20
factorlab run /tmp/opencode/mine/cap_bb.yaml         # BBANDS → Struct dtype 拒绝
factorlab run /tmp/opencode/mine/cap_opmeta.yaml     # op_meta 被吞 → 未知算子
factorlab lint /tmp/opencode/mine/cap_bogus.yaml     # bogus 字段静默通过
```
