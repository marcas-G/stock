# R19 证据目录说明（R21 补录，2026-09-15）

R19 的证据由「一次性迁移脚本（未随仓存档）+ 真跑命令」生成。R21 审计发现两处证据纪律
缺口（R01-EVID-I8），按 **不改写历史正文** 的约定在本文件补记：

## 1. 勘误：`02-migration-diff.txt` 第 15–23 行的 traceback 是 superseded 的失败过程

- 该段 `ModuleNotFoundError: No module named 'config'` 来自迁移**中间态**的一次失败调用：
  当时 `import_daily.py` 仍 `import config`（工具内配置/路径单点），随后按 G-TOPO 门的
  报红把 `config.py` 改名 `datapaths.py`（见 `status.md` §1/§5），并以正确加载方式复跑成功。
- 因此该 traceback **不代表最终结果**，仅是一次已被取代（superseded）的失败现场；
  最终结论以同文件第 24–26 行（新旧摘要对照 + `OUT_SCHEMA` 列序）与 `status.md` §4 为准。
- 正文不改写（保留历史原貌）；本条为事后说明。

## 2. 生成脚本 R21 补存：`gen-02-migration-diff.py`

原生成脚本（stdin 片段）未随仓存档，且**旧源 `projects/ashare_alpha3/scripts/0X_*.py`
迁移后已删除**（仅剩 `__pycache__/*.pyc`）——原文件 `02-migration-diff.txt` 的
`旧=` 数值与 -x/+y diff 统计**不可再生成**（不伪造）。补存脚本为**最小可复现**版本，
覆盖仍可复现的部分：

- 新侧 `import_daily._parse_one` 生产者摘要：600519（全量 zip）与 600591（退市 xlsx）；
- `OUT_SCHEMA` 列序自检。

其固定序列化为
`sha256(json.dumps(Table.from_pandas(df).replace_schema_metadata(None).to_pydict(), sort_keys=True, default=str, ensure_ascii=False))[:16]`
——原脚本的序列化未记录、无法逐字节重放；脚本输出存
`02-migration-diff-r21-repro.txt`，与 R19 记录值比较时注意：① 序列化方法可能不同；
② R21 TOOLS-C2 已改退市分支语义（in-file `code` 为准 + sidecar），600591 现值可能变化。

运行（只读 `data/raw/daily/`，`data/` 零改动）：

```bash
platform/.venv/bin/python docs/verification/R19/gen-02-migration-diff.py
```
