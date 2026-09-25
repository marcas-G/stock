# Issue #37 处理记录

## 问题

Issue [#37](https://github.com/marcas-G/stock/issues/37) 报告了
2026-09-24 03:00（Asia/Shanghai）nightly deep verify 失败。原始日志：

`/data/students/gaolei/quantresearch/results/platform/.nightly/20260924-030000.log`

失败步骤为 `pytest-platform` 和 `pytest-governance-ops`。

## 根因

这次失败发生在 R42“最终测试只跑一次”锁箱语义迁移尚未完成的中间状态，不是随机测试波动，也不是同一个缺陷在两个测试套件中的重复表现。

| 失败类别 | 原始表现 | 根因 |
|---|---|---|
| 入库测试 3 条 | `test_lockbox_admit.py` 断言登记行数为 1，实际为 2 | `factor.admit` 的最终测试门正在从旧的“补登记”语义切换到“最终测试车道执行 + 冻结件”语义；nightly 运行时测试仍是旧断言 |
| health 测试 2 条 | `roll(..., quota_final=7)` 抛出 `TypeError` | R42 删除 `quota_final` 参数和配额/探索状态字段，测试尚未同步 |
| governance ops 31 条 | fixture 调用 `register_access(kind="exploration")` 抛出 `ValueError`，随后大量用例无法建立样本台账 | R42 已规定新登记只允许 `final`；历史 `exploration` 行必须用只读历史数据模拟，fixture 尚未同步 |

时间线也支持“迁移中的测试/fixture 不一致”判断：nightly 在
2026-09-24 03:00 运行；随后 R42 的相关修复提交在 03:38–05:34 依次落地。

## 处理

R42 后续提交完成了实现、测试和治理 fixture 的同口径迁移：

- `3970ff8`：最终测试一次语义重写，移除新 exploration 登记和配额语义；
- `b67f13e`：入库只读取测试段冻结结果，并同步重写 admit 测试；
- `2d1a2f8`：同步 health/governance 检查器、测试和历史 exploration fixture；
- `3b1fa8e`：入库车道补齐挖矿标准环境默认值；
- `972b3bd`、`a48ff57`：完成 CI 环境无关化和终审收口。

## 复验

定向复验命令（经 `governance/ops/heavy.sh`）：

```text
platform/.venv/bin/python -m pytest -q \
  platform/tests/test_lockbox_admit.py \
  platform/tests/test_research_health_lockbox.py \
  governance/ops/tests/test_check_lockbox.py
```

结果：`77 passed in 4.08s`。

随后 R43 deep verify 全链通过：

- platform：`4128 passed, 17 skipped`；
- platform tools：`920 passed, 1 deselected`；
- research tools：`184 passed, 3 deselected`；
- governance ops：`219 passed`；
- `gates-full`、product lint、索引检查均为 `rc=0`。

完整日志：
`governance/evidence/verification/R43/tester/verify-deep-2026-09-25.log`。

## 结论

Issue #37 的失败已由 R42 后续提交处理并经定向测试和 R43 全链复验确认通过。当前 GitHub issue 仍为 open；回填本记录和验证日志后即可由维护者关闭，关闭动作不在本次本地变更中执行。
