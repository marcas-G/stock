# 工具迁移 + 单解释器实施计划（TM）

**来源**：用户拍板（2026-09-16）"可复用基础设施→平台；成果→研究"，逐工具决策见
`tools-reorg-decisions.md`；T1/T2 合并选 B（试点已验证）。
**目标态**：`platform/tools/` = 数据生产线工具集（8 项 + `lib/`）；`research/` 只留成果与专属工具
（`strategies/`、`factor_lib/`、`factor/`、`docs/`）；**单解释器**（平台 venv 3.13）运行全部工具。
**配套**：`structure-plan.md`（文档树重整，独立窗口、不同批执行）。

## 基线（Task 0 采集，2026-09-16 试点实测）

| 项 | 基线 |
|---|---|
| 单解释器全量 | `platform/.venv/bin/python -m pytest research/tools -q` → **372 passed / 49.1s** |
| 原 T2 三样 | quark 9 / converters 11 / lob_fact 192（均平台 venv 绿） |
| 平台测试 | `cd platform && .venv/bin/python -m pytest -q` → 3099 passed / 13 skipped（R23 口径，跑前重采） |
| 门 | `make gates` 全绿 |
| 冻结 | `git status --porcelain` 为空；tag `pre-tools-migration` + bundle 到 `_archive/backups/` |

## Global Constraints

- **冻结窗口**：TM2 搬迁期间与团队提交/重任务错开（同 structure-plan 纪律）。
- **门先于搬迁**：先改门/测试路径判据，再 `git mv`，每步跑指定验证。
- **跨树搬迁例外**：一次 `git mv` 横跨 research→platform 两树，属主题单一的搬迁，
  允许单提交（提交信息注明 `refactor(tools): 工具归位 platform/tools` 例外原因）；
  其后修补按树分提交。
- **仓外同步是验收项**：`/data/students/gaolei/.claude/skills/factorlab-{ch-pipeline,data,dsl,evaluate,backtest}/`
  与 `stock/.claude/skills/factor-mine/` 中的工具路径引用。
- **零触碰**：`data/`、`platform/src`、`platform/tests`、`research/factor/`。

---

## TM1 单解释器化（低风险，可立即做）

1. `Makefile`：
   - `test-research` 收敛为单腿：`$(PLATFORM_PY) -m pytest research/tools -q`（TM2 后改 `platform/tools`）；
   - `EMB_PY` 定义删除（或注释"已弃用：T1/T2 合并"）；`help` 文案同步。
2. 文档同步（T1/T2 映射退役）：
   - `stock/AGENTS.md`「工具链速查」（研究测试 T1/T2 两行 → 一行）；
   - `research/AGENTS.md`「调试」节（T1/T2 说法）；`research/CLAUDE.md`、`research/README.md` 中的解释器映射段。
3. 验收：`make test-research` 绿（372）；`grep -rn "emb" Makefile` 无运行路径残留（仅注释）。

## TM2 工具搬迁 → `platform/tools/`

1. **搬迁清单**（`git mv`，单批）：

```
platform/tools/{converters,quark_download,ch_ingest,ashare_ingest,universe_stages,1m_features,lob_fact}/   ← research/tools/同名
platform/tools/lib/                                                                                       ← research/tools/lib
```

   保留在 `research/tools/`：`strategies/`、`factor_lib/`（+ 其 tests/notes）。
2. **支持文件**：
   - `research/tools/_env.py` → `platform/tools/_env.py`：路径常量 `parents[2]` → `parents[1]`；
     docstring 中 T1/T2 说明改为"单解释器；本模块只保留落位断言（防错内核）"；
   - 新建 `platform/tools/conftest.py`（原 `research/tools/conftest.py` 的路径插入逻辑，
     按新目录列 tools 子目录）；`research/tools/conftest.py` 保留给 `strategies/factor_lib`；
   - **相对层级审计**：`grep -rn "parents\[" research/tools platform/tools` 逐个核对（如 datapaths、
     `_env`、conftest）；`FACTORLAB_STOCK_ROOT`/`core.factio` 路径单点不变。
3. **门同步**（先改判据后移动）：
   - `scripts/check_tool_layering.py`：扫描锚点扩为双树（`platform/tools` + `research/tools`），
     R1-R4 规则不变；白名单/豁免按新路径重写；
   - `scripts/check_dataiface.py`：`RESEARCH_TOOLS` → 工具根列表（`platform/tools` 主 + `research/tools` 剩余）；
     已有白名单条目的 `research/tools/<tool>/...` 路径全量改为 `platform/tools/<tool>/...`；
   - `scripts/check_imports.py`：基线已扫 `platform/`+`research/`，预期自动覆盖；跑门确认无 `factorlab.*` 解析回归；
   - `Makefile` 其余路径（`index`、`reconcile` 等）改 `platform/tools/...`。
4. **仓内引用清扫**：
   `grep -rn "research/tools" --include="*.py" --include="*.md" --include="*.sh" --include="Makefile" .`
   → 命中处更新（预期：`docs/data-map.md`、`platform/docs/interface.md`、skills、verification 索引；
   历史冻结文档正文不改，只更新活文档）。
5. **平台约定**：确认 `platform` 侧 AGENTS/README 接受 `tools/` 顶层目录；确认 editable 安装不受影响
   （`pip install -e` 仍只装 `src/`）；`.gitignore` 检查 `platform/tools/**/__pycache__`。
6. **验收**：
   - `platform/.venv/bin/python -m pytest platform/tools -q` → 372 量级；
   - `cd platform && .venv/bin/python -m pytest -q` → 基线；
   - `make gates` 全绿；
   - 真实链路抽验：`convert_tick` 金样字节对照 + `ch_ingest/reconcile.py`（只读对账）。

## TM3 `_env` 退役

单解释器下 `ensure_platform()` 的"注入"职责消失，仅剩"落位断言"价值：
- **推荐**：保留 `_env.py` 断言职责（防 `pip` 装成别的副本／误挂 PYTHONPATH），删除 T1/T2 表述；
- 或（更彻底）：一个绿色周期后删除 `_env` 与 `conftest` 插入逻辑，靠 editable 安装保证解析。
- 由团队择一；计划默认按"保留断言"执行。

## TM4 全量验收与证据

- `docs/verification/R27/`（或下一轮号）：TM1/TM2/TM3 命令 + 原始输出 + 门结果 + 搬迁前后对照（372 清单哈希）；
- 台账/报告登记：`docs/reviews/` 对应条目；skill 文件更新 diff 存档。

## 风险表

| 风险 | 处置 |
|---|---|
| 仓外 skills 路径过期（ch-pipeline 等） | TM2 步骤 4 + 验收项；diff 留证 |
| 白名单/豁免遗漏 → 门红 | 门先改后搬；门红即修（可发现） |
| `parents[]` 相对层级变化 | 迁移前审计全量；`check-day`/对拍抽验 |
| 跨树单提交违反"一次一棵树" | 主题唯一例外，提交信息注明（先例：R19/R20 收编） |
| 与 structure-plan 同文件冲突（`Makefile`、`factor_lib` 路径） | **不同窗口**执行；TM1 先落地，structure-plan 以其为基线 |
| `__pycache__`/`fixtures` 随迁 | 搬迁前清理 `__pycache__`，`fixtures` 属金样随工具 |
