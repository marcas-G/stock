# Issue #24：D10 参考库准入门槛

## 决策

2026-09-25，用户选择以 `|resIC t| ≥ 3` 作为当前参考库候选集的操作性准入门槛。
99 项试验、B=300 的联合 max-T 审计估计临界值约为 3.45；因此采用 3.0
不代表已控制 FWER。

D10 判决、`factor admit` 和 `factor ref add` 统一要求同时满足
`|resIC t| ≥ 3`、`corr_max < 0.7`、`retention ≥ 0.5`。非有限诊断不能通过准入。
`ref add` 在备份和写入参考库前复核判决；低于准入条件的观察候选会被拒绝，
旧版手工 entry 参数不能绕过诊断。原有重复与冗余判决保留。

## 验证证据

- RED：[`ISSUE-24-red.log`](ISSUE-24-red.log) 记录实现前的准入边界和写入门失败。
- GREEN：[`ISSUE-24-related-green.log`](ISSUE-24-related-green.log) 记录相关回归，
  **139 passed**。
- 文档路径门命令：`platform/.venv/bin/python -m pytest platform/tests/test_doc_paths_exist.py -q`；
  原始输出见 [`ISSUE-24-doc-paths.log`](ISSUE-24-doc-paths.log)，**11 passed**。
- 入库车道的诊断口径已与 #22/#23 接通：daily spec 使用 `forward_return_1d`，
  weekly spec 使用其 `target`；冻结件记录 schema、frequency、fwd_col，旧口径
  冻结件仅重算诊断、不重复 final 登记。相关回归在 #22 记录中为 **155 passed**。

## 状态

实现已提交于 `7d3a2b9`，准入路径回归覆盖提交于 `9d61058`；用户选择的 `t=3` 仍只是当前操作门槛，
99 项/B=300 的 max-T 估计临界值约 3.45，多重性校正/FWER 结论仍未解决。
该统计学尾项继续由 review finding 跟踪；GitHub issue 回填与状态变更随本 MR 处理。
