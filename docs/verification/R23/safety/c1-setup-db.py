"""R05-C1 证据：极小平台库（与 test_run_factor.build_db 同构，n_days=9）。"""
import pathlib
import sys

sys.path.insert(0, "/data/students/gaolei/stock/platform/tests")
sys.path.insert(1, "/data/students/gaolei/stock/platform/src")

from test_run_factor import build_db

build_db(pathlib.Path("/tmp/opencode/r05c1/demo"), n_days=9)
print("seeded: /tmp/opencode/r05c1/demo/q.duckdb")
