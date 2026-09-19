# 合成分档案模板

> 本文件是 composite 档案的标准模板。**新建合成分档案**：复制本文件为
> `knowledge/dossiers/composites/<name>.md`，删除本说明块，按各章节填写。
> **可执行定义**：`research/composites/specs/<name>.yaml`（机器执行）与本文档（人读档案）
> 并行存放；两者必须一致，改 spec 后同步本档案。索引 `knowledge/index/composites.md`
> 由 `research/tools/factor_lib/build_composite_index.py` 生成（spec ↔ 档案成对，缺一即红）。

<!-- 元信息块（自包含、标签化）。status 取值：探索中 / 候选 / 观察中 / 已废弃 -->
---
name: <合成分名>                        <!-- 与 research/composites/specs/<name>.yaml 的 name 一致 -->
spec: research/composites/specs/<name>.yaml
window: "<YYYY-MM-DD> ~ <YYYY-MM-DD>"   <!-- 样本窗口（成员交集口径） -->
status: 探索中
snapshot: <历史快照说明>                 <!-- 可选：验证数字无 in-tree 产物/当前不可复跑时保留；重跑留证后删本行 -->
---

# <name> 合成分档案

## 1. 成员与版本

> 成员顺序 = spec `members` 声明顺序 = 矩阵列顺序（design §3，不得重排）；
> 版本数字自 `runs/platform/composites/<name>/provenance.json` 与 `summary.json`。

| 位置 | 成员 ref | 类型（factor/composite） | artifact_hash | 角色/说明 |
|------|----------|--------------------------|---------------|-----------|
| 1 | `<member_a>` | factor | `<sha256...>` | <信号含义> |
| 2 | `<member_b>` | factor | `<sha256...>` | <信号含义> |

| 版本项 | 值 |
|--------|----|
| definition_hash | `<sha256...>` |
| cache_key（H = Spec+Impl+Params+MemberHashes） | `<sha256...>` |
| implementation.source_hash / git_commit | `<sha256...>` / `<commit>` |
| params_hash | `<sha256...>` |
| output_hash | `<sha256...>` |
| environment.lock_hash | `<sha256...>`（未接线时如实写 null） |

## 2. 方法与参数

- **方法**：<一句话方法说明；数学式：`y = ...` 只用匿名列 x1..xK（design §2）>
- **实现入口**：`<entrypoint: module:compute>`（文件 `<research/composites/implementations/...>`）
- **alignment**：`join: <intersection>` / `missing_policy: <reject>`
- **参数表**：

| 参数 | 值 | 含义 | 有效范围 |
|------|----|------|----------|
| `<p1>` | `<v1>` | <含义> | <范围> |

## 3. 样本窗口

| 项 | 值 |
|----|----|
| 区间 | `<start> ~ <end>` |
| 行数（成员交集后） | `<rows>` |
| 平均股票数 | `<n_stocks_avg>` |
| coverage（evaluation.valid_rows / total_rows） | `<pct_valid>` |
| target | `<forward_return_...>`（与因子评估同口径） |

## 4. 评估（与 Factor 共用逐日评估口径）

> 数字自 `runs/platform/composites/<name>/summary.json` 的 `evaluation` 段快照；
> 重跑后用新 summary.json 数值替换并刷新 `updated_ts`/判定。字段语义与因子
> `summary.evaluation` 相同（design §11）。

### 4.1 主指标

| 指标 | 值 |
|------|----|
| RankIC mean / t / IR | `<mean>` / `<t>` / `<ir>` |
| 近 26 周 mean / t | `<recent_26w_mean>` / `<recent_26w_t>` |
| PearsonIC mean / t | `<pearson_mean>` / `<pearson_t>` |
| 分层 spread（D1−D10）/ 单调 | `<spread>` / `<true|false>` |
| 换手（monthly / quarterly） | `<m>` / `<q>` |

### 4.2 增量与基线（design §11 自动两类比较）

`incremental_vs_best_member`（对成员中最优者的增量）：

| 项 | 值 |
|----|----|
| best_member | `<member>` |
| best_ic | `<ic>` |
| composite_ic | `<ic>` |
| delta | `<delta>` |

`baselines`（简单基线对照）：

| 基线 | IC | delta（composite − baseline） |
|------|----|-------------------------------|
| `equal_raw_average` | `<ic>` | `<delta>` |
| `equal_rank_average` | `<ic>` | `<delta>` |

### 4.3 判定

- <结论：合成分是否优于最优成员与两类基线；是否显著（\|t\|>2）>
- <下一个动作：换窗口复验 / 换预处理 / 弃用>

## 5. 稳定性

- **子期稳定**：<近 26 周 vs 全期；sign_consistent / direction_consistent_share>
- **换手/覆盖**：<换手水平、coverage、缺失交集口径影响>
- **失效风险**：<逻辑依赖的市场环境、过拟合信号、成员相关性>

## 6. 复现命令

```bash
# 0) 成员因子（或成员 composite）先落产物
factorlab run <member_a.yaml>
factorlab run <member_b.yaml>
# 1) 合成分（同一 spec/实现；cache miss 才重算）
FACTORLAB_DATA_BACKEND=ch platform/.venv/bin/factorlab compose \
  research/composites/specs/<name>.yaml
```

## 7. 迭代历史

| 日期 | 变体/版本 | 改动 | 增量 delta | 结论 |
|------|-----------|------|------------|------|
| `<YYYY-MM-DD>` | `<name>`（初始） | <做了什么> | `<delta>` | <有效/无效/衰减> |

## 8. 风险与备注

- <成员是库内因子还是证据 fixtures；产物是否随仓存档>
- <口径风险：交集对齐、coverage、成员版本变化导致 cache 失效>
- <其他：与相关合成分/因子的关系>

---
*档案规范见本模板；合成分设计与边界见
`knowledge/design/platform/specs/2026-09-19-composite-alpha-aggregation-design.md`。*
