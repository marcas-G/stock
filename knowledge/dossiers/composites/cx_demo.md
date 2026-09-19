---
name: cx_demo
spec: research/composites/specs/cx_demo.yaml
window: "2026-09-01 ~ 2026-09-16"
status: draft
snapshot: 历史快照（成员为 R34/C4 证据 fixtures 而非因子库因子；产物未随仓存档，见 §8）
---

# cx_demo 合成分档案（Plan CX-C1/C4 示例）

日期：2026-09-19 ｜ 数据：CH 事实库（`FACTORLAB_DATA_BACKEND=ch`）｜ 评估：与 Factor 共用逐日口径

## 1. 成员与版本

> 成员顺序 = spec `members` 声明顺序 = 矩阵列顺序（design §3）。
> 版本数字自 `runs/platform/composites/cx_demo/{provenance,summary,artifact}.json`。

| 位置 | 成员 ref | 类型 | artifact_hash | 角色/说明 |
|------|----------|------|---------------|-----------|
| 1 | `cx_demo_x1` | factor | `651a6a65d12799fb3f015a5b525f46bca8d92152860998aa14d84b74c91aa490` | `z(mom5)`：5 日动量截面标准化（证据 fixture） |
| 2 | `cx_demo_x2` | factor | `fefc8f1d509bde713b7d379edcdc3efe26046d9f4ac17d1cb5bed1d5e4181458` | `z(vol5)`：5 日已实现波动截面标准化（证据 fixture） |

| 版本项 | 值 |
|--------|----|
| definition_hash | `080c56fc40fec51db1560e2fb996d03f92cca1a17ebcb1c977f12025797325aa` |
| cache_key（H = Spec+Impl+Params+MemberHashes） | `8e1f1243bd4702a93669b963535515a5785e216e642fd314f5d5807e847c8423` |
| implementation.source_hash / git_commit | `4d7c1cb3d65ac634abf2872be4cfb36fc83a30d99bec8f067c24ed0e64e30e7a` / `9528f0117352571b535bbaa114e2db54b8167480` |
| params_hash | `44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a`（空参数集） |
| output_hash | `ccce52c0b0ec6fc1b4523a8cf7173e3257e547c1e146523ed5fd6780fc5ffceb` |
| environment.lock_hash | `null`（运行时 C2 environment hash 尚未接线；如实现归档该字段） |

## 2. 方法与参数

- **方法**：线性反向组合——`y = 0.5·x1 − 0.5·x2`（匿名列，design §2/§7 硬边界；
  实现文件 `research/composites/implementations/cx_demo.py`）。
- **实现入口**：`research.composites.implementations.cx_demo:compute`
- **alignment**：`join: intersection` / `missing_policy: reject`（design §5：只保留所有成员
  都有有效值的 `(date, code)`）。
- **参数表**：无（`params: {}`；权重以代码常量表达，改权重 → implementation hash 变）。

## 3. 样本窗口

| 项 | 值 |
|----|----|
| 区间 | 2026-09-01 ~ 2026-09-16（成员运行窗 2026-08-03 ~ 2026-09-17 的交集参与评估） |
| 行数（成员交集后） | 435 |
| 平均股票数 | 15.0 |
| coverage | 0.9655（valid_rows 420 / total_rows 435） |
| target | `forward_return_1d` |

## 4. 评估（与 Factor 共用逐日评估口径）

> 数字自 `runs/platform/composites/cx_demo/summary.json` 的 `evaluation` 段（同一
> 逐日口径；字段语义与因子 `summary.evaluation` 相同，design §11）。快照随 R34/C4 留证。

### 4.1 主指标

| 指标 | 值 |
|------|----|
| RankIC mean / t / IR | 0.019515 / 0.283098 / 0.053501 |
| 近 26 周 mean / t | 0.026786 / 0.374326 |
| PearsonIC mean / t | 0.051565 / 0.728038 |
| 分层 spread（D1−D10）/ 单调 | 0.000600 / true |
| 换手（monthly / quarterly） | 0.833333 / 0.766667 |

