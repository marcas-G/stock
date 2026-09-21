# xscore（截面评分器）

**多因子值 → 截面分数** 的通用聚合模块（不碰 DSL、不做因子计算、不做持仓）。

```
X_t [N,K] → N(robust z) → R(identity|core|extended|MLP|SupAE) → A(ridge|huber|enet|pls) → C(robust z) → s_t [N]
```

- 分数语义：`s_i > s_j` ⇔ i 相对更优；`|s_i−s_j|` 越大差异越大；**不是预期收益率**；输出 ∈ [−3,3]
- 统计量只用训练窗（按日分组的截面标准化）；测试期只 transform
- 阶梯实验 M0–M4（先显式后学习）见 `run_ladder.py`；分组对照见 `run_split.py`
- 设计/计划：`knowledge/design/research/{specs,plans}/2026-09-21-xscore*.md`
- 测试：`platform/.venv/bin/python -m pytest research/tools/xscore/tests -q`
- 结果（研究侧）：`/data/students/gaolei/quantresearch/results/2026-09-xscore/`
