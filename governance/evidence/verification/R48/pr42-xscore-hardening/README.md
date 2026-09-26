# R48 — PR #42 xscore final recovery hardening

This round closes the remaining final-retry and import-isolation issues:

- interrupted final retries remove only their version's known shared auxiliary cache files;
- panel identity uses file contents, and earlier stat-based ledger rows are replayed by their stable config and content signature;
- recovery resumes only the same pending attempt;
- published lockbox replays require an exact ledger/manifest panel-signature match and select the newest access ID after a re-final;
- the shared lockbox helper lives under `research/tools/lib/` under a unique module name, so it passes G-TOPO without shadowing the platform's `lib` package.

Run `bash governance/evidence/verification/R48/pr42-xscore-hardening/verify.sh` from the stock repository root to reproduce the relevant checks. `verify.log` contains the command trace and raw output for the current code commit.

The isolated worktree used the platform venv and editable platform-tools path from the primary checkout; this avoids reconfiguring FactorLab or copying the primary checkout's data into the worktree. `make test-research` was not used as an acceptance gate because its existing data-root checks require those local datasets. No production final run or lockbox backtest was started.
