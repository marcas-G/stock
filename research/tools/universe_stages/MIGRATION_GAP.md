# V4 local migration status — formulas CLOSED, end-to-end still blocked on missing sources

## Formulas: recovered and ported

The original frozen jqdata V4 source has now been recovered and archived at:

`references/v4_jqdata_final_original.py`

The previously missing formulas are now ported directly into `layer1/v4.py`:

- drawdown
- stress
- down_decay
- low_break_eff
- turnover_health
- rs_change
- efficiency
- clv20

The local implementation also reproduces two easy-to-miss source semantics:

1. activity is clipped to `[0, 5]` and requires at least 100 positive-money observations in the last 250 active rows;
2. Fast 90 and Detail 520 are selected by *market trading-date windows before suspension rows are removed*, not by per-stock active-row tail counts.

## Executable status (R01-TOOLS-I9, 2026-09-15)

This file previously read "CLOSED" without saying what is actually runnable locally. Formula parity
and end-to-end runnability are different claims; the latter is:

| stage | entry | status | blocker |
|---|---|---|---|
| layer1 single snapshot | `scripts/run_layer1.py --scan-date` | **not runnable end-to-end** | fundamentals PIT source missing (pending #4) |
| layer1 historical batch | `scripts/run_layer1.py --from-golden` | **not runnable end-to-end** | same + golden generation chain not retained (pending #9) |
| layer2 SAS | `scripts/run_layer2_sas.py` | sources present (not exercised end-to-end this round) | — |
| layer3 tick | `scripts/run_layer3_tick.py` | **not runnable end-to-end** | `data/raw/20260817.7z` not unpacked (pending #3); layer2 outputs absent |

Every script now runs `universe_paths.preflight_layer{1,2,3}()` first: a missing source raises
`MissingInput` naming the **exact path and how to obtain it** (e.g. `import_fundamentals.py
--fin-parquet`, unpacking `20260817.7z`), instead of leaking a bare `FileNotFoundError` from deep
inside a reader. A synthetic small-sample layer1 CLI test
(`tests/test_preflight.py::test_layer1_cli_end_to_end_with_synthetic_sources`) proves the code chain
runs when inputs exist; it is not real-data parity evidence.

## Remaining parity dependencies are data, not formulas

Before production parity can be claimed, local inputs must match jqdata semantics:

- point-in-time fundamentals and ST status as of factor_date;
- market cap normalization (local CNY vs jqdata 1e8 CNY);
- causal/pre-adjusted daily prices without future corporate-action leakage;
- CSI 500 (`000905`) adjusted close history for `rs_change`;
- a complete market trading calendar for 90/520 count windows.

Run historical parity against the saved V4 Top300 before declaring `V4.0-FINAL-local` production-ready.
