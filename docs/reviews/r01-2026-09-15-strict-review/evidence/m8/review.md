# Strict Adversarial Review — M7 Portfolio Construction + M8 Execution/Backtest

Repo: /data/students/gaolei/stock @ dd01bd9 (clean). Read-only review; probes under
/tmp/opencode/reviewer-m8/. Baseline: `tests/test_backtest_runtime.py tests/test_backtest_ca_gate.py
tests/test_backtest_marks_policy.py` → 43 passed.

## Strengths

1. **NAV identity is sound, verified by instrumented trace with nonzero costs.** Probe2
   (commission 3bps/min 5 + stamp 10bps + transfer 0.2bps on two rebalances) shows
   `pre_nav - post_nav == accounting.total_fees` at every event (float noise ~1e-10),
   `post.cash == pre.cash + Σ effective_cash_delta`, `nav == cash + Σ qty×mark`,
   and `save/load` exact round-trip. No fee double-count, no open/close mark mixup.
2. **Timing is strictly NEXT_OPEN, no lookahead path.** `resolve_execution_schedule`
   uses `bisect_right` (strictly > decision) and rejects non-open decision dates
   (calendar.py:53-57); `run_backtest` re-dates state forward only (backtest.py:199-206).
3. **T+1 is provenance-aware.** `apply_fill_batch` BUY leaves sellable unchanged;
   `advance_to_next_trading_day` releases only same-day BUY `filled_quantity` and
   enforces unsellable capacity (overnight.py:95-115); sparse-liquidation semantics
   correct (state.py:99-102).
4. **Fillability boundaries are exact and conservative**, including `nextafter` tests:
   BUY open==up blocked, SELL open==dn blocked, favorable side fillable, missing
   daily/limit evidence → `ExecutionDataQualityError` fail-closed (fillability.py:80-116).
5. **Quantity rules are reference-data driven** (`stock_basic.market` + suffix, no
   prefix heuristics; impossible combos fail) — rules.py:24-30, 243-269.
6. **CA Gate window (prev_exec, exec] is logically complete** for held positions,
   including events on exec day, across multi-day gaps, in suspension freeze (B12),
   and buys on the event day are correctly exempt (B6).
7. **Persistence unit files are individually atomic** (atomicio: temp+fsync+os.replace),
   unknown versions and missing files fail closed; empty BacktestResult round-trips.
8. **Tests kill stubs**: with `project_sell_quantity`/`project_buy_quantity` replaced by
   trivially-wrong stubs, 9 tests fail (test_backtest_orders.py + test_backtest_fills.py),
   including `test_star_no_oversell_target`, `test_cost_driven_buy_reduction`,
   `test_partial_buy_fees_based_on_filled`. Not shape-only.

## Issues

### Critical (Must Fix)

None found in the accounting/timing core.

### Important (Should Fix)

**I1. Stale manifest + failed overwrite silently loads a hybrid artifact (M7 and M8).**
- Where: `strategy_artifacts.write_strategy_artifacts` (target → schedule → manifest,
  lines 197-246) / `execution_store.save_backtest_result` (10 parquet writes → manifest,
  lines 156-217); loaders `load_strategy_artifacts:418-470`, `load_backtest_result:225-331`.
- What: saving into an existing complete artifact directory never invalidates/removes the
  previous manifest before overwriting data files. A failure mid-save (disk full, crash)
  leaves **new data files + old manifest + stale files**, and load accepts the mixture.
- Evidence (probes, synthetic only):
  - M7 probe3: bundle A (strategy_x, k=2, 4 rows) written; overwrite with bundle B
    (strategy_y, k=1) fails at schedule write. `load_strategy_artifacts` returns
    `spec.name='strategy_x'`, provenance `alpha_x`, but the **k=1 strategy_y frame**
    (2 rows, weight 1.0) — silent.
  - M8 probe4: run A nav `[1000000, 1075000]`; overwrite with run B nav
    `[1000000, 1100000]` fails at `nav/nav_series.parquet`. `load_backtest_result`
    returns artifacts whose `nav.nav == 1100000` while `nav_series.nav == 1075000`.
    `BacktestResult.__post_init__` (domain/backtest.py:156-170) checks only row count and
    date alignment, never artifact↔nav or artifact↔final_state values.
- Why it matters: interface.md §M7-04 claims “manifest 最后写（缺失 = incomplete
  directory，loader 识别）… 无 manifest 即不可加载” (lines 1803-1805); a *stale* manifest
  defeats this and defeats M8-06C fail-closed claims. Consumers get wrong NAV/target with
  no error.
- Fix: (a) stage a new directory and atomic-rename/swap, or at minimum unlink/rename the
  manifest before writing any core file (so a crash leaves no loadable manifest);
  (b) hardening on load: verify `nav_series` row i equals `artifacts[i].nav`
  (cash/mv/nav), `final_state` equals last post-state + overnight expectations, and
  treat extra per-event rows (`row(0)` currently ignores them) as errors. M7 loader
  should also cross-check the schedule against a per-run nonce persisted inside both
  target and manifest (or remove stale files before rewrite).

