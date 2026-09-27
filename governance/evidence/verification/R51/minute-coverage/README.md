# R51: Prefect minute-coverage failure

The failed FactorLab run for `intraday_close_auction_premium` checked the
`bars1m_2023_2025` pool on 2024-07-18. Comparing daily rows with minute rows
confirms seven missing `(code, date)` pairs:

`300265.SZ`, `300305.SZ`, `300533.SZ`, `300551.SZ`, `300552.SZ`,
`300576.SZ`, `300578.SZ`.

None of the seven has a member in the local 20240718 source ZIP, so the local
archive cannot backfill them. The repository change writes every missing pair
across all request chunks to `minute_coverage_audit.json` before the run fails.
It leaves fail-fast semantics intact and does not publish the signal.

The existing minute Spec uses a static sample-coverage pool. Replacing it with a
full-window pool requires an explicit common-universe decision for all
forward-selection comparisons because static full-window coverage introduces
survivorship bias. No pool or Spec was changed as part of this incident fix.

Checks are recorded in `commands.txt`; exact ClickHouse output and source ZIP
membership results are in `observed-output.txt`.
