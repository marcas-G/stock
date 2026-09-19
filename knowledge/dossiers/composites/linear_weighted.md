---
name: linear_weighted
spec: research/composites/specs/linear_weighted.yaml
window: "2026-09-01 ~ 2026-09-16"
status: draft
snapshot: 历史快照（成员为 R34/C4 证据 fixtures 而非因子库因子；产物未随仓存档，见 §8）
---

# linear_weighted 合成分档案（Plan CX-C2 代表法示例）

日期：2026-09-19 ｜ 数据：CH 事实库（`FACTORLAB_DATA_BACKEND=ch`）｜ 评估：与 Factor 共用逐日口径

## 1. 成员与版本

> 成员顺序 = spec `members` 声明顺序 = 矩阵列顺序（design §3）。
> 版本数字自 `runs/platform/composites/linear_weighted/{provenance,summary}.json`。

| 位置 | 成员 ref | 类型 | artifact_hash | 角色/说明 |
|------|----------|------|---------------|-----------|
| 1 | `cx_demo_x1` | factor | `651a6a65d12799fb3f015a5b525f46bca8d92152860998aa14d84b74c91aa490` | `z(mom5)`：5 日动量截面标准化（证据 fixture） |
| 2 | `cx_demo_x2` | factor | `fefc8f1d509bde713b7d379edcdc3efe26046d9f4ac17d1cb5bed1d5e4181458` | `z(vol5)`：5 日已实现波动截面标准化（证据 fixture） |

| 版本项 | 值 |
|--------|----|
| definition_hash | `791d33a022682db6817b7f15af2fe818f880474f515c307edf4d8f78bd77bae5` |
| cache_key（H = Spec+Impl+Params+MemberHashes） | `eb548328350abc38d9b6ea2872985f1d86175920d7ca45469a6e74e30eb3b594` |
| implementation.source_hash / git_commit | `3e45c2463cd9b7c3bb199ee290eaf086224f5dd6d1c7843eb95db82fa4995e2b` / `78c7041baf37b911003a5d8297e48fa4b990fc53` |
| params_hash | `026448ba1a9875712ef78aa97d60be7612198778972ffbe364bcbcdd7c3b9378` |
| output_hash | `a3a94d9e70f051eb56e11ddd9fa40853e052f65a53997fcb307f34c0f7f671b0` |
| environment.lock_hash | `6b00f85b732425c8376cde68bc48b415a0d0f0ff3b48ac16572dbd5533df6257`（C2 已接线） |

## 2. 方法与参数

- **方法**：线性加权——`y = X @ w`（numpy 矩阵运算），`w = params.weights`（长度必须 = 列数）。
- **实现入口**：`research.composites.implementations.linear_rank:linear`
- **alignment**：`join: intersection` / `missing_policy: reject`
- **参数表**：

| 参数 | 值 | 含义 | 有效范围 |
|------|----|------|----------|
| `weights` | `[0.7, 0.3]` | 成员列权重（列序 = members 声明序） | 长度 = 成员数；缺省等权 |

## 3. 样本窗口

| 项 | 值 |
|----|----|
| 区间 | 2026-09-01 ~ 2026-09-16（成员运行窗 2026-08-03 ~ 2026-09-17 的交集参与评估） |
| 行数（成员交集后） | 435 |
| 平均股票数 | 15.0 |
| coverage | 0.9655（valid_rows 420 / total_rows 435） |
| target | `forward_return_1d` |

## 4. 评估（与 Factor 共用逐日评估口径）

> 数字自 `runs/platform/composites/linear_weighted/summary.json` 的 `evaluation` 段快照
> （C2 证据 `governance/evidence/verification/R34/c2/compose_linear_weighted.txt`）。

### 4.1 主指标

| 指标 | 值 |
|------|----|
| RankIC mean / t / IR | -0.032908 / -0.671688 / -0.126937 |
| 近 26 周 mean / t | -0.041071 / -0.864376 |
| PearsonIC mean / t | -0.038171 / -0.646379 |
| 分层 spread（D1−D10）/ 单调 | 0.000095 / true |
| 换手（monthly / quarterly） | 0.900000 / 0.900000 |

### 4.2 增量与基线（design §11 自动两类比较）

`incremental_vs_best_member`（对成员中最优者的增量）：

| 项 | 值 |
|----|----|
| best_member | `cx_demo_x2` |
| best_ic | -0.049617 |
| composite_ic | -0.032908 |
| delta | **0.016709** |

`baselines`（简单基线对照）：

| 基线 | IC | delta（composite − baseline） |
|------|----|-------------------------------|
| `equal_raw_average` | -0.070153 | 0.037245 |
| `equal_rank_average` | -0.049537 | 0.016629 |

### 4.3 判定

- RankIC 不显著（\|t\|≈0.67）；相对最优成员与两类基线均有正增量（0.017~0.037）。
  与 `linear_rank`/`cx_demo` 同成员同窗口——权重口径不同导致评估分化，证明参数真实生效
  （params_hash 不同、cache_key 不同）。
- 示例性质：用于验证 numpy 矩阵运算 + 参数化路径，不构成因子质量结论。

## 5. 稳定性

- **子期稳定**：`sign_consistent=0.536`、`direction_consistent_share=0.536`——方向稳定性一般。
- **换手/覆盖**：monthly 0.900 / quarterly 0.900；coverage 0.9655（交集 reject 口径）。
- **失效风险**：x1 动量正权重 0.7 在示范窗口内未体现正 IC；小样本（28 周）噪声主导。

## 6. 复现命令

```bash
# 0) 成员因子（R34/C4 证据 fixtures，非因子库正式档案）
FACTORLAB_DATA_BACKEND=ch governance/ops/heavy.sh platform/.venv/bin/factorlab run \
  governance/evidence/verification/R34/c4/fixtures/cx_demo_x1.yaml
FACTORLAB_DATA_BACKEND=ch governance/ops/heavy.sh platform/.venv/bin/factorlab run \
  governance/evidence/verification/R34/c4/fixtures/cx_demo_x2.yaml
# 1) 合成分（同一 spec/实现；cache miss 才重算）
governance/ops/heavy.sh platform/.venv/bin/factorlab compose research/composites/specs/linear_weighted.yaml
# 2) 索引/配对门
platform/.venv/bin/python research/tools/factor_lib/build_composite_index.py --check
```

原始输出：`governance/evidence/verification/R34/c2/compose_linear_weighted.txt`。

## 7. 迭代历史

| 日期 | 变体/版本 | 改动 | 增量 delta | 结论 |
|------|-----------|------|------------|------|
| 2026-09-19 | `linear_weighted`（初始） | C2 代表法示例（numpy `X@w`）真跑，weights=[0.7, 0.3] | 0.016709 | 不显著；示例/治理用途 |

## 8. 风险与备注

- **成员为证据 fixtures**：`cx_demo_x1/x2` 不在 `research/factor/` 因子库索引内、无因子档案。
- **产物未随仓存档**：`runs/` 被 gitignore；本档案数字为 C2 运行快照
  （`git_commit=78c7041b`，`environment.lock_hash` 已由 C2 接线）。复跑后按 §6 更新。
- **口径**：intersection + reject；权重作用于对齐后矩阵。

---
*档案规范见 `_template.md`；合成分设计与边界见
`knowledge/design/platform/specs/2026-09-19-composite-alpha-aggregation-design.md`；C2 验收见
`governance/evidence/verification/R34/c2/acceptance.md`。*
