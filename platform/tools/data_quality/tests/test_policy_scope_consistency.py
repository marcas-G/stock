"""R37（2026-09-20 用户裁定）：daily-v3 ``scope:`` 块与 ``factorlab.core.scope``
常量一致性锁定——口径单一事实源，不许漂移。

规格：knowledge/design/platform/specs/2026-09-20-dq-scope-cut-addendum.md §1。
"""
from pathlib import Path

import pytest

from data_quality import rules
from factorlab.core import scope as core_scope

# 最小合法 policy（含 v3 scope 块；其余块取 spec §3.2 冻结值）
MINIMAL = """\
dq_policy_version: daily-v3
partition_gate:
  pass:
    max_error_rate: 0.0001
    min_coverage: 0.999
  fail:
    min_error_rate: 0.01
    max_missing_coverage: 0.01
systemic:
  field_share: 0.80
  field_count: 50
  group_ratio: 10.0
  group_count: 50
  time_share: 0.80
  time_count: 100
vwap:
  default_tol: 0.01
  pre_1995_tol: 0.02
  pre_1995_cutoff: "1995-01-01"
field_invalidity:
  null_on_nonpositive: ["adj_factor", "fq_factor"]
historic_unit_exceptions: []
scope:
  min_trade_date: "1996-01-01"
  exclude_code_suffixes: [".BJ"]
"""


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "policy.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_shipped_policy_scope_matches_core_constants():
    rules.load_policy()                     # 不一致即 ValueError
    import yaml
    raw = yaml.safe_load(rules.DEFAULT_POLICY_PATH.read_text(encoding="utf-8"))
    assert raw["scope"]["min_trade_date"] == core_scope.MIN_TRADE_DATE_ISO
    assert raw["scope"]["exclude_code_suffixes"] == list(
        core_scope.EXCLUDED_CODE_SUFFIXES)


def test_scope_min_date_mismatch_rejected(tmp_path):
    body = MINIMAL.replace('min_trade_date: "1996-01-01"',
                           'min_trade_date: "1995-01-01"')
    with pytest.raises(ValueError) as ei:
        rules.load_policy(_write(tmp_path, body))
    assert "scope.min_trade_date" in str(ei.value)


def test_scope_suffixes_mismatch_rejected(tmp_path):
    body = MINIMAL.replace('exclude_code_suffixes: [".BJ"]',
                           'exclude_code_suffixes: [".SH"]')
    with pytest.raises(ValueError) as ei:
        rules.load_policy(_write(tmp_path, body))
    assert "scope.exclude_code_suffixes" in str(ei.value)


def test_missing_scope_rejected(tmp_path):
    body = "\n".join(line for line in MINIMAL.splitlines()
                     if not line.startswith(("scope:", "  min_trade_date",
                                             "  exclude_code_suffixes")))
    with pytest.raises(ValueError) as ei:
        rules.load_policy(_write(tmp_path, body))
    assert "scope" in str(ei.value)
