#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"
make_test_rc=0

{
  echo '$ governance/ops/heavy.sh platform/.venv/bin/python -m pytest research/tools/research_flows/tests -q'
  governance/ops/heavy.sh platform/.venv/bin/python -m pytest \
    research/tools/research_flows/tests -q
} > governance/evidence/verification/R44/flows-tests.log 2>&1

{
  echo '$ governance/ops/heavy.sh platform/.venv/bin/python -m pytest research/tools/xscore/tests/test_ref_sync.py research/tools/xscore/tests/test_manifest.py -q'
  governance/ops/heavy.sh platform/.venv/bin/python -m pytest \
    research/tools/xscore/tests/test_ref_sync.py \
    research/tools/xscore/tests/test_manifest.py -q
} > governance/evidence/verification/R44/ref-sync-tests.log 2>&1

{
  echo '$ governance/ops/heavy.sh make test-research'
  governance/ops/heavy.sh make test-research || make_test_rc=$?
} > governance/evidence/verification/R44/make-test-research.log 2>&1

{
  echo '$ governance/ops/heavy.sh platform/.venv/bin/python -m pytest governance/ops -q'
  governance/ops/heavy.sh platform/.venv/bin/python -m pytest governance/ops -q
} > governance/evidence/verification/R44/governance-tests.log 2>&1

{
  echo '$ governance/ops/heavy.sh platform/.venv/bin/python -m pytest platform/tests/test_doc_paths_exist.py -q'
  governance/ops/heavy.sh platform/.venv/bin/python -m pytest \
    platform/tests/test_doc_paths_exist.py -q
} > governance/evidence/verification/R44/doc-paths-tests.log 2>&1

{
  echo '$ PYTHONPATH=research/tools research/.venv/bin/python -c ... create_deployments()'
  PYTHONPATH=research/tools research/.venv/bin/python -c \
    'from research_flows.deployments import create_deployments; print([d.name for d in create_deployments()])'
  echo '$ platform/.venv/bin/python -m py_compile research/tools/research_flows/*.py research/tools/research_flows/tests/*.py research/tools/xscore/pipeline/data_prep.py'
  platform/.venv/bin/python -m py_compile research/tools/research_flows/*.py \
    research/tools/research_flows/tests/*.py \
    research/tools/xscore/pipeline/data_prep.py
  echo '$ bash -n governance/ops/install_prefect_runner.sh'
  bash -n governance/ops/install_prefect_runner.sh
  echo '$ git diff --check'
  git diff --check
} > governance/evidence/verification/R44/runtime-checks.log 2>&1

exit "$make_test_rc"
