# R37 AE 聚合 42 因子（GPU）证据

- 代码：`quantresearch/lab/autoencoder42/`（模块 + `run_experiment.py`；71 passed 骨架测试）
- 结果：`quantresearch/results/2026-09-autoencoder-42/`（REPORT.md / manifest.json / metrics.json）
- 面板：42 成员 `_5y`，intersection+reject，2023-05-25..2026-07-31（770×4691，3.46M 行）；目标 forward_return_1d
- 协议：滚动 252 训练 → 63 测试 → 步进 63（9 折）；rank-z 特征；ridge 方向头；训练只用截止 t 数据
- GPU：RTX 6000（torch 2.2.1+cu121，vllm1203 env），AE 3 个 k 共约 13 分钟

## 主结果（517 有效测试日，同覆盖）

| 模型 | IC | t(NW) | 增量 IC/t（vs ref_all42_5y_rk） |
|---|---|---|---|
| 等权秩 WF | 0.0820 | 15.18 | 0.0267 / 6.62 |
| PLS(1) | 0.0815 | 17.09 | **0.0393 / 9.00** |
| AE k=5 | 0.0718 | 14.69 | 0.0199 / 4.30 |
| PCA k=3 | 0.0724 | 15.55 | 0.0215 / 4.64 |
| 零假设探针（移位） | 0.0016 | 1.68 | — |

## 结论

AE ≈ PCA（无非线性增量），低于等权秩与 PLS(1)；**不建议引入 AE 聚合**；PLS(1) 值得作为
下一候选（t 最高、增量最大、换手最低）。零假设探针 ≈0，装配/评估链无隐性泄漏。

原始输出：`run-full.log`（各模型/折/耗时）；`metrics.json`（逐月序列与每折 records）。
