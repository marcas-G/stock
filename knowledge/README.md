# knowledge —— 文档与知识的唯一入口

> R24 顶层目录重整（方案 A）：历史文档按用途归入 `knowledge/`（契约、设计、手册）
> 和 `governance/`（工作区规范、证据、评审）。
> 迁移台账：`../governance/workspace/migration-r04.md`。

## 我要找 X → 去哪

| 我想找… | 去哪 |
|---|---|
| 平台契约（interface / catalog / data-ops-playbook / teajoin-guide） | `contracts/` |
| 平台设计/计划（原 `platform/docs/superpowers/`） | `design/platform/{specs,plans}/` |
| 研究设计/计划（原 `research/docs/superpowers/`） | `design/research/{specs,plans}/` |
| 工作区级设计（分钟执行、算子开放、策略分解等） | `design/workspace/` |
| 因子档案（与 `$QUANTRESEARCH_ROOT/factor/<族>/<stem>.yaml` 一一对应） | **产物区** `$QUANTRESEARCH_ROOT/dossiers/factors/<族>/<stem>.md` |
| 策略档案（与 `$QUANTRESEARCH_ROOT/strategy/<stem>.yaml` 一一对应） | **产物区** `$QUANTRESEARCH_ROOT/dossiers/strategies/<stem>.md` |
| 挖因子方法论 / 单页上手手册 | `handbooks/factor-mining-playbook.md`、`handbooks/factor-authoring-manual.md` |
| 长文手册（NASA SE × V-Model） | `handbooks/` |
| 因子/策略机器索引（自动生成，勿手改） | **产物区** `$QUANTRESEARCH_ROOT/index/{factors,strategies}.md` |

> R37 Phase 2：档案/索引已随研究产物迁出主仓（`QUANTRESEARCH_ROOT`，缺省
> `/data/students/gaolei/quantresearch`）；本仓 `dossiers/`、`index/` 不再存在。
> 目录公约见 `../governance/workspace/directory-conventions.md` §7。

## 生成物纪律

- `$QUANTRESEARCH_ROOT/index/factors.md`：`research/tools/factor_lib/build_index.py`（`--check` 逐字节门，常驻 `make index-check`/`make gates`）。
- `$QUANTRESEARCH_ROOT/index/strategies.md`：`research/tools/factor_lib/build_strategy_index.py`（同上）。
- 契约 `contracts/*` 各只一份（G-COPY 门；消费方引用此目录为唯一权威）。
