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

> **总入口：`STATUS.md`**（评审轮次 R01-R09 × 全部方案族 × 完成度 × 待办分派；本表保留为简版）。

| 序 | 方案 | 状态 | 执行入口 |
|---|---|---|---|
| 1 | **工具迁移**（8 工具+lib → `platform/tools/`；T1/T2 合并单解释器） | ✅ 已实施（R24 批次） | `r04-efficiency-2026-09-16/tools-migration-plan.md` |
| 2 | **策略配置化 Plan S**（六层 + YAML + L5） | ✅ 已实施并验收（R27 实现 / R28 验收；R06 复核） | `knowledge/design/workspace/2026-09-16-strategy-decomposition/plan.md` |
| 3 | **目录重整 R24**（契约/档案/治理/证据单点化） | ✅ 已实施（验收 `governance/evidence/verification/R24/`） | `r04-efficiency-2026-09-16/structure-plan.md` |
| 4 | **缺口整改 Plan G**（R07：门回绿/判据加固/契约同步/数据三件/口子收口） | 待执行 | `r07-2026-09-16-gap-audit/improvement-plan.md` |
| 5 | **评估指标 v2**（口径修订 + 增强） | **已拍板**（D1=B；D2/D4/D5/D6 批；D3 本质；D7 垃圾不保存；D8 补灌；**D9 逐日默认+周频可选**；**D10 参考库 daily/minute**；**D11 因子侧纯净：forward 固定 1d、E3/E4 归策略层**；**D12 去 Rust 叙事+Task13 合并单一实现**；E 顺序 E4→E3→E2→E1）；待执行 | `knowledge/design/platform/{specs,plans}/2026-09-16-factorlab-eval-metrics-v2*` |
| 6 | **同花顺模拟炒股接入 Plan T**（网页接口：盘后算单→次日开盘模拟下单→台账对账；参考 `Cfu4536/ths_simulated_API`） | 立项（设计+计划就绪，待执行） | `knowledge/design/research/{specs,plans}/2026-09-17-ths-simulated-api*` |
| 7 | **数据质量与清洗流水线 Plan DQ**（FATAL/ERROR/WARN + 分区门 PASS/DEGRADED/FAIL + health 双维度 + 读取 fail-closed + 全史体检只审不改） | **M1 终审通过；M1.5 完成（acceptance ①②③ PASS + M1.5c 解阻断）**：18 日 RCA（3 恢复/4 隔离/11 例外）、adj 字段级（8,235 回补）、1.25M 分群（SOURCE 1.15M/EXPECTED 104k）；门控作用域=更新增量 + `quality_backlog` 披露；`make data-update`（daily）**rc=0**、health PASS、verify rc=0；**残余 3,828 挂 M3，Quark cookie 待刷新（外部）**；证据 R32/R33 | `knowledge/design/platform/{specs,plans}/2026-09-18-data-quality-pipeline*` |
| — | open-operators Plan 1（开放算子底座） | ✅ 已实施（R22） | `knowledge/design/workspace/2026-09-15-open-operators/`；Plan 2/3 计划未写 |
| — | minute-execution（分钟级执行） | ✅ 已实施（R22） | `knowledge/design/workspace/2026-09-15-minute-execution/` |
| — | R04 快速项 P1-P5 | ✅ 已实施（R23） | `r04-efficiency-2026-09-16/report.md` |

**执行注意（2026-09-16 R06 更新）**：
- 序 1/2/3 **均已落地**（R24 批次 + R27/R28），原"不同窗口"约束解除；
- R06 复查结论：迁移面无 C、R24/R27/R28 声明全部实证通过；**门红（G-INDEX/G-ANNOTATE）与台账/门问题**
  见 `r06-2026-09-16-post-migration-review/report.md` §2/§6；
- `make test-research` 已为**单解释器单腿**（平台 venv 3.13）。

## GitHub 追踪（2026-09-19 起）

- **对外追踪面 = GitHub Issues**（`marcas-G/stock`）：团队在 issue 里回填修复说明、走 PR（`Fixes #N`）；
  本地 `findings.md` 仍为**证据原件**（append-only，不因迁 GitHub 删改历史）。
- **同步命令**：`platform/.venv/bin/python governance/ops/sync_review_issues.py --apply`
  （缺省 dry-run；幂等靠 issue 正文标记 `<!-- finding:ID -->` / `<!-- plan:ID -->`；
  token 从 `GH_TOKEN` 或 `~/.config/factorlab/github_token`（0600）读取，绝不入库）。
