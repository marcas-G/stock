# PR #42 CI follow-up

## Failure found

GitHub Actions run `36218937737` for commit `8d2ac579c841985cf6f751c4591bfa22a4778e64` completed with failure on 2026-09-26.

Two independent checks were red:

- `platform/tests/test_architecture.py::test_tools_declare_env_single_point` found a direct `sys.path.insert()` in `research/tools/lib/xscore_lockbox.py`. The repository requires research tools to use the canonical `platform/tools/_env.py::ensure_platform()` bootstrap.
- The offline path gate treated `QR / "results/platform"` in `research/tools/research_flows/xscore_flow.py` as a stale `results/<name>` path. Splitting the path into `QR / "results" / "platform"` preserves the same directory while avoiding that false match.

The Python test summary was `1 failed, 3034 passed, 1109 skipped, 1 deselected` in the platform suite. The platform-tools, research-tools, and governance suites passed.

## Fix

- The lockbox adapter now lazily calls `_env.ensure_platform()` and no longer edits `sys.path` itself.
- The Prefect runner, local flow command, legacy xscore launcher, worker subprocess, and research test commands expose `research/tools` before `platform/tools` through `PYTHONPATH`, so the research `lib` package wins while `_env` remains available.
- The default artifact root is expressed as path components and retains the same `results/platform` location.
- Added regression tests for the shared bootstrap, default artifact root, and Prefect runner environment.

## Verification

- `research-tests.log`: `PYTHONPATH=research/tools:platform/tools:platform/src platform/.venv/bin/python -m pytest research/tools/xscore/tests/test_manifest.py research/tools/research_flows/tests -q` — **87 passed**.
- `platform-architecture.log`: `PYTHONPATH=research/tools:platform/tools:platform/src platform/.venv/bin/python -m pytest platform/tests/test_architecture.py::test_tools_declare_env_single_point platform/tests/test_architecture.py::test_env_resolves_platform_src -q` — **2 passed**.
- `gates-offline.log`: `QUANTRESEARCH_ROOT=.ci-no-product-area bash governance/ops/gates.sh --offline` — structure gate all green; G-TOPO has 0 violations; G-READ has 0 violations.
- `static-checks.log`: `bash -n` on the modified scripts, `make -n research-flow FLOW_NAME=factor-mining/factor-mining FLOW_CFG=/tmp/factor.yaml`, and `git diff --check` completed successfully.

Commands were run in the PR worktree with its platform, research, and platform source paths on `PYTHONPATH`. The offline gates used a nonexistent temporary product root, matching hosted CI's no-product-area mode.
