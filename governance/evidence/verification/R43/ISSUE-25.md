# Issue #25 关闭后 CLI 遗留项处理记录

复核日期：2026-09-25。#25 已关闭时的评论指出，若 `flab data status --pretty` 仍不可用，应作为单独小项跟踪。本次确认两种入口都返回 `USAGE`，且 `data.status` 自描述未列出 `pretty` 参数。

## 修复

`platform/src/factorlab/research/data_meta.py` 为 `data.status` 注册布尔 `pretty` 参数（默认 `false`），并将其加入自描述示例。实现复用既有 `data_status` handler 的输出格式参数；未改变数据状态计算或 JSON envelope。

## 验证

- 新增测试先红后绿：`test_cli_research_data_status_accepts_pretty` 验证命令输出缩进 JSON、内容仍可解析；`test_describe_data_status_documents_pretty_flag` 验证参数出现在自描述中。
- 定向复验：`governance/ops/heavy.sh platform/.venv/bin/python -m pytest -q platform/tests/test_research_data.py -k 'status_accepts_pretty or describe_data_status_documents_pretty'`：**3 passed**。原始输出：[issue-25-data-status-focused.log](issue-25-data-status-focused.log)。
- data CLI 测试文件：`governance/ops/heavy.sh platform/.venv/bin/python -m pytest -q platform/tests/test_research_data.py`：**66 passed, 4 skipped**。原始输出：[issue-25-data-status-tests.log](issue-25-data-status-tests.log)。

## 结论与范围

该关闭评论留下的 CLI 小项已由仓内改动修复并通过定向及文件级测试。原 GitHub issue #25 的健康发布验收结论不变；本项作为其关闭后的补充处理记录，不据此重开或扩大 #25 范围。源码改动尚未提交。
