# 评估指标 v2 终验收（Task 10）

日期：2026-09-17 ｜ 计划：`knowledge/design/platform/plans/2026-09-16-factorlab-eval-metrics-v2.md`

## 提交链（17 commits；e530732..42e2245，验收文档为 5852d36）

| 阶段 | commits |
|---|---|
| 口径批（Task 0/5，D1=B + version） | `e530732` `4df9506` |
| 核心（Task 13/15，daily + 单一实现） | `f453c5f` `63014bc` `6d72c1f` |
| 修订（Task 1/2/3/4） | `1cf4a92` `7cc4984` `0ee8454` `8805284` `2e63bf9` |
| 参考库/退市/重跑（Task 14/12/11） | `04bbfe0` `2b39807` `fc51796` |
| 增强（Task 9/8/6/7） | `4d86a9f` `8084376` `c1f510d` `42e2245` |

> 注（R30 fix 波校正）：原写「14 commits」与表不符（表内即 17 个）。
> `8084376` 标题「成本后净值进 summary（E3）」**与实现相反**——实际交付为策略层
> 纯函数 `app/strategy/cost_net.py`（`cost_net_report`），**不进因子评估 summary**
> （D11 因子侧纯净）。标题保留历史（不复写公开提交），此处注记以正视听。

## 验收数字

- 平台全量：**3187 passed / 11 skipped / 0 failed**（766.72s，`14-task10-full-suite.txt`；基线 3089/11 → 新测试 +98）
- `make test-research`：platform/tools 549 passed / 0 failed；research/tools 2 failed = 挖矿在途（`factor_lib/test_index.py`，非本次范围）
- `make gates`：唯一红 = **G-INDEX**（挖矿在途 spec 未入索引，与各批基线一致）；**G-DATAIFACE 全绿**（`delisted_adj_backfill`/`ingest_daily`/`reconcile` 4 个 D8 直读点补登记，`governance/ops/check_dataiface.py`）
- `ch_ingest/reconcile.py all` → exit 0（批 4）
- 逐值/手算：spread 翻转对照、D2 7 面板对拍、D3 真实数据 t=4.489→2.527≈NW 2.673、daily vs weekly 差异表、R22 六代表 weekly 零变更、E2-E4 手算，全部绿（各批证据）
- 口径文档一致：interface 含 version/frequency/dead_signal/direction_consistent_share/sampling/ic_decay/weighting/cost/capacity/forward_return_1d；playbook §4.1 spread 行与 interface 一致（防漂移断言测试）

## 范围符合性（因子侧纯净）

- E3/E4 落策略层模块，不写因子评估 summary（**当前无调用方**，等 M8/策略报告接线）；
  E2/E1 落因子侧统计。**R30 fix 波补口**：E1 产品入口 `FactorSpec.weighting`
  （默认 equal_weight 零回归）→ `evaluate_run` → kernel 全链接通；`market_cap`
  用 `total_mv`（E1a）按需进评估面板（signal artifact 单列不变）；E1b（`circ_mv`）
  kernel/bridge `mv_col=` 已可用（R07-DATA-I4 于 2026-09-16 完成），spec 入口未暴露
  `mv_col` 字段（后续按需加）。
- `frequency=daily` 为默认；`weekly` 保留可选对照且零变更

## 遗留（给协调者/挖矿收尾后）

1. G-INDEX 与 research/tools 索引 2 红：挖矿收尾后 `make index` 收口
2. **档案注记清单已重生成（R30 fix 波，2026-09-17）**：实际 **17 个 v1 运行 /
   5 份关联档案 / 12 个未匹配**（旧口径「74 runs / 46 档案」随 D7 重跑删除失效）；
   另 **41 个 v2+daily 运行 → 39 份关联档案数值/符号仍写于 v1 时代，待按 v2 产物
   刷新**（如 `low_vol_20d`；2 个无档案 = 参考库种子 top2/top5）。区分清单见
   `governance/evidence/verification/R30/eval-v2-fix/11-pending-annotations-v2.txt`
   （脚本 `pending_annotations_v2.py`，只读）——a) 5 份含挖矿在途禁动；
   b) 刷新属研究树动作，由协调者/挖矿收尾执行
3. `max_effect_20d_high` 等 17 个挖矿在途产物待挖矿批次产出 v2 产物
4. 参考库 2 只（`rsi_reversal_14`/`amihud_illiq_turn_20d`）初判冗余，按入库流程待替换
5. 退市股 turnover 类因子仍缺股本/成交额（超出 adj 补口范围）；5 码 vendor 漂移拒补、87 日 hfq≤0 剔除（loud，不伪造）
6. E1b（circ_mv 加权）：数据与 kernel 参数均已就绪（R07-DATA-I4，2026-09-16 完成）；
   待产品决策是否在 spec 暴露 `mv_col` 字段
