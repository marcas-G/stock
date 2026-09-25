# Issue #33 处理与证据记录

复核日期：2026-09-25。问题为 G-TOPO 检出 porteval 的通用模块名 `engine` 与其他工具冲突。

## 处理

提交 `dfbb59f` 将 `research/tools/porteval/engine.py` 改名为 `pv_engine.py`，并同步更新 `run.py` 与引擎测试中的导入。当前 porteval 目录中不再有 `engine.py`；生产入口使用 `pv_engine`。

## 验证

- `platform/.venv/bin/python governance/ops/check_tool_layering.py`：**G-TOPO 0 处**（2026-09-25 复跑）。
- #33 初次定向复核时，`platform/.venv/bin/python -m pytest research/tools/porteval/tests -q` 为 **7 passed**（5 条运行时告警，来自零方差收益下 IR/Sharpe 的非有限值）。这组输出对应 #38 的严格 JSON 回归加入前的测试快照。
- R43 后续为 #38 增加零方差与严格 JSON 测试；持久输出 [R43 porteval 测试日志](issue-38-porteval-tests.log)记录当前测试集 **12 passed**。这些新增断言处理的是零方差指标序列化问题，不改变 #33 的模块重名修复结论。
- [R43 全门日志](issue-37-gates.log)也记录 G-TOPO **0 处**。

## 结论

现有证据确认 #33 所述的 porteval 模块名冲突已由 `dfbb59f` 处理，且当前 G-TOPO 检查通过。该结论仅覆盖模块拓扑冲突；零方差下非有限 JSON 指标由独立 #38 跟踪。GitHub issue 尚需回填提交和验证结果后收尾。
