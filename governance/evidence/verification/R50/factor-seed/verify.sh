#!/usr/bin/env bash
set -euo pipefail
bash -n governance/ops/install_prefect_runner.sh
echo 'bash -n governance/ops/install_prefect_runner.sh: PASS'
platform/.venv/bin/python -m compileall -q research/tools/research_flows
echo 'compileall research/tools/research_flows: PASS'
git diff --check
echo 'git diff --check: PASS'
bash governance/ops/gates.sh --topo
echo 'gates.sh --topo: PASS'
