# R05 使用验证轮修复证据（R23/usage）

来源：`docs/reviews/r05-usage-2026-09-16/report.md` + `docs/reviews/findings.md` R05 段。

| Finding | 证据文件 | 要点 |
|---|---|---|
| R05-I1（分类表返回形态 + Struct 清晰拒绝） | `r05-i1-struct-gate.txt` | `BBANDS` 分类标注 `returns=struct`（生成表 8 个 struct / 3 个 multi + `PROBE_FALLBACKS` 记录探测失败）；带 process 的 spec lint 即报"算子 BBANDS（Struct 单列结构体）…请勿直接进 process…Plan 2/catalog"（替换 clip/quantile 天书）；`compute_formula` 直调与 process 前置 dtype 兜底门同文案 |
| R05-I2（spec 未知字段静默忽略 + op_meta 无效指引） | `r05-i2-spec-strict.txt` | `bogus_field: 123` → 加载期 pydantic `extra_forbidden` 点名拒绝；`op_meta` 非空 → "op_meta 暂未支持（Plan 2）"；未知算子文案指引公式内 `def` / `factorlab op add`，注明 op_meta 尚未实现（不再承诺未实现机制） |
| R05-M1（分类面不可发现） | `r05-m1-op-discovery.txt`、`r05-m1-op-list-catalog.raw.txt` | `op list --catalog` 528 条（含未注册库函数；name/partition/window/source/returns）；`op doc BBANDS` 未注册回退打印分类元数据与来源；默认 `op list` 注册面视图不变（3308B）；`interface.md` 已标注入口（完整同源归 Plan 2） |

门结果：

- `platform/.venv/bin/python -m pytest -q tests/test_op_classification.py tests/test_compute.py tests/test_cli_op_registry.py tests/test_cli_lint_v2.py tests/test_spec.py tests/test_spec_strict.py tests/test_semantics.py tests/test_future_gate_v2.py tests/test_open_ops_e2e.py` → 161 passed
- `make lint-factors` → 159 通过 / 0 失败（strict spec 解析下全库不变量保持）
- 生成表 `--check` 门：`python scripts/gen_op_catalog.py --check` → exit 0
