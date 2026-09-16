# Task 9 — 存量 152 因子零迁移回归

**命令**：`cd platform && .venv/bin/python -m pytest tests/test_regression_152.py -q`
**结果**（原始输出 `test_regression.txt`）：`2 passed in 352.00s`（exit 0）。
**对比口径**：6 个代表 spec 改动前后各跑一次（真实 CLI + `FACTORLAB_DATA_BACKEND=ch`），
逐项断言 `evaluation.ic.{mean,t_stat,ir}` |Δ| ≤ 1e-9 且 `n_weeks` 相等。
**产物**：`<name>.json` = 改动后 summary（直接拷自 `platform/results/<name>/summary.json`）；
`ic_delta.tsv` = before/after 逐值对照。

## 零迁移结论

6/6 代表 spec 全部逐值一致（|Δ| = 0.000e+00，n_weeks 相等）→ 管线重构（分类表/
语义推断/未来门/分块）对存量因子**零行为漂移**。数据面等价代表说明见
`../00-baseline/README.md`（CH 无 stock_st/index_daily；pb/circ_mv 全 null）。
