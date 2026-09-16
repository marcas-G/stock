# 策略配置化详细方案（2026-09-16）

**这是什么**：FactorLab 策略体系的三件事——**六层漏斗规范（L0-L5）**、**策略 YAML 配置化（D4）**、
**L5 执行/风控规则边界**。目标：策略像因子一样"一处定义（YAML）、机器可跑、人可阅读、可版本化"。
**给谁**：开发团队按 `plan.md` 实施（7 个任务，TDD，逐任务可独立验收）；复查走 `governance/evidence/reviews/README.md` 流程。
**状态**：**已实施并验收**（Plan S 实现 `7e03acb`/`126def4`；验收 `governance/evidence/verification/R28/`——
首例 `low_lottery_top30_weekly` 5 决策 / NAV +2.2257% / 176 笔 / 11 帧与落盘逐帧一致；R06 复查复核通过）。
用户可见接口契约（YAML 示例 / 平台 API / 研究 CLI）见 `plan.md` §接口契约。
**关系**：
- 与 `../2026-09-15-open-operators/`、`../2026-09-15-minute-execution/` 独立；NEXT_WINDOW 分钟执行由本计划
  Task 1/2 直通（`ExecutionSpec.minute_window`）；
- `research/tools/strategies` **留 research**（工具迁移决策）；`factor_lib` 扩策略索引（同留 research）；
- R24 目录重整已落地：本计划档案/索引落在 `knowledge/` 新坐标（`knowledge/dossiers/strategies/`、
  `knowledge/index/strategies.md`）。

## 文件

| 文件 | 内容 |
|---|---|
| `design.md` | 设计规格：六层漏斗 §1、层间契约 §2、现状对照 §3、缺口 G1-G5 §4、D4 YAML 草案 §6 |
| `plan.md` | 实施计划 Plan S：7 个任务（契约+加载器 → 运行器 → 研究入口 → 档案/索引门 → 首例 → L5 规则 → 后置登记），含精确路径与失败测试要点 |

## 关键决策（先读这个）

1. **六层顺序约束**：L1 池先于 L3 打分（截面只在池内算）；L2 regime 只做"信号门控"（不改变截面范围，
   D1）；L5 是唯一有状态层。
2. **YAML 组合两份 spec，不合并 schema**：平台 `StrategySpec` 明令禁 execution 字段（`extra=forbid`）
   → 加载器产出 `StrategyDoc{StrategySpec + ExecutionSpec + date/regime/rules/universe_override}`。
3. **M8 无 CLI（勿发明）**：运行器 = `app/strategy/run.py` API + 研究侧薄入口
   `research/tools/strategies/run_strategy.py`。
4. **L5 分层落地**：V1 只做 `max_hold`（调仓日近似，研究侧）；`stop_loss/take_profit` 需成交明细/日内
   语义 → 平台化另立（触发条件登记在 plan Task 7）。
5. **首例**：`low_lottery_top30_weekly`（`max_effect_20d_high` top30 等权周频，干净窗口 2025-03）。

## 验收锚点

- 加载器 fail-fast：未知键/类型/`NEXT_WINDOW` 无窗口/非 null `rules` 均有明确报错；
- 运行器：链上逐值断言 + 持久化 round-trip + `NEXT_OPEN` 既有链零差异 + CA Gate 不吞错；
- 建档：`docs/index/strategies.md` 字节级 `--check`，spec↔档案双向齐全；
- 首例真 CH 跑通（内存护栏 + 原始输出留证）；`NEXT_WINDOW` 配置可加载（V1 接口闭环）。
