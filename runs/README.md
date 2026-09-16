# runs —— 运行产物（本地，不入库）

> R24（2026-09-16）产物单点化：`platform/results/` → `runs/platform/`；`settings.results_dir`
> 默认 = 仓库根 `runs/platform/`（从包位置派生、与 cwd 无关；`FACTORLAB_RESULTS_DIR` 可覆盖）。
> 根 `results/`（R24 迁移前遗留）已于 R06-MIG-I3（2026-09-16）清理完毕——唯一产物写入点
> 即本目录；根 `results/` 树不再存在（清理前清单/备份见
> `governance/evidence/verification/R24/16-r06-fixes/`）。

## 目录映射（run ↔ 档案）

| 产物目录 | 对应档案（唯一入口） |
|---|---|
| `runs/platform/<因子名>/`（`summary.json` / `signal.parquet` / `panel.parquet` / `weekly.parquet` / `labels.parquet`） | `knowledge/dossiers/factors/<族>/<因子短名>.md`（与 `research/factor/<族>/<短名>.yaml` 同族同短名；族见 `research/factor/_families.yaml`） |
| `runs/platform/<因子名>_<k><v>.../`（`--set` 参数变体） | 同族档案的“变体/迭代”节或独立档案；索引 `knowledge/index/factors.md` |
| `runs/platform/strategies/<策略名>/`（策略回测产物） | `knowledge/dossiers/strategies/<策略名>.md`（spec: `research/strategy/<策略名>.yaml`；索引 `knowledge/index/strategies.md`） |
| `runs/platform/_mine_rounds/_mine_round_<n>.md`（挖矿变异点临时记录） | 不入库；技能 `.claude/skills/factor-mine/`（R06-SKILL-I2 归位） |
| `runs/platform/<名>__root-dup-20260916/`（R06-MIG-I3 双份保留：根版本更新时） | 目录内 `_R06-MIG-I3.md` 说明来源与差异；不覆盖标准目录 |

## 常用命令

```bash
# 在任意 cwd 下，默认读写同一 runs/platform（无需 FACTORLAB_RESULTS_DIR）
platform/.venv/bin/factorlab list
platform/.venv/bin/factorlab show <因子名>
platform/.venv/bin/factorlab show <因子名> --json
```

- 产物文件布局契约：`knowledge/contracts/interface.md` §4（`results_fs` / `panel_store` 单点）。
- 新因子归档后重生成索引：`platform/.venv/bin/python research/tools/factor_lib/build_index.py`（G-INDEX `--check` 门；R06-M5 改平台 venv）。
