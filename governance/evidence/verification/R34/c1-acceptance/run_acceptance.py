#!/usr/bin/env python
"""R34 / Plan CX-C1 T5：spec §17 七条验收逐条取证（可重跑）。

命令（仓库根）：
    POLARS_MAX_THREADS=1 platform/.venv/bin/python \
        governance/evidence/verification/R34/c1-acceptance/run_acceptance.py

成员 artifact 在临时 sandbox 内合成（真实 `write_factor_artifacts`，labels 为平台
schema v2），经生产链 `factorlab.app.composite.runner.run_composite`（与
`factorlab compose` 同一入口）跑通；七条断言任一失败 → 非零退出。

验收项（design §17）：
  ① 手算一致  ② member 顺序变→definition hash 变  ③ artifact 变→cache 失效重算
  ④ 缺 member→FAIL  ⑤ coverage 不一致→intersection 正确（审计计数）  ⑥ output NaN→FAIL
  ⑦ 成员名不传入 compute()
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import textwrap
from pathlib import Path

import polars as pl
import yaml

from factorlab.adapters.parquet_artifacts import write_factor_artifacts
from factorlab.app.composite.resolver import MemberResolutionError
from factorlab.app.composite.runner import run_composite
from factorlab.core.composite import definition_hash, load_composite_spec
from factorlab.core.domain.frames import LabelArtifact, SignalArtifact, SignalMeta

REPO = Path(__file__).resolve().parents[5]
SAMPLE_SPEC = REPO / "research" / "composites" / "specs" / "cx_demo.yaml"
SAMPLE_IMPL = REPO / "research" / "composites" / "implementations" / "cx_demo.py"

DATES = [dt.date(2024, 1, 2), dt.date(2024, 1, 3), dt.date(2024, 1, 4)]
CODES = ["000001.SZ", "000002.SZ", "000003.SZ"]
X2_SKIP = {(DATES[1], CODES[0])}          # x2 少一行 → 覆盖不一致（验收⑤）

_NAN_BODY = """
import numpy as np


def compute(X, params):
    return np.full(X.shape[0], np.nan)
"""

_SPY_BODY = """
import json
from pathlib import Path

import numpy as np

_LOG = Path(__file__).with_name("calls.jsonl")


def compute(X, params, **kwargs):
    with _LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"shape": list(X.shape), "params": params,
                             "kwargs": sorted(kwargs), "x_repr": repr(X)}) + "\\n")
    return 0.5 * X[:, 0] - 0.5 * X[:, 1]
