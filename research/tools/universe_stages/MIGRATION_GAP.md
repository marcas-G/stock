# V4 local migration gap — CLOSED for formulas

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

## Remaining parity dependencies are data, not formulas

Before production parity can be claimed, local inputs must match jqdata semantics:

- point-in-time fundamentals and ST status as of factor_date;
- market cap normalization (local CNY vs jqdata 1e8 CNY);
- causal/pre-adjusted daily prices without future corporate-action leakage;
- CSI 500 (`000905`) adjusted close history for `rs_change`;
- a complete market trading calendar for 90/520 count windows.

Run historical parity against the saved V4 Top300 before declaring `V4.0-FINAL-local` production-ready.
