# 合成分索引（自动生成，勿手改）

> 生成器：`research/tools/factor_lib/build_composite_index.py`（`--check` 门：产物必须逐字节一致）。
> 共 **6 个合成分**；成员列顺序 = spec 声明顺序（design §3）。

## 注册合成分（spec ↔ 档案成对）

| 合成分 | 成员（列顺序） | 方法 | 参数 | 窗口 | 状态 | 档案 | 规格 |
|---|---|---|---|---|---|---|---|
| `cx_demo` | `cx_demo_x1` → `cx_demo_x2` | `research.composites.implementations.cx_demo:compute` | — | 2026-09-01 ~ 2026-09-16 | draft | [cx_demo.md](../../knowledge/dossiers/composites/cx_demo.md) | [cx_demo.yaml](../../research/composites/specs/cx_demo.yaml) |
| `linear_rank` | `cx_demo_x1` → `cx_demo_x2` | `research.composites.implementations.linear_rank:rank_average` | — | 2026-09-01 ~ 2026-09-16 | draft | [linear_rank.md](../../knowledge/dossiers/composites/linear_rank.md) | [linear_rank.yaml](../../research/composites/specs/linear_rank.yaml) |
| `linear_weighted` | `cx_demo_x1` → `cx_demo_x2` | `research.composites.implementations.linear_rank:linear` | weights=[0.7, 0.3] | 2026-09-01 ~ 2026-09-16 | draft | [linear_weighted.md](../../knowledge/dossiers/composites/linear_weighted.md) | [linear_weighted.yaml](../../research/composites/specs/linear_weighted.yaml) |
| `pca` | `cx_demo_x1` → `cx_demo_x2` | `research.composites.implementations.ridge_pls_pca:pca` | — | 2026-09-01 ~ 2026-09-16 | draft | [pca.md](../../knowledge/dossiers/composites/pca.md) | [pca.yaml](../../research/composites/specs/pca.yaml) |
| `pls` | `cx_demo_x1` → `cx_demo_x2` | `research.composites.implementations.ridge_pls_pca:pls` | n_components=1 | 2026-09-01 ~ 2026-09-16 | draft | [pls.md](../../knowledge/dossiers/composites/pls.md) | [pls.yaml](../../research/composites/specs/pls.yaml) |
| `ridge` | `cx_demo_x1` → `cx_demo_x2` | `research.composites.implementations.ridge_pls_pca:ridge` | alpha=1.0 | 2026-09-01 ~ 2026-09-16 | draft | [ridge.md](../../knowledge/dossiers/composites/ridge.md) | [ridge.yaml](../../research/composites/specs/ridge.yaml) |