"""


def _member_values() -> tuple[dict, dict]:
    x1: dict = {}
    x2: dict = {}
    for di, d in enumerate(DATES):
        for ci, c in enumerate(CODES):
            v = 10 * di + ci
            x1[(d, c)] = float(v)
            if (d, c) not in X2_SKIP:
                x2[(d, c)] = float(5 + 2 * v)
    return x1, x2


def _write_member(runs: Path, name: str, values: dict) -> Path:
    keys = sorted(values)
    dates = [k[0] for k in keys]
    codes = [k[1] for k in keys]
    sig = pl.DataFrame({"date": dates, "code": codes,
                        "signal": [values[k] for k in keys]})
    fwd1 = [0.01 * (CODES.index(c) + 1) + 0.001 * DATES.index(d) for d, c in keys]
    labels = pl.DataFrame({"date": dates, "code": codes,
                           "forward_return_1d": fwd1,
                           "forward_return_5d": [v * 2 for v in fwd1],
                           "forward_return_20d": [v * 3 for v in fwd1]})
    panel = sig.join(labels, on=["date", "code"], how="left")
    out = Path(runs) / name
    write_factor_artifacts(out, SignalArtifact(frame=sig, meta=SignalMeta(name=name)),
                           LabelArtifact(frame=labels), panel, {"name": name})
    return out


def _write_spec(sandbox: Path, name: str, members: list[str], entrypoint: str, *,
                params: dict | None = None) -> Path:
    d = sandbox / "research" / "composites" / "specs"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.yaml"
    path.write_text(yaml.safe_dump({
        "name": name,
        "members": members,
        "implementation": {"entrypoint": entrypoint},
        "params": params or {},
        "alignment": {"join": "intersection", "missing_policy": "reject"},
        "output": {"name": "signal"},
    }, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def _write_impl(sandbox: Path, name: str, body: str) -> str:
    d = sandbox / "research" / "composites" / "implementations"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.py").write_text(textwrap.dedent(body), encoding="utf-8")
    return f"research.composites.implementations.{name}:compute"


def _keys(frame: pl.DataFrame) -> list[tuple]:
    return list(zip(frame["date"].to_list(), frame["code"].to_list()))


def main() -> int:
    assert (REPO / "platform").is_dir() and (REPO / "research").is_dir(), f"bad REPO={REPO}"
    sandbox = Path(tempfile.mkdtemp(prefix="cx-c1-acceptance-"))
    runs = sandbox / "runs"
    x1, x2 = _member_values()
    _write_member(runs, "cx_demo_x1", x1)
    _write_member(runs, "cx_demo_x2", x2)
    sandbox_impl = sandbox / "research" / "composites" / "implementations" / "cx_demo.py"
    sandbox_impl.parent.mkdir(parents=True, exist_ok=True)
    sandbox_impl.write_text(SAMPLE_IMPL.read_text(encoding="utf-8"), encoding="utf-8")
    entry = "research.composites.implementations.cx_demo:compute"

    print(f"# repo        = {REPO}")
    print(f"# sandbox     = {sandbox}")
    print(f"# sample spec = {SAMPLE_SPEC.relative_to(REPO)}")
    print("# backend     = 生产链 run_composite（compose CLI 同入口）")

    run_a = run_composite(SAMPLE_SPEC, results_dir=runs)
    print(f"# run A: out={run_a.out_dir} rows={run_a.frame.height} "
          f"cached={run_a.cached} cache_key={run_a.summary['cache_key'][:16]}...")

    # ---------------- ① 手算一致 ----------------
    print("\n[1] 手算一致：y = 0.5*x1 - 0.5*x2（逐值）")
    keys = _keys(run_a.frame)
    actual = run_a.frame["signal"].to_list()
    expected = [0.5 * x1[k] - 0.5 * x2[k] for k in keys]
    print("    date        code        x1     x2   expected    actual   match")
    for k, e, a in zip(keys, expected, actual):
        print(f"    {k[0]}  {k[1]}  {x1[k]:5.1f} {x2[k]:6.1f}  {e:8.2f}  {a:8.2f}   "
              f"{'OK' if e == a else 'MISMATCH'}")
    assert actual == expected, "手算不一致"
    print(f"[1] PASS 手算一致（{len(keys)} 行逐值）")

    # ---------------- ⑤ coverage 交集 ----------------
    print("\n[5] coverage 不一致 → intersection 正确（审计计数）")
    audit = run_a.alignment
    print(f"    intersection_rows={audit.intersection_rows}")
    for m in audit.members:
        print(f"    member={m.member} rows={m.rows} valid={m.valid_rows} kept={m.kept_rows} "
              f"dropped_missing_value={m.dropped_missing_value} "
              f"dropped_uncovered={m.dropped_uncovered}")
    by_member = {m.member: m for m in audit.members}
    assert audit.intersection_rows == len(x1) - len(X2_SKIP) == 8
    assert by_member["cx_demo_x1"].rows == 9
    assert by_member["cx_demo_x1"].dropped_uncovered == 1
    assert by_member["cx_demo_x2"].rows == 8
    assert by_member["cx_demo_x2"].dropped_uncovered == 0
    assert (DATES[1], CODES[0]) not in set(keys)
    print("[5] PASS 交集=8 行，x1 掉 1 行（覆盖不足）、x2 掉 0 行")

    # ---------------- ② member 顺序 → definition_hash ----------------
    print("\n[2] member 顺序变 → definition_hash 变")
    spec_model = load_composite_spec(SAMPLE_SPEC)
    swapped = spec_model.model_copy(update={"members": list(reversed(spec_model.members))})
    h_orig = definition_hash(spec_model)
    h_swap = definition_hash(swapped)
    print(f"    原序 {spec_model.members} → hash={h_orig}")
    print(f"    换序 {swapped.members} → hash={h_swap}")
    assert h_orig != h_swap
    swap_spec = _write_spec(sandbox, "cx_demo_swapped",
                            ["cx_demo_x2", "cx_demo_x1"], entry)
    run_swap = run_composite(swap_spec, results_dir=runs)
    print(f"    e2e 换序 run: cached={run_swap.cached} "
          f"definition_hash={run_swap.summary['definition_hash'][:16]}...")
    assert run_swap.summary["definition_hash"] != run_a.summary["definition_hash"]
    assert run_swap.frame["signal"].to_list() == [-v for v in actual]
    print("[2] PASS 顺序敏感（hash 变 + 列序语义翻转 y→−y）")

    # ---------------- ③ 成员 artifact 变 → cache 失效 ----------------
    print("\n[3] 成员 artifact 变 → cache 失效重算")
    hit = run_composite(SAMPLE_SPEC, results_dir=runs)
    print(f"    不变重跑: cached={hit.cached} cache_key={hit.summary['cache_key'][:16]}...")
    assert hit.cached is True
    old_key = run_a.summary["cache_key"]
    x2b = {k: v + 1000.0 for k, v in x2.items()}
    _write_member(runs, "cx_demo_x2", x2b)
    re_run = run_composite(SAMPLE_SPEC, results_dir=runs)
    print(f"    成员改后: cached={re_run.cached} old_key={old_key[:16]}... "
          f"new_key={re_run.summary['cache_key'][:16]}...")
    assert re_run.cached is False
    assert re_run.summary["cache_key"] != old_key
    assert re_run.frame["signal"].to_list() != actual
    print("[3] PASS member hash 变 → cache miss + 重算（signal 变）")

    # ---------------- ④ 缺 member → FAIL ----------------
    print("\n[4] 缺 member → FAIL（无产物落盘）")
    miss_spec = _write_spec(sandbox, "cx_demo_missing",
                            ["cx_demo_x1", "cx_demo_missing"], entry)
    miss_msg = ""
    try:
        run_composite(miss_spec, results_dir=runs)
    except MemberResolutionError as exc:
        miss_msg = str(exc)
    print(f"    MemberResolutionError: {miss_msg}")
    assert "cx_demo_missing" in miss_msg
    assert not (runs / "composites" / "cx_demo_missing" / "artifact.json").exists()
    print("[4] PASS 缺成员 fail fast，无 artifact")

    # ---------------- ⑥ output NaN → FAIL ----------------
    print("\n[6] output NaN → FAIL（无产物落盘）")
    nan_entry = _write_impl(sandbox, "nan_impl", _NAN_BODY)
    nan_spec = _write_spec(sandbox, "cx_demo_nan", ["cx_demo_x1", "cx_demo_x2"], nan_entry)
    nan_msg = ""
    try:
        run_composite(nan_spec, results_dir=runs)
    except ValueError as exc:
        nan_msg = str(exc)
    print(f"    ValueError: {nan_msg}")
    assert "NaN" in nan_msg
    assert not (runs / "composites" / "cx_demo_nan" / "artifact.json").exists()
    print("[6] PASS NaN fail fast，无 artifact")

    # ---------------- ⑦ 成员名不进 compute ----------------
    print("\n[7] 成员名不传入 compute()（spy：kwargs/params/X 内容）")
    spy_entry = _write_impl(sandbox, "spy_impl", _SPY_BODY)
    spy_spec = _write_spec(sandbox, "cx_demo_spy", ["cx_demo_x1", "cx_demo_x2"],
                           spy_entry, params={"w": 0.5})
    run_composite(spy_spec, results_dir=runs)
    log_path = sandbox / "research" / "composites" / "implementations" / "calls.jsonl"
    calls = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 1
    call = calls[0]
    print(f"    spy call: shape={call['shape']} params={call['params']} kwargs={call['kwargs']}")
    assert call["shape"] == [8, 2]
    assert call["params"] == {"w": 0.5}
    assert call["kwargs"] == []
    assert "cx_demo_x1" not in call["x_repr"] and "cx_demo_x2" not in call["x_repr"]
    print("[7] PASS compute 仅收到匿名 X/params（无成员名/无句柄关键字）")

    print("\n# ============================================================")
    print("# C1 验收 7/7 PASS（sandbox 保留供复核）")
    print(f"# sandbox = {sandbox}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
