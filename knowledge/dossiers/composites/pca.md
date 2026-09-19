---
name: pca
spec: research/composites/specs/pca.yaml
window: "2026-09-01 ~ 2026-09-16"
status: draft
snapshot: 未真跑（平台 venv 缺 scikit-learn，pending #18 锁环境欠账；装库后按 §6 转真跑）
---

# pca 合成分档案（Plan CX-C2 代表法示例）

日期：2026-09-19 ｜ 状态：**缺库未真跑**（无产物/无评估数字，如实登记）

## 1. 成员与版本

> 成员顺序 = spec `members` 声明顺序 = 矩阵列顺序（design §3）。

| 位置 | 成员 ref | 类型 | artifact_hash | 角色/说明 |
|------|----------|------|---------------|-----------|
| 1 | `cx_demo_x1` | factor | `651a6a65d12799fb3f015a5b525f46bca8d92152860998aa14d84b74c91aa490` | `z(mom5)`：5 日动量截面标准化（证据 fixture） |
| 2 | `cx_demo_x2` | factor | `fefc8f1d509bde713b7d379edcdc3efe26046d9f4ac17d1cb5bed1d5e4181458` | `z(vol5)`：5 日已实现波动截面标准化（证据 fixture） |

| 版本项 | 值 |
|--------|----|
| definition_hash / cache_key / output_hash | **未产出**（缺 sklearn，compose fail fast，无 runs 产物） |
| implementation.source_hash | `ridge_pls_pca.py` 当前内容（重跑时以 provenance 为准） |
| params_hash | 未产出 |

## 2. 方法与参数

- **方法**：PCA 第一主成分得分（sklearn `PCA(n_components=1, svd_solver="full")`，确定性）——
  预处理列 z-score（ddof=0），`y = fit_transform(Xs)[:, 0]`（无监督，stateless `X→y`，
  design §12 边界内，design §6 框架不解析）。
- **实现入口**：`research.composites.implementations.ridge_pls_pca:pca`
- **alignment**：`join: intersection` / `missing_policy: reject`
- **参数表**：无（`params: {}`；`n_components` 固定 1）。

## 3. 样本窗口

| 项 | 值 |
|----|----|
| 区间（意图口径） | 2026-09-01 ~ 2026-09-16（成员交集） |
| 行数 / 平均股票数 / coverage | 未真跑（预期与同成员示例一致：435 / 15.0 / 0.9655） |
| target | `forward_return_1d` |

## 4. 评估（与 Factor 共用逐日评估口径）

**未评估**：平台 venv 无 scikit-learn（`lib_probe.txt: sklearn absent`），`factorlab compose`
在 compute 入口 fail fast（exit 1，不落半成品）：

```
ImportError: scikit-learn 未安装：pca 示例需要 sklearn（平台 venv 不新增依赖；装入 scikit-learn 后本示例即可运行）
```

原始输出：`governance/evidence/verification/R34/c2/compose_pca.txt`。
`incremental_vs_best_member` / `baselines` 待真跑后由 summary.json 自动产出（design §11）。

## 5. 稳定性

未真跑，无稳定性结论。真跑后按模板 §4/§5 补数字与判定。

## 6. 复现命令

```bash
# 0) 成员因子（R34/C4 证据 fixtures）
FACTORLAB_DATA_BACKEND=ch governance/ops/heavy.sh platform/.venv/bin/factorlab run \
  governance/evidence/verification/R34/c4/fixtures/cx_demo_x1.yaml
FACTORLAB_DATA_BACKEND=ch governance/ops/heavy.sh platform/.venv/bin/factorlab run \
  governance/evidence/verification/R34/c4/fixtures/cx_demo_x2.yaml
# 1) 装入 scikit-learn（平台 venv 不新增依赖是 C2 期约束；装库后本示例即可真跑）
platform/.venv/bin/python -m pip install scikit-learn
# 2) 合成分
governance/ops/heavy.sh platform/.venv/bin/factorlab compose research/composites/specs/pca.yaml
# 3) 索引/配对门
platform/.venv/bin/python research/tools/factor_lib/build_composite_index.py --check
```

## 7. 迭代历史

| 日期 | 变体/版本 | 改动 | 增量 delta | 结论 |
|------|-----------|------|------------|------|
| 2026-09-19 | `pca`（初始） | C2 示例落库（sklearn 惰性 import，缺库 fail fast） | — | 缺库未真跑；装库后可转真跑 |

## 8. 风险与备注

- **未真跑**：本档案无评估数字；不得被引用为质量结论。C2 测试 `importorskip` 自动在装库后转真跑。
- **成员为证据 fixtures**：`cx_demo_x1/x2` 不在 `research/factor/` 因子库索引内、无因子档案。
- **方向**：第一主成分符号由 SVD 约定决定，评估方向字段可能与经济直觉相反（真跑后核对）。

---
*档案规范见 `_template.md`；C2 生态验收见 `governance/evidence/verification/R34/c2/acceptance.md`。*
