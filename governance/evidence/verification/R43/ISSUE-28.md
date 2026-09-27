# Issue #28 盘查记录：无涨跌幅制度日与 `stk_limit`

盘查日期：2026-09-25。结论：**实现阻塞，当前不能无歧义地修复生产端识别规则**。

## 问题与历史证据

R37 原始记录在 [`exec-issues/README.md`](../R37/exec-issues/README.md) 的
R37-EXEC-I2 段。只读 ClickHouse 探针与产物如下：

- [`probe_stk_limit_gap.py`](../R37/exec-issues/probe_stk_limit_gap.py)
- [`stk-limit-gap-output.txt`](../R37/exec-issues/stk-limit-gap-output.txt)
- [`outside-band-rows.csv`](../R37/exec-issues/outside-band-rows.csv)

探针覆盖 2021-08-01 至 2026-07-31，发现 140 条 open 超出已派生 `[down_limit, up_limit]`
带宽的行，涉及 138 只证券（占样本 0.0008%）。样例 `000502.SZ` 于 2022-06-06
的 `pre_close=3.80`、`open=0.45`、`close=0.52`，派生界限为 `[3.42, 4.18]`；
股票基本信息记录退市日为 2022-06-25。R37 记录将该日识别为退市整理期首日、合法无涨跌幅日。
以上数量与案例是当时探针快照，不代表本次重新扫描后的全量数据结论。

## 当前实现契约

执行消费端的语义已明确：

- [`fillability.py`](../../../../platform/src/factorlab/core/execution/fillability.py) 规定，
  `has_limit=False` 时订单按 raw open 标记为 `FILLABLE`；有界限证据时，open 越界则抛
  `ExecutionDataQualityError`。
- [`market_open.py`](../../../../platform/src/factorlab/adapters/read/market_open.py) 用当日
  `stk_limit` 行是否存在生成 `has_limit`，并以全市场覆盖率门拦截疑似整日/大面积漏派生；
  单证券缺行仍是合法无限制语义。
- [`ch_ingest/README.md`](../../../../platform/tools/ch_ingest/README.md) 明确记载，
  `stk_limit` 仅覆盖有涨跌停的日子，缺行表示合法无限制；既有测试也锁定此行为。

## 根因与契约缺口

生产端 [`derive_stk_limit.py`](../../../../platform/tools/ch_ingest/derive_stk_limit.py)
目前可以按日期、`pre_close`、证券代码段和上市行序识别历史制度起始日、上市首日及部分注册制新股
前五个交易日。但它没有逐证券逐交易日的权威“是否实行涨跌幅限制”输入，不能据此识别
长期停牌后的复牌日或退市整理期起始日。

生产脚本的已知近似说明明确记载：复牌/退市整理首日仍基于 stale `pre_close` 派生带宽；
停牌原因/名称来源不可辨，并将此近似列为 v1 接受项。R37 问题记录则要求对此类日子输出
`has_limit=0` 或宽口径，并核对清单。两者没有约定权威日期来源、复牌日的判定阈值、
退市整理期的起止判定，也没有裁决应以官方限价数据替换派生值，还是由研究股票池排除此类日期。
股票基本信息中的 `delist_date` 只能给出退市日期，不能单独确定退市整理期首日；仅按价格越界或
停牌间隔反推会把真实市场异常与制度豁免混为一类。

因此，消费端契约本身明确，但**生产端识别哪些日期应省略 `stk_limit` 行的规则不完整且与既有
v1 近似政策冲突**。在缺少逐日制度状态源或范围裁决前，不添加基于价格/间隔的推断，也不改派生 SQL。

## 当前行为定向验证

经 `governance/ops/heavy.sh` 执行：

```text
governance/ops/heavy.sh platform/.venv/bin/python -m pytest -q \
  platform/tests/test_open_fillability.py::test_non_suspended_missing_limit_accepted_as_no_limit \
  platform/tests/test_open_fillability.py::test_no_limit_buy_sell_both_fillable_at_open \
  platform/tests/test_market_open_snapshot.py::test_single_code_missing_limit_represented \
  platform/tests/test_market_open_snapshot.py::test_stk_limit_coverage_gap_fails_loudly \
  platform/tests/test_market_open_snapshot.py::test_stk_limit_coverage_new_listing_exempt
```

结果：`8 passed in 2.64s`。这确认现有执行层的“无界限行可成交”与市场级漏派生保护，
**没有验证生产端能识别 R37 的复牌/退市整理日期**。本次未重跑长窗口 CH 探针或 deep verify；
既有 R43 deep verify 不包含对上述 140 行逐行制度状态的独立裁定，不能视为 #28 已闭环证据。

## 解除阻塞所需决定

继续实现前，需要确定以下一种可验收路径：

1. 提供逐证券逐交易日的权威限价/是否有限价数据，并规定与当前派生值的优先级、缺失处理和校验规则；
   或
2. 明确受支持的无涨跌幅事件类型和日期区间规则，指定可追溯的数据字段/来源，并裁决无法识别的事件
   是否继续按 v1 近似、从股票池排除，或让回测 fail-fast。

口径确定后再为具体规则先写真实数据/合成边界回归，修复派生与 reconcile，并用 R37 越界清单及真实 CH
长窗口回测验证合法行情不再触发 M8 越界门、而普通有涨跌幅日的数据错误仍 fail-fast。
