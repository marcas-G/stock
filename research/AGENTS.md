# 研究侧工作流与技能

本 worktree（research 分支）的协作代理约定。硬性纪律见 `CLAUDE.md`。

## 技能（Skills）

- `.claude/skills/factor-mine/` —— **挖因子循环**（本 worktree 专属）：
  种子因子 → 隐含假设分析 → 假设审核（语义矛盾/数据可实现）→ 变异/精确化 →
  实现 → subagent 审核 → 跑结果 → 按模板入库。
- 平台侧技能（用户级 `~/.claude/skills/`）：`factorlab-dsl`（写 spec/DSL）、
  `factorlab-data`、`factorlab-ch-pipeline`、`factorlab-backtest`、`factorlab-evaluate`、
  `quark-share-download`（网盘下载）。

## 标准工作流

1. **假设先行**：新因子/策略先写清"市场行为假设 + 为什么可交易"，再动手实现。
2. **TDD**：工具代码先失败测试再实现（金样/逐值断言，硬编码存根必败）。
3. **对拍**：与平台内核共享入口的批算（如 1m 特征）必须过 `check-day` 单日对拍
   （max|Δ|=0）才算完成。
4. **留证**：运行产出（state.json、sha256 摘要、对拍报告）落对应工具的 output/notes；
   关键结论进因子档案 `knowledge/dossiers/factors/<族>/<stem>.md`（front matter + 六节模板）；
   新增/改名后重生成索引 `knowledge/index/factors.md`（有 byte-equality 门）。
5. **提交**：改动进 research 分支；平台侧改动去 main worktree（见 CLAUDE.md）。

## 调试

- 数据问题先查 `../../governance/workspace/data-map.md`（哪个表是谁生产的、生产者在哪）。
- 批算异常先看 flock 单写者门与 `_SUCCESS` 事务边界（半成品分区不入库）。
- 数字不对时先确认用的是**哪个内核**（`_env.py::ensure_platform()` 的落位断言就是为此存在；
  单解释器：平台 venv 3.13，`emb` 已退役）。
