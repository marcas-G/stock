# G-TOPO 回归：ashare_ingest 顶层模块重名

复验时间：2026-09-25 UTC（2026-09-26 Asia/Shanghai）。

## 发现与根因

本轮全仓 `make gates` 首次运行发现：

```text
platform/tools/ashare_ingest/check_inputs.py:
  import 'contracts' → research_flows
  （跨工具 import；共享代码请落 lib/）
```

`platform/tools/ashare_ingest/` 与 `research/tools/research_flows/` 各自存在顶层
`contracts.py`。工具拓扑检查器按全仓模块名索引报告了同名导入冲突。全仓 G-TOPO 重新运行后，这项违规为 0。

## 修复与回归

按 TDD 新增 `governance/ops/tests/test_check_tool_layering_regressions.py`，锁定
`ashare_ingest/check_inputs.py` 不得引入拓扑冲突。修改前定向测试失败并准确报告上面的
`contracts → research_flows` 冲突，原始输出见 [issue-topology-red.log](issue-topology-red.log)。

将平台工具的 `contracts.py` 改名为 `ashare_ingest_contracts.py`，并更新
`check_inputs.py` 与布局测试导入。验证结果：

- 拓扑回归 + `ashare_ingest` 布局测试：**6 passed**，
  [issue-topology-focused.log](issue-topology-focused.log)。
- 独立 G-TOPO：**0 处**，
  [issue-topology-gtopo.log](issue-topology-gtopo.log)。
- `make gates`：**结构门全绿**，
  [issue-followup-gates.log](issue-followup-gates.log)。

门运行同时发现 `knowledge/README.md` 的历史路径描述触发旧路径审计；将其改为按文档用途概述迁移结果，保留了历史背景而不在当前入口重复已迁出的路径。

## 范围

修复已包含在本 MR 的变更集中；这是 R43 门运行中发现并修复的拓扑回归，不属于 porteval 的 #33 原始模块冲突。

提交后补验：`platform/.venv/bin/python -m pytest -q research/tools/porteval/tests platform/tools/ashare_ingest/tests/test_layout.py governance/ops/tests/test_check_tool_layering_regressions.py`，结果 **18 passed**，原始输出见 `issue-38-40-final.log`。
