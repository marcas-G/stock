# R2 状态（树搬迁 + 平台分层收口）

日期：2026-09-15 · 分支：`restructure/monorepo` · 提交：`d45e59a`（搬迁）+ 本轮收口

## 做了什么

| 项 | 结果 |
|---|---|
| 三树落位 | `platform/`（267 文件）、`research/`（410）、根 `docs/`（工作区文档 + 验证证据）、`.claude/skills/` 3 文件 |
| 纯移动门 | 平台侧 **267 文件 blob 0 差异**；研究侧 406 文件 0 差异；31 份平台 spec **有意单点化**（只在 `platform/docs/superpowers`），研究独有 4 份进 `research/docs/superpowers/` |
| `.gitignore` 分家 | 根 = 新白名单（`data/`、`_archive/`、`projects/` 三重保护已复验）；`platform/` 与 `research/` 各自保留原版 |
| venv 迁移 | `.venv`（968M）同盘 mv → `platform/.venv`；离线 editable 重装 → `factorlab → platform/src/factorlab/__init__.py` ✓；`quant_core` 仍指 `projects/quant_core_shim`（未动） |
| emb(3.11) | **装不了** `factorlab`（requires-python ≥3.13）→ T2 继续走 `research/tools/_env.py` 注入（已改指 `platform/src`），实测解析正确、`platform_head()` 可用 |
| **core→config 欠账结清** | `RunContext`（唯一带 settings 默认值的装配容器）从 `core/engine/compute.py` 迁到 **`app/context.py`**；18 个引用文件同步改；core 内 `settings` 引用 = 0 |
| **config 副作用去除** | `settings.plugin_dir.mkdir()` 从 import 期移到装配点 `app.bootstrap.ensure_assembly()`（G-NOSIDE） |
| 废弃脚本删除 | `platform/scripts/gate_shared_core.sh`（两 worktree 时代产物） |
| 架构门重写 | `platform/tests/test_architecture.py`：旧的两条"分支门"换成**目录不互串**（G-COPY）+ 契约文档单副本；`_env` 两条门**去掉 skip**（单树下 research/ 永远在场，"跳过"会变成死门）；纯核静态门禁单加 `factorlab.config`；新增 config 叶门 + 无副作用门 → **10 passed / 0 skip** |
| 顺手修 | `core/domain/codes.py` 模块 docstring 改 raw（消除 `SyntaxWarning: invalid escape sequence '\d'`） |

## 门

| 门 | 结果 |
|---|---|
| 平台全量（新布局） | 首次：2482 passed / 2 failed（旧分支门）/ 15 skipped；收口后见 `R2-platform-pytest.log` |
| 研究侧 T2 / T1 | 183 passed / 1 skipped · 24 passed ✓ |
| 常驻门 `scripts/gates.sh` | G-COPY ✓ G-BOUNDARY ✓ G-PATHS ✓ G-VENV ✓；**G-LEGACY ✗ 42 处**（→ R6 工作清单，已可见清单前 5 条） |
| 数据零改动 | `data/` 未被任何操作触碰（全程只 mv 源码/文档）；`df` 基线见 R0 |

## 与计划的偏差

1. **emb editable 装不上**（计划里写了"两套解释器各自 editable 安装"）：包 `requires-python >=3.13` 使 pip 拒绝——这是既有事实，T2 一直靠注入而非安装。已在根 CLAUDE.md/README 记录。
2. **合并 2 的冲突量**（176 项而非预估 6 项）：见 R1 status。
3. `platform/CLAUDE.md` 的分支纪律段落在 R2 一并改写为**目录分权**（旧文引用 `../quant-platform-research` 已失效，属 G-LEGACY 命中项）。

## 下一阶段（R3/R4）前置条件

- [x] 单树可用：平台 CLI/测试、研究 T1/T2、`_env` 注入全部在新路径下实测通过
- [x] 常驻门脚本就位（结构门强制 + 数据接口门报告模式）
- [x] 数据接口基线（R0 `02-structure-and-dataiface.txt`）作为 R4 的"改前"对照


## 补充发现（2026-09-15，R4b 期间）：**未跟踪的运行时产物**也需要迁移

R2 的纯移动门只覆盖 **git 跟踪**文件；被 `.gitignore` 忽略的运行时产物仍留在旧 worktree
（git mv 不会搬它们）。实测清单与处置：

| 位置（旧 → 新） | 大小 | 处置 |
|---|---|---|
| `projects/quant-platform-research/tools/1m_features/output/` → `research/tools/1m_features/output/` | 244M（80 个月目录 + 2 个 merged parquet + state.json） | 同盘 `mv` ✓ |
| `…/tools/ch_ingest/state.json/`（119 个 `.done`） → `research/tools/ch_ingest/` | 480K | 同盘 `mv` ✓，随后按 R4b **迁移为 JSON**（旧目录留档 `state.json.legacy-20260915/`） |
| `…/tools/lob_fact/_batch/` | 不存在（无批算断点） | — |
| `platform` 侧 | 仅 `__pycache__`/`.pytest_cache`/`egg-info`（可再生） | 不搬（新位置会重新生成） |

**门缺口与补救**：纯移动门应显式包含"未跟踪运行时产物"一节；本轮以 R4b 的 state 迁移
（含留档）与 `1m_features output` 实测可用（check-day 依赖 merged parquet）作为验证。
