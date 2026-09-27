# R43 测试记录

执行分支：`restructure/monorepo`；基线提交：`a48ff57`。

## 验证结果

- `bash governance/ops/verify.sh --profile fast`：rc=0。平台 3,039 passed、1,105 skipped、1 deselected；platform tools 914 passed、7 deselected；research tools 174 passed、10 skipped、3 deselected；governance ops 219 passed。
- `bash governance/ops/verify.sh --profile deep`：rc=0。平台 4,128 passed、17 skipped；CH integration 14 passed、11 skipped；platform tools 920 passed；research tools 184 passed；governance ops 219 passed。全库 factor lint 284/284；宿主门全绿。
- `make gates`（新增 R43 finding 后）：rc=0；G-REVIEWS 识别 127 条 finding，ID、状态、引用路径与统计检查通过。

深档原始输出：[`verify-deep-2026-09-25.log`](verify-deep-2026-09-25.log)。

Issue #37 的失败定位、R42 处理提交、定向复验和关单建议见
[`../ISSUE-37.md`](../ISSUE-37.md)。

## 新发现：R43-PORT-I1

运行 `platform/.venv/bin/python governance/evidence/verification/R43/tester/repro_porteval_nonfinite_json.py`，基于现有 `test_benchmark_domain_equal` 平收益用例，得到：

```text
metrics= {'ir': nan, 'vol': 0.0, 'sharpe': inf, 'excess': 0.0}
strict_json=REJECTED: non-standard JSON constant: NaN
```

可复现脚本：[`repro_porteval_nonfinite_json.py`](repro_porteval_nonfinite_json.py)；原始输出：[`repro_porteval_nonfinite_json.txt`](repro_porteval_nonfinite_json.txt)。已登记于 `governance/evidence/reviews/findings.md`，GitHub issue [#38](https://github.com/marcas-G/stock/issues/38)。
