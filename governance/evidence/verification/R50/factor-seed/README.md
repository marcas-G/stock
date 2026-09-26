# R50 factor-seed verification

This change adds one Prefect entrypoint that reruns one existing Factor Spec on a declared IS window, validates the FactorLab output, and publishes a versioned `factor_signal` ArtifactRef for XScore. It does not run a real factor calculation, access the lockbox, or change the live Prefect runner.

| Check | Result | Evidence |
|---|---|---|
| Research flows and XScore tests | 189 passed; 1 existing Python 3.13 multiprocessing `fork()` deprecation warning | `commands.txt`, `research-tests.log` |
| Platform documentation path tests | 11 passed | `doc-path-tests.log` |
| Installer shell syntax, flow compile, diff whitespace, G-TOPO | PASS; G-TOPO reports 0 violations | `verify.sh`, `static-checks.log` |
| Prefect deployment listing JSON option | Supported by installed CLI | `prefect-cli-help.txt` |

The installed Prefect service was only queried read-only during verification. It still lists the previously registered deployments; the new `factor-seed/factor-seed` deployment is not live until the updated code is installed and the runner registers it.
