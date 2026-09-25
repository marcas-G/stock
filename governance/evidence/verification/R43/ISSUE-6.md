# Issue #6 处理与证据记录

复核日期：2026-09-25。关联评审项：R08-DATA-I2。

## 处理范围

本记录只确认 **退市股 `adj_factor` 自 2022-01-01 起的补灌实现及其既有验收证据**。该工作通过腾讯后复权行情生成 sidecar；与原始收盘价逐日对拍，存在 vendor 校准漂移时拒绝填洞；入库只以 sidecar 补充空值，不覆盖已有 vendor 值。

实现提交：`2b39807`（`fix(data): 退市股 adj_factor 补灌（R08-DATA-I2）`）。

## 复核结果

- 定向测试：`platform/.venv/bin/python -m pytest platform/tools/ch_ingest/tests/test_delisted_adj_backfill.py -q`，**19 passed**（2026-09-25 复跑）。
- R30 执行记录显示 sidecar 共 **83,967 行、180 码**；目标 185 码中有 5 码因 vendor 与 hfq/raw 漂移超出 0.5% 阈值而拒绝补尾段：`000004.SZ`、`002808.SZ`、`600608.SH`、`600636.SH`、`688287.SH`。拒绝细节见 [R35 sidecar 与工具抽查](../R35/R08-DATA-I2/sidecar-and-tool.txt)。
- [R30 对账输出](../R30/eval-v2-task14-12-11/27-reconcile-all-v2.log)记录 sidecar 对应 CH 行无缺失/空值、无重复键且全库 reconcile 一致；[R35 CH 抽样](../R35/R08-DATA-I2/ch-delisted-adj-sample.txt)记录 5 个抽样代码非空，并核对了 `000005.SZ` 的 2022-01-04 值与 sidecar 一致。
- [R30 变更前后查询](../R30/eval-v2-task14-12-11/17-ch-adj-after.txt)记录评估窗（>=2023）仍有 **157 行 `adj_factor` NULL，涉及 10 码**。这是当次抽查口径，不能据此宣称所有退市股或所有历史空值均已修复。

## 未覆盖范围与尾项

- 仅补灌 2022 年起的数据；R30 记录说明 2022 年前未恢复，因为腾讯与通达信历史数据存在差异。
- 上述 5 个代码因漂移检查被拒绝填尾段，相关数据仍可能为空。
- 退市股 `float_shares`、`total_shares`、`amount` 源数据缺失，因而依赖这些字段的 turnover 类因子仍可能为 null；这不属于本次 `adj_factor` 修复范围。

因此，证据支持“2022+ 退市股 `adj_factor` 补灌链已实现，并对成功写入的数据完成对账”，不支持“全历史、全代码缺失均已清零”。GitHub issue 是否关闭仍须按原 issue 的验收范围决定；若要求所有代码和全历史无缺失，现有证据不足以关单。

## 复现入口

- 定向测试：`platform/.venv/bin/python -m pytest platform/tools/ch_ingest/tests/test_delisted_adj_backfill.py -q`
- 实现与契约说明：[R30 Task 12 记录](../R30/eval-v2-task14-12-11/README.md)
- 生产对账：[R30 reconcile 输出](../R30/eval-v2-task14-12-11/27-reconcile-all-v2.log)
- 当前实现文件：`platform/tools/ch_ingest/delisted_adj_backfill.py`、`platform/tools/ch_ingest/ingest_daily.py`、`platform/tools/ch_ingest/reconcile.py`