### Minor (Nice to Have)

**I2. `FillBatch.order_quantity` is always == `filled_quantity`, so partial fills are not
representable in the fill row.** fills.py:275 appends `(code, "buy", q, q, ...)` where `q`
is already the funding-scaled quantity; SELL rows likewise. The 12-column contract has a
dedicated `order_quantity`, and M8-04C docs describe “BUY partial fills”/fees based on
`filled_quantity`; as built, no row can say “ordered 1000, filled 800” (they both say 800).
Impact is auditability only: `artifacts/orders.parquet` still preserves the original order,
and cash/fees are correct. Fix: pass the original OrderBatch quantity as `order_quantity`
and the funded/scaled quantity as `filled_quantity` (FillBatch validator already allows
`filled < order`).

**I3. `load_adj_event_window` returns a Null-typed empty frame for a zero-row window when
the table exists.** market_open.py:374 builds `pl.DataFrame(events, schema=[...])` from an
empty list → `Schema({'code': Null, 'trade_date': Null})` (probe1), contradicting the
documented `code String / trade_date Date` output and the analogous missing-table branch
that *is* typed. Currently harmless because the CA gate only reads `.height`, but any
future date/string consumer breaks. Fix: return the typed-empty constructor used at
market_open.py:366-368 (or cast after construction).

**I4. `decision_range` cannot isolate an early segment when a later decision is trailing.**
`run_backtest` filters `all_dates` first but resolves the schedule for the **full target**
(backtest.py:173-181). `resolve_execution_schedule` fails whole on any decision with no
next open day (calendar.py:54-57), so a range ending before an unresolved trailing decision
still fails. Spec calls decision_range a decision-level filter (`target.decision_dates ∩
range`); resolve only the in-range decisions (or document the restriction).

**I5. Trailing-unresolved termination drops intermediate results despite the design note.**
m8-06a §6.3 says the last execution with no next open day is “合法终止，不 drop 中间结果”,
but the unconditional `advance_to_next_trading_day` at backtest.py:316 propagates
`ValueError` (overnight.py:85-88), so no `BacktestResult` is returned. interface.md
M8-06B says “全链 fail fast”, so this may be intended — but the two docs disagree; pin the
semantics (e.g. return result with `final_state = POST` plus an explicit
`trailing_unresolved` flag, or delete the “不 drop” sentence).

**I6. Load-time manifest strictness gaps (M8).** `load_backtest_result` ignores
`manifest.columns`, `created_at`, `runtime_version`, and the date range; `artifact_count`
comparison accepts a JSON `true` as `1` (bool is int). M7's loader is stricter
(`_strict_int` rejects bool). Fold a `_strict_int`-style check and a `columns ==
manifest.columns` check into I1's fix.

**I7. Future hazard: persisted artifacts do not carry `execution_timing`.**
`load_backtest_result` hardcodes `ExecutionTiming.NEXT_OPEN` (execution_store.py:288,
294, 298). Unreachable today (all NEXT_CLOSE paths raise — see below), but if NEXT_CLOSE
becomes writable this loader would silently relabel it. Persist the timing (or assert
NEXT_OPEN on save).

## Verification notes (requested checks)

- **NEXT_CLOSE**: 7 explicit `NotImplementedError` sites confirmed: state.py:61,
  accounting.py:59, fillability.py:61, orders.py:121, overnight.py:72, fills.py:116, plus
  backtest.py:168 (`MarksPolicy` guard). `calendar.py` passes the timing through by
  design, but every downstream primitive raises before fills/accounting — no silent
  NEXT_OPEN fallback found (except the loader hazard I7).
- **Quantity rules**: mapping uses `stock_basic.market` + suffix only; missing/duplicate
  reference, empty market, unknown/impossible combination all fail (rules.py:238-269).
- **Suspension**: circular interval parser and open-09:30 semantics match the frozen
  grammar (suspension.py:79-114); missing-row inference is in `run_backtest`; R+timing,
  distinct multi-events, and unparseable timing fail fast (market_open.py:150-189).
- **Tests sampled**: test_backtest_runtime, test_backtest_marks_policy, test_backtest_ca_gate,
  test_execution_signal_chain, test_rebalance_schedule, test_open_fillability — all assert
  computed values against independent seed-side arithmetic, not shapes; mutation probe
  above confirms kill-power.

## Verdict

Overall: **fix-first** (accounting/marks/T+1/CA core is sound; integrity gates are not as
fail-closed as documented).

Single most important thing to fix: **I1 — invalidate (or stage around) the manifest
before rewriting artifact files, and cross-check `nav_series`/`final_state` against
artifacts on load.** Until then a crash or disk-full during re-save can silently serve a
mixture of two runs as a valid result.