- **现状（seed）**：12 个 issue（#5-#16）＝ R08 finding×2 + 方案待办×10；
  标签 `kind:*/severity:*/status:*/round:*/paused`；里程碑 `R08 / Plan DQ / Plan T / Plan 开放算子 / Plan 分钟执行 / Plan CX / 治理`。
- **V1 限制**：同步器目前只做"创建 + 幂等查重"；`verified → 评论+关单`、`reopened → 重开` 待 V2
  （在此之前：状态以本地台账为准，GitHub 侧手动关单或等 V2）。
- **并发双建（2026-09-20 已发生一次，见 closed #21）**：`findings.md` 推送会触发
  workflow 自动同步；若此时本地再跑 `sync_review_issues.py --apply`，两通道在
  「查重→创建」窗口赛跑会各建一条。约定：**推送后等 workflow 跑完**，本地先 dry-run
  看"已存在"，确需手动再 `--apply`。发现双建：保留编号小的一条，另一条评论+`duplicate` 关单。
- **本机推送通道（2026-09-19 实测）**：本服务器出网**阻断 `github.com:443`**（git https 报
  `Empty reply from server`；`api.github.com` 正常）。git 一律走 **SSH-over-443**：
  `~/.ssh/config` 已配 `Host github.com → HostName ssh.github.com / Port 443`，
  key `~/.ssh/id_ecdsa_github`（账号 key 名 `gaolei-gpu-server`，OpenSSH 8.2 不支持 ed25519 故用 ECDSA）；
  `origin` 已切 `git@github.com:marcas-G/stock.git`。**本机不要再用 https 推拉**。
- **自托管 runner（2026-09-19）**：`gpu-server-1`（标签 `self-hosted,Linux,X64,factorlab,ch`），
  宿主直连 CH + 在盘数据；systemd **user** 服务 `actions-runner.service`
  （目录 `/data/students/gaolei/actions-runner`，日志 `journalctl --user -u actions-runner`，
  已 `--disableupdate`——本机下不了 runner 升级包，升级走 API asset 手动换版）。
  配套 workflow `.github/workflows/selfhosted-verify.yml`（手动 dispatch / 夜间 03:00 /
  repository_dispatch；**故意不挂 `pull_request`**——公开仓 + 自托管的分叉 PR 是任意代码执行
  风险面，GitHub 官方建议自托管仅配私有仓；**建议后续把仓库转私有**）。
  自查口径（2026-09-19 实测）：平台单测 = **默认后端**（无 CH，~8min，CH 腿自动 skip）；
  CH 集成 = `cd platform && FACTORLAB_DATA_BACKEND=ch FACTORLAB_ST_DEGRADE=allow
  FACTORLAB_MINUTE_UNCOVERED=drop .venv/bin/python -m pytest -q -m integration`（14 passed /
  11 skipped / ~5min；11 skip 为需 tick 用例）。**给整包强加 ch 是错误口径**（大量红 + 数小时）。
  **首跑全绿（2026-09-20）**：run `35455396087`，约 27min——平台 3834 passed / CH 集成
  14 passed+11 skipped / platform/tools 877 passed（排除 2 条：tick 挂死 + pan_update 同根
  守卫）/ research/tools+governance 同 hosted 口径。排障发现：heavy.sh 环境注入会污染测试
  （injected `FACTORLAB_MAX_MEMORY` 破 env 断言、`POLARS_MAX_THREADS=8` 破 cash bridge 精确
  相等校验 → 后者已立 R36-CI-I1 / issue #20）；故 CI 腿不加 heavy.sh/线程 env，内存由
  memguard 兜底。

## 证据收口（方案 A，2026-09-21）

- 轮次证据只收**命令 + 结论 + 关键数字 + 指路**；运行产物拷贝（factor/composite/strategy/审计 sidecar 的
  JSON/CSV）**不进 git**——产物本体在 `quantresearch/` 或 `runs/`，证据里引用路径。探针/门的小文本原始输出可留。

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
| 8 | **Composite / Alpha 聚合层 Plan CX**（多因子→截面分数→组合/回测） | **C1+C4+C2+C3+C4b 已交付并复查可收口**（compose/引用/score_weighted/top_k_buffered/market_cap_weighted/档案索引门/环境 hash；linear 真跑、Ridge·PLS·PCA 待装库；170+479+338 级测试；证据 R34；残余 D10 verdict/装库） | `knowledge/design/platform/{specs,plans}/2026-09-19-composite-alpha-aggregation*` |
