# runs —— 运行产物（本地，不入库）

> R24（2026-09-16）产物单点化：`platform/results/` → `runs/platform/`；`settings.results_dir`
> 默认 = 仓库根 `runs/platform/`（从包位置派生、与 cwd 无关；`FACTORLAB_RESULTS_DIR` 可覆盖）。
> 旧根 `results/` 为挖矿在途产物（过渡期仍在写，停机后清理），不在本单点内。

## 目录映射（run ↔ 档案）

| 产物目录 | 对应档案（唯一入口） |
|---|---|
| `runs/platform/<因子名>/`（`summary.json` / `signal.parquet` / `panel.parquet` / `weekly.parquet` / `labels.parquet`） | `knowledge/dossiers/factors/<族>/<因子短名>.md`（与 `research/factor/<族>/<短名>.yaml` 同族同短名；族见 `research/factor/_families.yaml`） |
| `runs/platform/<因子名>_<k><v>.../`（`--set` 参数变体） | 同族档案的“变体/迭代”节或独立档案；索引 `knowledge/index/factors.md` |
| `runs/platform/strategies/<策略名>/`（策略回测产物） | `knowledge/dossiers/strategies/<策略名>.md`（spec: `research/strategy/<策略名>.yaml`；索引 `knowledge/index/strategies.md`） |

## 常用命令

```bash
# 在任意 cwd 下，默认读写同一 runs/platform（无需 FACTORLAB_RESULTS_DIR）
platform/.venv/bin/factorlab list
platform/.venv/bin/factorlab show <因子名>
platform/.venv/bin/factorlab show <因子名> --json
```

- 产物文件布局契约：`knowledge/contracts/interface.md` §4（`results_fs` / `panel_store` 单点）。
- 新因子归档后重生成索引：`python3 research/tools/factor_lib/build_index.py`（G-INDEX `--check` 门）。
