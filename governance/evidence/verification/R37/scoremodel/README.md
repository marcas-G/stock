# R37 scoremodel M0–M4 阶梯（进行中）

模块：`research/tools/scoremodel/`（N/R/A/C + pipeline + run_ladder；32 tests，TDD+突变）
Spec/计划：`knowledge/design/research/{specs,plans}/2026-09-21-scoremodel*.md`

## 已完成
- Task1 N/C（robust z 标准化+校准）`39a4ab3`
- Task2 显式扩充 Core/Extended + 训练窗过滤 `131e8a1`
- Task3 聚合器 Ridge/HuberRidge/ElasticNet/PLS `6f12f0c`
- Task4 pipeline + 泄漏硬门 `8dd98b8`
- 修复：训练期按日分组标准化、O(M²) 去重→向量化、runner groups（本次提交）

## 部分结果（9 折 walk-forward，42 因子面板，517 测试日）
| 模型 | IC | tNW | 分档ρ | 自相关 | 耗时 |
|---|---|---|---|---|---|
| M0a Z→Ridge | 0.0974 | 18.6 | 1.00 | 0.508 | 24s |
| M0b Z→HuberRidge | **0.0991** | 17.7 | 1.00 | 0.537 | 30s |
| M1 Core+Filter→HuberRidge（λ=1e-3 固定） | 0.0619 | 19.2 | 1.00 | 0.218 | 1947s |
| M2 Extended+Filter→Ridge（λ=1e-3 固定） | 0.0342 | 20.1 | 0.94 | 0.107 | 1398s |

**观察**：固定 λ=1e-3 下，显式二阶扩充显著**低于** raw Huber 基线（0.099→0.062→0.034）——
疑为**正则强度未调**（1071/5460 特征 vs 40k 样本）+ 训练/预测标准化一致性修复前后的口径；
需加训练窗内 λ 选择后重跑，方可对"低阶非线性是否有价值"下结论。
