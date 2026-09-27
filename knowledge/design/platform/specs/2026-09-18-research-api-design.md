# 研究员接口封装（Research API）设计文档

日期：2026-09-18 ｜ 状态：**已拍板**（用户：命令面做全、AI 为使用者）
背景：研究员（使用者=AI agent）目前要串通"查数据→写因子→评估→回测→看报告"需接触深层模块 API、
venv/环境变量、表名映射、内存护栏、heavy 闸等概念；`import factorlab` 无顶层导出、无 notebook/示例。
本设计提供**面向 agent 的统一研究门面**：CLI（`flab`）+ 同源 Python 面，统一 JSON 契约、零概念负担、自描述。

## 1. 目标与范围

- 一条链可用：`flab study run <factor.yaml> [--strategy y.yaml]` → 因子计算/评估 → 入库冗余检验 → 策略回测 → 报告 URL；全程 JSON。
- 覆盖全库数据查询（17 张 CH 表 + PIT 池），命令面**做全不丢能力**。
- 默认值内建：后端 ch、结果目录、内存护栏、重任务闸；AI 不需理解这些也能安全跑。
- 自描述：`flab describe --json` 为命令/参数/默认值/错误码/输出 schema 唯一权威，命令注册表单点驱动。

**非目标**：不做 MCP Server（后续可加）；不做 Web 交互编辑；不改既有 `factorlab` 命令语义（新命令组并存）。

## 2. 架构

- 新门面包 `platform/src/factorlab/research/`（仅依赖 app/adapters/ports，与 `surfaces/cli` 同向，不反向依赖）：
  - `registry.py`：命令注册表（名称/参数/默认值/输出 schema/示例/错误码），describe/help/手册生成单点。
  - `envelope.py`：统一 JSON 信封、错误码→退出码映射、`--pretty`。
  - `data.py` / `factor.py` / `strategy.py` / `report.py` / `study.py`：门面函数（装配默认值，不实现业务）。
  - `cli.py`：`factorlab research` 命令组挂载进现有 CLI（同框架），逐命令薄委托。
- 短入口 `flab`：`governance/ops/install_flab.sh` 安装 `~/.local/bin/flab`；内部注入 venv/PATH、
  `FACTORLAB_DATA_BACKEND=ch`、结果目录、内存护栏 env，重命令自动过 heavy 闸。
- Python 面 `from factorlab.research import ...`：返回 `Envelope` dataclass（`.ok/.data/.artifacts/.warnings/.error`），
  数据带 `.frame()`/`.to_polars()` 便捷；默认值与 CLI 完全一致。

## 3. 命令面（全量）

**通用**
| 命令 | 作用 |
|---|---|
| `flab version` | 版本 |
| `flab describe [--command <name>] [--json]` | 自描述：命令目录/单命令 schema/错误码表/示例 |
| `flab health [--json]` | CH 连通·内存·磁盘·护栏·数据新鲜度一览 |

**data（读全库）**
| 命令 | 作用 |
|---|---|
| `flab data tables` / `schema <t>` / `status` | 表清单（行数/最新日期/列语义）/ 单表列语义 / 新鲜度+交易日缺口 |
| `flab data calendar [--start --end]` | 交易日历 |
| `flab data daily --codes ... [--start --end] [--view raw\|qfq\|hfq\|pit_qfq] [--cols] [--out]` | 日K（含复权视图） |
| `flab data minute --codes --start --end [--session] [--out]` | 分钟线 |
| `flab data tick --codes --date --kind trades\|orders\|snapshots [--limit]` | 逐笔/盘口 |
| `flab data daily_basic` / `adj` / `limit` / `stock_basic` | 市值换手估值 / 复权因子·明细·事件 / 涨跌停 / 股票基础 |
| `flab data moneyflow` / `sector` / `members` / `fundamentals` | 个股资金 / 板块资金 / 概念成分 / 财务快照 |
| `flab data universe [--date] [--layer]` | PIT 池 |

**factor**
| 命令 | 作用 |
|---|---|
| `flab factor lint <spec...> [--all]` | 静态校验（秒级，不连库） |
| `flab factor run <spec.yaml> [全参数透传]` | 计算+评估+分层回测；返回摘要（IC/十分位/换手/覆盖/ic_decay/version/frequency） |
| `flab factor list` / `show <name>` / `export <name> [--format]` | 浏览 / 单因子摘要 / 产物导出 |
| `flab factor corr` / `resic` / `svd` | 相关性 / 增量信息 / 结构维度 |
| `flab factor ref list\|add\|remove` | 参考库管理 |
| `flab factor admit <spec.yaml>` | 一键入库检验：lint→(缺产物则 run)→对参考库 corr+resic→`verdict: 可加入\|观察\|冗余\|重复`；参考库准入要求测试段 `|resIC t|≥3`、`corr_max<0.7`、`retention≥0.5` |
| `flab factor op list\|doc\|add\|remove` / `catalog` | 算子/活文档 |

**strategy**
| 命令 | 作用 |
|---|---|
| `flab strategy lint <yaml>` / `run <yaml> [--signal --dry-run]` | 校验 / 组合构建→执行回测 |
| `flab strategy list` / `show <name>` / `export <name>` | 浏览 / 摘要（净值/成本/容量） / 导出 |
| `flab strategy capacity <name>` / `cost <name>` | E4 容量代理 / E3 成本后净值 |

