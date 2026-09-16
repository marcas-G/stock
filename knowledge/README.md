# knowledge —— 文档与知识的唯一入口

> R24 顶层目录重整（方案 A）：所有**文档/知识**从三处（根 `docs/`、`platform/docs/`、
> `research/docs/`）收敛到这里。迁移台账：`../governance/workspace/migration-r04.md`。

## 我要找 X → 去哪

| 我想找… | 去哪 |
|---|---|
| 平台契约（interface / catalog / data-ops-playbook / teajoin-guide） | `contracts/` |
| 平台设计/计划（原 `platform/docs/superpowers/`） | `design/platform/{specs,plans}/` |
| 研究设计/计划（原 `research/docs/superpowers/`） | `design/research/{specs,plans}/` |
| 工作区级设计（分钟执行、算子开放、策略分解等） | `design/workspace/` |
| 因子档案（与 `research/factor/<族>/<stem>.yaml` 一一对应） | `dossiers/factors/<族>/<stem>.md` |
| 策略档案（与 `research/strategy/<stem>.yaml` 一一对应） | `dossiers/strategies/<stem>.md` |
| 挖因子方法论 / 单页上手手册 | `dossiers/factor-mining-playbook.md`、`dossiers/factor-authoring-manual.md` |
| 长文手册（NASA SE × V-Model） | `handbooks/` |
| 因子/策略机器索引（自动生成，勿手改） | `index/factors.md`、`index/strategies.md` |

## 生成物纪律

- `index/factors.md`：`research/tools/factor_lib/build_index.py`（`--check` 逐字节门，常驻 `make gates`）。
- `index/strategies.md`：`research/tools/factor_lib/build_strategy_index.py`（同上）。
- 契约 `contracts/*` 各只一份（G-COPY 门；消费方引用此目录为唯一权威）。
