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

## 组合层（周频长多，同窗同域等权基准，7bp 单边）

| 分数器 | 域 | 年化 | 基准 | 超额 | IR | 换手 |
|---|---|---|---|---|---|---|
| M0a Z→Ridge | 全市场 | 38.2% | 21.2% | **+13.5%** | **1.79** | 0.151 |
| M0a | Q1-Q3 | 42.9% | 27.8% | +11.3% | 1.82 | 0.158 |
| M0b Z→HuberRidge | 全市场 | 36.5% | 21.2% | +12.0% | 1.57 | 0.152 |
| M0b | Q1-Q3 | 41.7% | 27.8% | +10.2% | 1.60 | 0.157 |

同口径参照（autoencoder42）：等权秩 +4.8%/0.69、PCA k3 +6.3%/0.98、PLS +3.4%/0.51。
→ 目标 y 截面 rank 化 + 全因子判别的 ridge 是最强单模型；M1–M4 待 λ 选择后重跑。

## 执行时点口径：close（旧）vs next-open（T 信号→T+1 开盘）

| 分数器 | 成交 | 域 | 年化 | 基准 | 超额 | IR |
|---|---|---|---|---|---|---|
| M0a | close | 全市场 | 38.2% | 21.2% | +13.5% | 1.79 |
| M0a | open | 全市场 | 31.3% | 19.7% | +9.4% | 1.08 |
| M0a | close | Q1-Q3 | 42.9% | 27.8% | +11.3% | 1.82 |
| M0a | open | Q1-Q3 | 35.7% | 22.7% | +10.2% | 1.44 |
| M0b | open | 全市场 | 30.2% | 19.7% | +8.2% | 0.94 |
| M0b | open | Q1-Q3 | 34.8% | 22.7% | +9.3% | 1.27 |

执行时点成本：全市场 -4.2pp/-0.71IR；小中盘 -1.1pp/-0.38IR（反转信号隔夜衰减更伤大盘段）。
脚本 `quantresearch/lab/autoencoder42/portfolio_nextopen.py`；数据 `open_adj_42_5y.npz`。
