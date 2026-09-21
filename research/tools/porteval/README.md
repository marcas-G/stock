# porteval（组合评估器）

**分数 → 仅多头组合 → 评估** 的研究级组合层（xscore 的下游）。

- 设计/参数冻结：`knowledge/design/research/specs/2026-09-21-porteval-design.md`
- 核心：`engine.py`（`PortfolioConfig` + `simulate` 纯函数）；CLI：`run.py`
- 关键决策：仅多头；默认 `exec=open`（T 信号→T+1 开盘）；容量默认关闭（给 `--aum` 才启用）；
  涨跌停默认 `block`（禁买/禁卖+冻结）；成本 7bp 单边；基准同域等权、同窗比较
- 产 `portfolio.json`（统计+分年）+ `manifest.json`（输入指纹/参数/代码/git）
- 测试：`platform/.venv/bin/python -m pytest research/tools/porteval/tests -q`
- 被流水线调用：`research/tools/xscore/pipeline/portfolio_once.py`（薄壳转发）