**report**
| 命令 | 作用 |
|---|---|
| `flab report list` / `show <name>` / `url <name>` / `serve [--port --host]` | 报告浏览 / 静态 URL / 启动 Web |

**study（一条链）**
| 命令 | 作用 |
|---|---|
| `flab study run <factor.yaml> [--strategy <yaml>] [--against reference] [--skip-admit]` | 因子 run →（admit 冗余检验）→ 策略回测 → 报告 URL；返回全部产物路径 |
| `flab study list` | 历史 study 记录 |

## 4. JSON 契约

stdout **只输出一个 JSON**；日志/进度走 stderr 与 `artifacts.log`。

```json
{"ok": true, "schema_version": 1, "command": "factor.run",
 "data": {...}, "artifacts": {"run_dir": "...", "summary": "...", "log": "..."},
 "warnings": [], "error": null}
```

- 大数据集（>200 行）：默认落 parquet（`runs/research/<command>/<ts>/data.parquet`）返回 `path`+`head(5)`+schema，避免 token 爆炸；`--out` 覆盖、`--limit` 截断、`--inline` 强制内联。
- 错误：`{"ok": false, "error": {"code": "...", "message": "...", "hint": "...", "log": "..."}}`。

**错误码→退出码**（稳定枚举，describe 内置）：
| code | exit | 场景 |
|---|---|---|
| `USAGE` | 2 | 参数/用法错误 |
| `LINT` | 3 | spec/strategy 校验失败 |
| `MEMORY_GUARD` | 4 | 内存预检拒绝/看门狗中止 |
| `DEAD_SIGNAL` | 5 | 死信号 fail-loud |
| `RUN_FAILED` | 6 | 因子计算失败 |
| `STRATEGY_FAILED` | 7 | 策略/回测失败 |
| `DATA` | 8 | 数据读取/表缺失/后端不可用 |
| `NOT_FOUND` | 9 | 名称/产物不存在 |
| `BUSY` | 11 | heavy 闸满且未 `--wait` |
| `INTERNAL` | 10 | 未预期异常（含 traceback 路径） |

## 5. 默认值与安全

- 后端 `ch`（env 可覆盖）；结果目录固定 `runs/platform` / `runs/platform/strategies`。
- `factor run`/`strategy run`/`study run` 自动：heavy 闸（flock 2 槽，默认非阻塞→`BUSY`+hint；`--wait` 阻塞）、
  内存预检（avail<8GB → `MEMORY_GUARD`+hint）、env 注入（`FACTORLAB_MAX_MEMORY=8GB`、
  `FACTORLAB_MIN_AVAILABLE_MEMORY=6GB`、`OMP/POLARS_MAX_THREADS=8`、nice）。
- 全程不交互；幂等可重试；长任务同步执行并写 `run.log`。

## 6. 可发现性（agent 优先）

- `flab describe --json`：命令/参数/默认值/输出 schema/示例/错误码表；registry 单点。
- 一页手册 `knowledge/handbooks/research-agent-manual.md`（≤1 屏，含 10 条常用示例）。
- 更新 `.claude/skills/factorlab-{dsl,data,evaluate,backtest,ch-pipeline}/SKILL.md` 指向 flab；
  `AGENTS.md` 登记"研究员入口=flab"。
- 防漂移测试：手册中的 flab 命令必须存在于 registry（`test_doc_paths_exist` 扩展）。

## 7. 测试与验收

- 单测：registry/envelope/错误码映射；门面函数用真库（`env` fixture duckdb|ch 双腿）与 `MemoryRead` 双轨；
   **禁止行为断言**：data 命令必须真实调用 ReadPort（spy）、factor run 必须产生新产物（version/frequency 字段）、
   重命令必须经过闸（mock 闸记录调用）；存根必败突变检验。
- CLI 契约测试：CliRunner 断言 JSON schema/退出码/stdout 单 JSON/stderr 分离。
- 文档防漂移：`describe` 与手册/registry/CLI help 三处一致。
- E2E（真 CH）：`flab data daily` → `flab factor run`（小 universe）→ `flab factor admit` → `flab strategy run` → `flab report url`；
   证据 `governance/evidence/verification/R31/research-api/`。
- 平台全量 / `make test-research` 不回退；`make gates` 仅预存红可接受。

## 8. 交付顺序

1. 契约层：registry + envelope + `describe` + `flab` 安装脚本 + CLI 挂载骨架
2. data 组
3. factor 组
4. strategy 组
5. report + study 链
6. 手册/skills/文档同步 + E2E 证据 + 全量验收

## 9. 风险

| 风险 | 处置 |
|---|---|
| 门面变第二实现（业务漂移） | 门面只装配不实现；业务调用既有函数；契约测试锁死 |
| JSON 体积爆炸 | >200 行落 parquet + head；`--inline/--limit` 显式覆盖 |
| 重命令绕过闸 | 闸在门面入口强制执行；契约测试断言闸调用 |
| 命令面膨胀难维护 | registry 单点 + describe 自动生成；文档漂移测试 |
