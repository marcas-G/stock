# 因子档案目录（knowledge/dossiers/factors）

与 `research/factor/<族>/<短名>.yaml` **同族同短名镜像**的因子档案（每因子一份 `.md`：
front matter + 六节模板，规范见 `_template.md`）。机器索引在 `knowledge/index/factors.md`
（`research/tools/factor_lib/build_index.py` 生成，`--check` 常驻门）。

> **R24 路径映射（2026-09-16）**：本目录 `research/docs/factors/` → `knowledge/dossiers/factors/`；
> 索引 `docs/index/factors.md` → `knowledge/index/factors.md`；playbook `research/docs/factor-mining-playbook.md`
> → `knowledge/dossiers/factor-mining-playbook.md`（R06-M10 单点归位：`knowledge/handbooks/factor-mining-playbook.md`）。
> 各档案正文尾部模板行残留的
> `docs/factor-mining-playbook.md` 为**历史档案正文**（不改写），按本映射理解。

## ⚠️ 验证数字是历史快照（R21 标注，2026-09-15）

**全部 152 份档案 §4「验证结果」里的数字（IC / t / IR / 分层 / 换手）来自当时本地跑出的
`platform/results/<name>/summary.json`，该产物从未随仓存档，且当前环境不可复跑。**
R01-EVID-C1（台账 `governance/evidence/reviews/findings.md`）的修复选择**如实标注为历史快照**，不伪造产物。

不可复跑的事实（复现命令与原始输出：`governance/evidence/verification/R21/EVID/C1-not-reproducible.txt`）：

| 事实 | 实测 |
|---|---|
| `platform/results/` 为空（`results/` 被 gitignore，历史运行产物已清） | `find platform/results -mindepth 1` → 0 项 |
| 平台 DuckDB 库不存在（duckdb 后端的默认库，被 gitignore） | `find . -name '*.duckdb'` → 无 |
| CH 后端缺 `stock_st`（ST 快照表） | `SHOW TABLES FROM factorlab` 无 `stock_st`；`SELECT count() FROM factorlab.stock_st` → `UNKNOWN_TABLE` |
| **152/152** spec 的 universe 规则都含 `exclude_st: true` | `grep -l exclude_st research/factor/*/*.yaml \| wc -l` → 152 |

→ 任一档案的"全市场"口径数字都缺输入数据：duckdb 腿无库、CH 腿无 `stock_st`。

## 恢复条件（完成复跑后更新档案并删除快照标注）

1. ~~**重建平台库**（`platform/.venv/bin/factorlab data rebuild`）~~ **已退役**
   （2026-09-17 Plan P T11：teajoin 源与命令删除）——恢复路径 = 第 2 条
   （CH 灌入 `stock_st`）或外部 ST 源到位（`governance/workspace/pending-items.md` #26）；
   复跑后按原流程用新的 `runs/platform/<name>/summary.json` 更新档案 §4、`updated_ts`，
   并删除 `snapshot` 字段；
2. **或**给 CH 灌入 `stock_st`（`platform/tools/ch_ingest/`），以
   `FACTORLAB_DATA_BACKEND=ch` 复跑同一条链；
3. 只做**小样本冒烟**（`universe.codes` / `--universe`）可在 CH 上跑通
   （例：`governance/evidence/verification/R12/r12_smoke.yaml`），但**不构成**对全市场档案数字的复现。

## 标注约定

- 每份档案 front matter 的 `snapshot:` 字段 = 该档案的验证数字为历史快照；
- 数字重新在可复现环境跑出并留证（命令 + 输出存档）后，**删除该字段**并刷新 `updated_ts`；
- `snapshot` 只存在于档案 front matter，不在 YAML spec 里、也不参与
  `knowledge/index/factors.md` 生成（索引只读 `research/factor/**/*.yaml`），因此不影响索引
  byte-equality 门；批量标注脚本存于
  `governance/evidence/verification/R21/EVID/annotate_factor_archives.py`（幂等，`--check` 可验）。
