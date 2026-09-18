# R32 T9：读取拒绝矩阵（真实 require_dataset）

| # | 用例 | Accepted | 说明 |
|---|---|---|---|
| 1 | PASS/fresh/COMPLETE → 过 | ✅ | 通过（gate + manifest 五字段） |
| 2 | PASS/stale/COMPLETE → 拒（freshness） | ❌ | 读取门拒绝：dataset=ashare_daily partition=2026-09-17 status=PASS——数据陈旧：freshness.latest_trade_date=2026-08-18 距 as_of 为 30d > max_staleness=1d；指引：刷新数据到 as_ |
| 3 | PASS/fresh/INCOMPLETE → 拒（completeness） | ❌ | 读取门拒绝：dataset=ashare_daily partition=2026-09-17 status=PASS——completeness.status=INCOMPLETE ≠ COMPLETE（独立检查，不靠 coverage 推）；指引：补齐/重导缺失分区后重发 health；完整性未 |
| 4 | PASS/fresh/UNKNOWN → 拒（completeness） | ❌ | 读取门拒绝：dataset=ashare_daily partition=2026-09-17 status=PASS——completeness.status=UNKNOWN ≠ COMPLETE（独立检查，不靠 coverage 推）；指引：补齐/重导缺失分区后重发 health；完整性未证实不 |
| 5 | DEGRADED/默认 → 拒 | ❌ | 读取门拒绝：dataset=ashare_daily partition=2026-09-17 status=DEGRADED——不在 accept_quality=('PASS',)（默认 fail-closed）；指引：DEGRADED 需显式 opt-in（accept_quality 含 D |
| 6 | DEGRADED/fresh/COMPLETE/opt-in → 过 + manifest | ✅ | 通过（gate + manifest 五字段） |
| 7 | DEGRADED/stale/opt-in → 拒（freshness） | ❌ | 读取门拒绝：dataset=ashare_daily partition=2026-09-17 status=DEGRADED——数据陈旧：freshness.latest_trade_date=2026-08-18 距 as_of 为 30d > max_staleness=1d；指引：刷新数据到 |
| 8 | DEGRADED/fresh/INCOMPLETE/opt-in → 拒（completeness） | ❌ | 读取门拒绝：dataset=ashare_daily partition=2026-09-17 status=DEGRADED——completeness.status=INCOMPLETE ≠ COMPLETE（独立检查，不靠 coverage 推）；指引：补齐/重导缺失分区后重发 health； |
| 9 | FAIL/默认 → 拒 | ❌ | 读取门拒绝：dataset=ashare_daily partition=2026-09-17 status=FAIL——FINAL 门判 FAIL（不可 opt-in）；指引：修复数据并重跑 clean → ingest → health；不得绕过 |
| 10 | FAIL/opt-in → 拒（不可 opt-in） | ❌ | ValueError: FAIL 不可 opt-in（设计 §7：FAIL ❌ 不可 opt-in）——先修复数据重发 health |
| 11 | UNKNOWN(LEGACY)/默认 → 拒 | ❌ | 读取门拒绝：dataset=ashare_daily partition=2026-09-17 status=UNKNOWN——不在 accept_quality=('PASS',)（默认 fail-closed）；指引：DEGRADED 需显式 opt-in（accept_quality 含 DE |
| 12 | UNKNOWN(LEGACY)/fresh/COMPLETE/opt-in → 过 + manifest | ✅ | 通过（gate + manifest 五字段） |
| 13 | UNKNOWN(LEGACY)/stale/opt-in → 拒（freshness） | ❌ | 读取门拒绝：dataset=ashare_daily partition=2026-09-17 status=UNKNOWN——数据陈旧：freshness.latest_trade_date=2026-08-18 距 as_of 为 30d > max_staleness=1d；指引：刷新数据到  |
| 14 | UNKNOWN(LEGACY)/fresh/INCOMPLETE/opt-in → 拒（completeness） | ❌ | 读取门拒绝：dataset=ashare_daily partition=2026-09-17 status=UNKNOWN——completeness.status=INCOMPLETE ≠ COMPLETE（独立检查，不靠 coverage 推）；指引：补齐/重导缺失分区后重发 health；完 |
| 15 | UNKNOWN(VERIFIED) → 拒（非 LEGACY） | ❌ | 读取门拒绝：dataset=ashare_daily partition=2026-09-17 status=UNKNOWN——UNKNOWN 仅限 LEGACY 存量（verification_state='VERIFIED'）；指引：重发 health 或按 §8 过渡条款处理 |
| 16 | strict + DEGRADED opt-in → ValueError | ❌ | ValueError: strict（正式 OOS/验收/基准场景）只接受 PASS：accept_quality=('PASS', 'DEGRADED') 非法 |