### 4.2 增量与基线（design §11 自动两类比较）

`incremental_vs_best_member`（对成员中最优者的增量）：

| 项 | 值 |
|----|----|
| best_member | `cx_demo_x2` |
| best_ic | -0.049617 |
| composite_ic | 0.019515 |
| delta | **0.069133** |

`baselines`（简单基线对照）：

| 基线 | IC | delta（composite − baseline） |
|------|----|-------------------------------|
| `equal_raw_average` | -0.070153 | 0.089668 |
| `equal_rank_average` | -0.049537 | 0.069053 |

### 4.3 判定

- 合成分 RankIC 绝对值仍小、t≈0.28 **不显著**；但相对最优成员（x2）与两类基线均有
  正增量（delta 0.069~0.090），符合"反向波动 + 正向动量"组合假设的方向。
- 样本仅 28 周 / 15 只主板 fixtures，**不构成统计结论**——R34/C1/C4 为链路验收。
  下一个动作：换成员池（真因子库）与更长窗口复验；成员版本变则 cache 自动失效重算。

## 5. 稳定性

- **子期稳定**：全期 28 周，`sign_consistent=0.5`、`direction_consistent_share=0.5`
  ——方向稳定性一般，评估不显著（见 §4.3）。
- **换手/覆盖**：monthly 换手 0.833（高分位轮动快）；coverage 0.9655（交集 reject 口径，
  缺失不填充）。
- **失效风险**：成员为 5 日短窗信号，动量/波动结构切换时组合方向可能失效；过拟合风险
  因示范窗口小而被放大。

## 6. 复现命令

```bash
# 0) 成员因子（R34/C4 证据 fixtures，非因子库正式档案）
FACTORLAB_DATA_BACKEND=ch governance/ops/heavy.sh platform/.venv/bin/factorlab run \
  governance/evidence/verification/R34/c4/fixtures/cx_demo_x1.yaml
FACTORLAB_DATA_BACKEND=ch governance/ops/heavy.sh platform/.venv/bin/factorlab run \
  governance/evidence/verification/R34/c4/fixtures/cx_demo_x2.yaml
# 1) 合成分（同一 spec/实现；cache miss 才重算）
governance/ops/heavy.sh platform/.venv/bin/factorlab compose research/composites/specs/cx_demo.yaml
# 2) 索引/配对门
platform/.venv/bin/python research/tools/factor_lib/build_composite_index.py --check
```

原始输出：`governance/evidence/verification/R34/c4/`（成员运行、compose、E2E 指标）。

## 7. 迭代历史

| 日期 | 变体/版本 | 改动 | 增量 delta | 结论 |
|------|-----------|------|------------|------|
| 2026-09-19 | `cx_demo`（初始） | 首建（Plan CX-C1 T5/C4 示例）：`0.5·x1 − 0.5·x2` | 0.069133 | 链路验收；不显著，留档 |

## 8. 风险与备注

- **成员为证据 fixtures**：`cx_demo_x1/x2` 仅存在于
  `governance/evidence/verification/R34/c4/fixtures/`，**不在 `research/factor/` 因子库
  索引内、无因子档案**；本档案不构成对因子库成员的质量背书。
- **产物未随仓存档**：`runs/` 被 gitignore；本档案数字为 R34/C4 运行快照
  （`git_commit=9528f011`，`environment.lock_hash=null`——C2 未接线）。复跑后按 §6 更新。
- **口径**：intersection + reject，无填充/无成员 dropout（design §5）。
- **C3 边界**：参考库增量 verdict（D10）未实现，登记为后续（不阻塞本档案）。

---
*档案规范见 `_template.md`；合成分设计与边界见
`knowledge/design/platform/specs/2026-09-19-composite-alpha-aggregation-design.md`。*
