# 评估指标 v2 终验收（Task 10）

日期：2026-09-17 ｜ 计划：`knowledge/design/platform/plans/2026-09-16-factorlab-eval-metrics-v2.md`

## 提交链（14 commits）

| 阶段 | commits |
|---|---|
| 口径批（Task 0/5，D1=B + version） | `e530732` `4df9506` |
| 核心（Task 13/15，daily + 单一实现） | `f453c5f` `63014bc` `6d72c1f` |
| 修订（Task 1/2/3/4） | `1cf4a92` `7cc4984` `0ee8454` `8805284` `2e63bf9` |
| 参考库/退市/重跑（Task 14/12/11） | `04bbfe0` `2b39807` `fc51796` |
| 增强（Task 9/8/6/7） | `4d86a9f` `8084376` `c1f510d` `42e2245` |

## 验收数字

- 平台全量：**3187 passed / 11 skipped / 0 failed**（766.72s，`14-task10-full-suite.txt`；基线 3089/11 → 新测试 +98）
- `make test-research`：platform/tools 549 passed / 0 failed；research/tools 2 failed = 挖矿在途（`factor_lib/test_index.py`，非本次范围）
- `make gates`：唯一红 = **G-INDEX**（挖矿在途 spec 未入索引，与各批基线一致）；**G-DATAIFACE 全绿**（`delisted_adj_backfill`/`ingest_daily`/`reconcile` 4 个 D8 直读点补登记，`governance/ops/check_dataiface.py`）
- `ch_ingest/reconcile.py all` → exit 0（批 4）
- 逐值/手算：spread 翻转对照、D2 7 面板对拍、D3 真实数据 t=4.489→2.527≈NW 2.673、daily vs weekly 差异表、R22 六代表 weekly 零变更、E2-E4 手算，全部绿（各批证据）
- 口径文档一致：interface 含 version/frequency/dead_signal/direction_consistent_share/sampling/ic_decay/weighting/cost/capacity/forward_return_1d；playbook §4.1 spread 行与 interface 一致（防漂移断言测试）

## 范围符合性（因子侧纯净）

- E3/E4 落策略层模块，不写因子评估 summary；E2/E1 落因子侧统计（E1a `total_mv` 口径；circ_mv=E1b 待 R07-DATA-I4）
- `frequency=daily` 为默认；`weekly` 保留可选对照且零变更

## 遗留（给协调者/挖矿收尾后）

1. G-INDEX 与 research/tools 索引 2 红：挖矿收尾后 `make index` 收口
2. 历史 v1 口径注记：74 个 v1 运行 → 46 份档案待注 `spread 为 v1 口径（负=自洽）`（其中 6 份挖矿在途未动），清单见 `eval-v2-task0-5/09-v1-annotation-pending.md`
3. `max_effect_20d_high` 等 17 个挖矿在途产物待挖矿批次产出 v2 产物
4. 参考库 2 只（`rsi_reversal_14`/`amihud_illiq_turn_20d`）初判冗余，按入库流程待替换
5. 退市股 turnover 类因子仍缺股本/成交额（超出 adj 补口范围）；5 码 vendor 漂移拒补、87 日 hfq≤0 剔除（loud，不伪造）
6. E1b（circ_mv 加权）待 R07-DATA-I4 完成后启用
