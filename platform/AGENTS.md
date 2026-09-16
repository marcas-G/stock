# 工作流与技能约定

本仓库的协作代理（Claude Code / Codex 等）工作流约定。硬性工程纪律见 `CLAUDE.md`。

## 技能（Skills）

- **平台侧**（本仓库无关分支差异）：用户级技能 `~/.claude/skills/`：
  `factorlab-dsl`（因子 DSL/spec 编写）、`factorlab-data`、`factorlab-ch-pipeline`、
  `factorlab-backtest`、`factorlab-evaluate`、`quark-share-download`（网盘批量下载）。
- **研究侧技能**（单树后在仓库根）：`.claude/skills/factor-mine/`
  （挖因子循环：种子→假设审核→变异→实现→审核→入库）。
- 技能发现：Claude Code 自动发现上述目录；新增技能放对应位置并在此登记。

## 架构速览（2026-09-12 重构后）

五层：`surfaces → app → ports → core`，I/O 全在 `adapters/`；`core/` 是纯计算核（有静态+运行双门）。
改代码前先定位改动属于哪层；跨层调用只允许图示方向（详见 `CLAUDE.md` 架构分层节）。

## 标准工作流

1. **需求澄清**：写代码前先厘清需求与边界（不明确就问，不猜）。
2. **计划**：多步骤任务先出实施计划（含验收标准），再动手。
3. **TDD**：红-绿-重构——先写失败测试（断言来自设计文档/规格，不是实现），
   再写最小实现；替换为存根必败的测试才算有效。
4. **执行**：逐任务推进；每个任务收尾跑相关测试。
5. **审查**：关键改动做代码审查（正确性/复用/简化）。
6. **收尾**：全量 `pytest -q` 通过 + 文档同步 + 按分支纪律提交。

## 调试与验证

- 修 bug：先复现（最小用例）→ 定位根因 → 修复 → 回归测试锁死。
- 完成前必须验证"真的通了"：跑真实入口（CLI/API）+ 真实数据，不用"测试通过"
  替代"端到端可用"。
- 证据留痕：关键验证命令与输出存档（工作区级见 `stock/governance/evidence/verification/`）。
