# 评审台账（reviews）—— 约定

**用途**：严格 review 的存放与修复追踪单点。reviewer 每轮追加；开发团队在 `findings.md` 对应行
记录修复说明；reviewer 复查后闭环。**append-only：不删除/不改写历史轮次与已登记行。**

## 结构

```
docs/reviews/
├── README.md                本文件（约定）
├── findings.md              台账 = 唯一状态源（reviewer 与开发团队都写这里）
├── 2026-09-15-open-operators/  设计/实施方案（非评审轮次；开发团队实施入口）
├── 2026-09-15-minute-execution/  分钟级执行设计/实施计划（开发团队实施入口）
├── 2026-09-16-strategy-decomposition/  策略六层漏斗分解规范（设计，待评审）
└── rXX-YYYY-MM-DD-<标题>/    每轮评审
    ├── report.md            该轮完整报告（reviewer 只追加；勘误加在文末）
    └── evidence/            可复跑 probe 脚本 + 原始输出（索引见目录内 README.md）
```

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
- **M（Minor）**：文档、风格、优化项（只登记在各轮 report，不进台账，避免稀释主线）。

## 复查命令（基线见各轮 report §0）

```bash
make gates                                                    # 常驻门
cd platform && .venv/bin/python -m pytest -q                  # 平台全量（~7.5min）
make test-research                                            # T2 (emb) + T1 (平台 venv)
```

## 给开发团队的提示

- 修复前先跑当轮 `evidence/` 里的对应 probe 复现；修完再跑一遍，前后输出都存档。
- probe 大多需 `cd platform` 后用 `.venv/bin/python` 运行；涉及 CH 的只读且限流；m8 的 probe
  会自建 `/tmp` 中间产物。
- 数据类修复（重灌）请在「修复说明」里写清影响行数、重灌命令与对账结果。
