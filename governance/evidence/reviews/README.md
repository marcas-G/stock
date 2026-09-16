# 评审台账（reviews）—— 约定

**用途**：严格 review 的存放与修复追踪单点。reviewer 每轮追加；开发团队在 `findings.md` 对应行
记录修复说明；reviewer 复查后闭环。**append-only：不删除/不改写历史轮次与已登记行。**

## 结构

```
governance/evidence/reviews/
├── README.md                本文件（约定）
├── findings.md              台账 = 唯一状态源（reviewer 与开发团队都写这里）
└── rXX-YYYY-MM-DD-<标题>/    每轮评审
    ├── report.md            该轮完整报告（reviewer 只追加；勘误加在文末）
    └── evidence/            可复跑 probe 脚本 + 原始输出（索引见目录内 README.md）
```

> R24（2026-09-16）：原 `2026-09-15-open-operators/`、`2026-09-15-minute-execution/`、
> `2026-09-16-strategy-decomposition/` 三个**设计/实施方案**目录已迁
> `knowledge/design/workspace/`（非评审轮次，不占台账结构）。
> **本台账目录** `docs/reviews/` → `governance/evidence/reviews/`（同一搬迁）；历史报告/证据正文不改，按此映射解析。

## 方案批次与执行序（2026-09-16 整理）

| 序 | 方案 | 状态 | 执行入口 |
|---|---|---|---|
| 1 | **工具迁移**（8 工具+lib → `platform/tools/`；T1/T2 合并单解释器） | ✅ 已实施（R24 批次） | `r04-efficiency-2026-09-16/tools-migration-plan.md` |
| 2 | **策略配置化 Plan S**（六层 + YAML + L5） | ✅ 已实施并验收（R27 实现 / R28 验收；R06 复核） | `knowledge/design/workspace/2026-09-16-strategy-decomposition/plan.md` |
| 3 | **目录重整 R24**（契约/档案/治理/证据单点化） | ✅ 已实施（验收 `governance/evidence/verification/R24/`） | `r04-efficiency-2026-09-16/structure-plan.md` |
| — | open-operators Plan 1（开放算子底座） | ✅ 已实施（R22） | `knowledge/design/workspace/2026-09-15-open-operators/`；Plan 2/3 计划未写 |
| — | minute-execution（分钟级执行） | ✅ 已实施（R22） | `knowledge/design/workspace/2026-09-15-minute-execution/` |
| — | R04 快速项 P1-P5 | ✅ 已实施（R23） | `r04-efficiency-2026-09-16/report.md` |

**执行注意（2026-09-16 R06 更新）**：
- 序 1/2/3 **均已落地**（R24 批次 + R27/R28），原"不同窗口"约束解除；
- R06 复查结论：迁移面无 C、R24/R27/R28 声明全部实证通过；**门红（G-INDEX/G-ANNOTATE）与台账/门问题**
  见 `r06-2026-09-16-post-migration-review/report.md` §2/§6；
- `make test-research` 已为**单解释器单腿**（平台 venv 3.13）。

## 流程

1. **reviewer 出报告**：新建轮次目录 + `report.md`；在 `findings.md` 追加本轮全部 finding 行
   （状态 `open`），每行含 ID / 严重度 / 问题 / 关键位置 / 证据入口。
2. **开发团队修复**：在 `findings.md` 对应行的「修复说明」列写清 **commit SHA + 验证命令 + 原始输出路径**
   （符合本仓证据纪律：命令 + 输出 + 门结果），状态改 `fixed-claimed`。
3. **reviewer 复查**：实际重跑（不允许只看代码），在「复查」列写结论 + 证据，通过 → `verified`，
   未通过 → `reopened` 并在当轮 `report.md` 追加复查节。
4. **勘误**：报告有误时在当轮 `report.md` 文末加「勘误」节，并在 `findings.md` 行内注明；不直接改写原文。

## 状态词表

| 状态 | 含义 |
|---|---|
| `open` | 已登记，未修复 |
| `fixed-claimed` | 开发团队称已修复（须附证据） |
| `verified` | reviewer 复查通过 |
| `reopened` | 复查未通过，已回报 |
| `wontfix` | 明确不修（须写理由） |
| `deferred` | 挂起（须写触发条件） |

## 严重度

- **C（Critical）**：错误结果 / 数据损坏 / 未来函数 / 资金安全类，必须修。
- **I（Important）**：正确性风险、契约违反、测试盲区，应修。
- **M（Minor）**：文档、风格、优化项（**允许 级=M 入台账**——现状 `findings.md` 有 6 行 M；不稀释主线，主线看 C/I）。

## 复查命令（基线见各轮 report §0）

```bash
make gates                                                    # 常驻门
cd platform && .venv/bin/python -m pytest -q                  # 平台全量（~7.5min）
make test-research                                            # 工具/研究测试（单解释器：平台 venv 3.13）
```

## 给开发团队的提示

- 修复前先跑当轮 `evidence/` 里的对应 probe 复现；修完再跑一遍，前后输出都存档。
- probe 大多需 `cd platform` 后用 `.venv/bin/python` 运行；涉及 CH 的只读且限流；m8 的 probe
  会自建 `/tmp` 中间产物。
- 数据类修复（重灌）请在「修复说明」里写清影响行数、重灌命令与对账结果。
