# Issue #27 — NEXT_OPEN 滑点越过涨跌停价

复核日期：2026-09-25。

## 问题与处理决定

R37 的执行面记录保存了一个五年日线策略复现：`slippage_bps=5` 时，
`600900.SH` 的滑点价为 `21.480735`，略高于 `up_limit=21.48`，旧实现因此
抛出 `ValueError` 并中断回测。原始摘录和复现配置见
[`R37/exec-issues/README.md`](../R37/exec-issues/README.md)。

处理规则限定在 `NEXT_OPEN` 成交：

- BUY 滑点价越过上限时，最终成交价封顶到 `up_limit`；SELL 越过下限时，
  最终成交价封底到 `down_limit`。
- 价带内及恰好等于边界时保持原价。
- 封顶后再次通过 `compute_execution_cost` 计算 gross、各项费用和
  `effective_cash_delta`。BUY 的 affordability probe、迭代预算计算及最终
  FillBatch 使用同一规则。
- 仅调整成交价，不改变 `OpenFillAssessment` 的市场阻断状态。
- `NEXT_WINDOW` 的分钟模拟和日级限价一致性检查维持原规则。

## 实现与验证

先添加 BUY/SELL 越界封顶、费用/现金及 BUY 资金边界回归，再在实现前按
`governance/ops/heavy.sh` 运行，两个用例均按预期失败：旧实现分别在 BUY
`10.05 > 10.04`、SELL `9.95 < 9.96` 时抛出越界异常。原始输出：
[`ISSUE-27-red.log`](ISSUE-27-red.log)。

RED 命令：

```text
governance/ops/heavy.sh platform/.venv/bin/python -m pytest platform/tests/test_backtest_fills.py -k 'buy_slippage_crosses_upper_is_capped or sell_slippage_crosses_lower_is_capped' -q
```

实现后验证结果：

| 命令 | 结果 | 原始输出 |
|---|---:|---|
| `governance/ops/heavy.sh platform/.venv/bin/python -m pytest platform/tests/test_backtest_fills.py -k 'buy_slippage_crosses_upper_is_capped or sell_slippage_crosses_lower_is_capped' -q` | 2 passed | [`ISSUE-27-focused-green.log`](ISSUE-27-focused-green.log) |
| `governance/ops/heavy.sh platform/.venv/bin/python -m pytest platform/tests/test_backtest_fills.py platform/tests/test_execution_costs.py platform/tests/test_execution_accounting.py platform/tests/test_backtest_runtime.py platform/tests/test_window_fills.py -q` | 246 passed | [`ISSUE-27-related-green.log`](ISSUE-27-related-green.log) |
| `governance/ops/heavy.sh platform/.venv/bin/python -m pytest platform/tests/test_doc_paths_exist.py -q` | 11 passed | [`ISSUE-27-doc-paths.log`](ISSUE-27-doc-paths.log) |

BUY 回归还将同一 FillBatch 传入 state transition、execution accounting 和
valuation：最终现金为 `0.5`，账务现金变化为 `-1009`，按原始开盘价估值的
NAV 下降 `9`（封顶价差 `4` 加佣金 `5`）。SELL 回归核对封底后的 gross、
佣金、印花税、过户费和净回款。

## 验收边界

以上验证覆盖本地 NEXT_OPEN 执行、账务/NAV 传递和相关单元测试。按本轮约束
没有重跑原始五年 CH 策略，因此尚无证据证明 issue 复现策略在真实 CH 数据上
完成全窗运行；该 live acceptance 仍待后续串行验证。本记录不代表 GitHub
Issue 状态已更改；本 MR 仅关闭本地行为缺陷，五年 CH live acceptance 仍需单独补证。

2026-09-25 的主机检查仍不满足重任务运行条件：`llama-server` 常驻进程 RSS
约 53.2 GiB，且另有 pytest integration 进程在运行。虽然当时 MemAvailable
约 62.4 GB、SwapFree 约 36.8 GB，仓库 R30 纪律禁止与 LLM 服务并发重任务，
故本轮没有启动五年 CH 回测。空闲后应经 heavy 闸重跑原复现命令：

```text
governance/ops/heavy.sh flab strategy run /data/students/gaolei/quantresearch/experiments/r37_5y/low_lottery_5y.yaml
```

验收时需保留完整命令输出、退出码和结果产物路径。
