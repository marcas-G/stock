#!/usr/bin/env python
"""R01 ENG findings 复现/验证 probe（每个 finding 一段，参数化选择）。

用法:
    platform/.venv/bin/python docs/verification/R21/ENG/probe_eng.py C1
    platform/.venv/bin/python docs/verification/R21/ENG/probe_eng.py all

输出为人类可读事实（不依赖断言库）；同脚本在修复前后各跑一次即为
<ID>-probe-before.txt / <ID>-probe-after.txt 的原始输出。
"""
from __future__ import annotations

import argparse
import datetime
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
PLATFORM = REPO / "platform"
SCRATCH = Path("/tmp/opencode/eng-probe")
sys.path.insert(0, str(PLATFORM / "tests"))
sys.path.insert(0, str(PLATFORM / "src"))

DB: Path | None = None


def _seed_db() -> Path:
    """评审 probe 同款最小 duckdb（dualbridge + qfq fixture 60 天）。"""
    global DB
    if DB is not None:
        return DB
    import dualbridge
    import test_qfq_chunk_invariance as tq

    SCRATCH.mkdir(parents=True, exist_ok=True)
    DB = SCRATCH / "probe.duckdb"
    if DB.exists():
        DB.unlink()
    dualbridge.seed_duckdb(DB, tq._qfq_tables(n=60))
    return DB


def _e2e_run(formula: str, name: str, chunk: int | None = None):
    import yaml
    from factorlab.app.context import RunContext
    from factorlab.app.run import run_factor
    from factorlab.core.spec import FactorSpec

    db = _seed_db()
    body = "\n".join("  " + line for line in formula.splitlines())
    spec = FactorSpec.model_validate(yaml.safe_load(f"""
name: {name}
category: custom
direction: 1
universe:
  codes: ["000001.SZ"]
date:
  start: "2024-01-02"
  end: "2024-03-01"
formula: |
{body}
process: []
"""))
    out = SCRATCH / f"run_{name}"
    if out.exists():
        shutil.rmtree(out)
    return run_factor(spec, RunContext(data_backend="duckdb", db_path=db,
                                       output_dir=out, adjustment="raw",
                                       chunk_days=chunk))


def _e2e_report(label: str, formula: str, name: str, chunk: int | None = None,
                expect_shift: int | None = None) -> None:
    import polars as pl

    try:
        res = _e2e_run(formula, name, chunk)
    except Exception as exc:  # noqa: BLE001
        print(f"  {label}: REJECT {type(exc).__name__}: {str(exc)[:160]}")
        return
    frame = res.panel.select(["date", "code", "signal", "close"]).sort(["code", "date"])
    if expect_shift is not None:
        shifted = frame.with_columns(
            pl.col("close").shift(-expect_shift).alias("__expect"))
        nn = frame.filter(pl.col("signal").is_not_null())
        z = shifted.filter(pl.col("signal").is_not_null())
        match = (z["signal"].to_list() == z["__expect"].to_list())
        print(f"  {label}: ACCEPTED rows={frame.height} "
              f"signal(t)==close(t+{expect_shift}): {match}")
    else:
        print(f"  {label}: ACCEPTED rows={frame.height}")


def probe_c1() -> None:
    print("[C1] 未来函数旁路#1：signal = close[-1]（应被拒）")
    from factorlab.core.engine.partitions import reject_future_shifts
    from factorlab.core.factor.ast_gate import validate_formula

    src = "signal = close[-1]"
    for label, fn in (("validate_formula", validate_formula),
                      ("reject_future_shifts", reject_future_shifts)):
        try:
            fn(src)
            print(f"  {label}({src!r}): PASS（旁路未修复）")
        except Exception as exc:  # noqa: BLE001
            print(f"  {label}: REJECT {type(exc).__name__}: {str(exc)[:140]}")
    for expr, want in (("close[1]", "PASS"), ("close[0]", "PASS"),
                       ("close[n]", "PASS")):
        try:
            reject_future_shifts(f"signal = {expr}")
            print(f"  正向控制 {expr}: PASS")
        except Exception as exc:  # noqa: BLE001
            print(f"  正向控制 {expr}: REJECT（回归！）{str(exc)[:120]}")
    _e2e_report("E2E run_factor close[-1]", "signal = close[-1]",
                "c1_probe", expect_shift=1)


def probe_c2() -> None:
    print("[C2] 未来函数旁路#2：_n=3; ts_delay(close, -_n)（应被拒）")
    from factorlab.core.engine.partitions import reject_future_shifts

    for expr in ("-_n", "1 - _n", "0 - _n", "-_n * 1"):
        src = f"_n = 3\nsignal = ts_delay(close, {expr})"
        try:
            reject_future_shifts(src)
            print(f"  {expr}: PASS（旁路未修复）")
        except Exception as exc:  # noqa: BLE001
            print(f"  {expr}: REJECT {type(exc).__name__}: {str(exc)[:120]}")
    for src, label in ((("signal = ts_delay(close, -1)"), "字面量 -1"),
                       (("_n = 3\nsignal = ts_delay(close, _n)"), "正向 _n=3")):
        try:
            reject_future_shifts(src)
            print(f"  控制 {label}: PASS")
        except Exception as exc:  # noqa: BLE001
            print(f"  控制 {label}: REJECT（不应发生）{str(exc)[:120]}")
    _e2e_report("E2E run_factor -_n", "_n = 3\nsignal = ts_delay(close, -_n)",
                "c2_probe", expect_shift=3)


def probe_i1() -> None:
    print("[I1] --chunk-days × 累计算子（文档承诺逐 cell 一致）")
    import polars as pl

    for label, formula in (
        ("ts_cum_sum(close)", "signal = ts_cum_sum(close)"),
        ("vwap(...)", "signal = vwap(high, low, close, volume)"),
    ):
        try:
            full = _e2e_run(formula, "i1_full")
            chunked = _e2e_run(formula, "i1_chunk", chunk=10)
        except Exception as exc:  # noqa: BLE001
            print(f"  {label}: run FAIL {type(exc).__name__}: {str(exc)[:140]}")
            continue
        fa = full.signal_artifact.frame.sort(["date", "code"])
        ca = chunked.signal_artifact.frame.sort(["date", "code"])
        eq = fa.equals(ca)
        n_diff = fa.join(ca, on=["date", "code"], suffix="_c").filter(
            pl.col("signal") != pl.col("signal_c")).height
        print(f"  {label}: full==chunk10 -> {eq}（不一致 {n_diff} 行）")
    # 控制：窗口算子 ts_mean 分块应一致
    full = _e2e_run("signal = ts_mean(close, 3)", "i1_ctl_full")
    ch = _e2e_run("signal = ts_mean(close, 3)", "i1_ctl_chunk", chunk=10)
    print("  控制 ts_mean(close,3): full==chunk10 ->",
          full.signal_artifact.frame.sort(["date", "code"]).equals(
              ch.signal_artifact.frame.sort(["date", "code"])))


_EVIL_PLUGIN = '''
from os import system

import polars as pl

from factorlab.core.ops.registry import factor_op

system("touch {marker}")


@factor_op("ts_evil_probe", kind="ts", version="0.0.1")
def ts_evil_probe(x: pl.Expr, d: int = 1) -> pl.Expr:
    return x - d
'''

_META_PLUGIN = '''
from pathlib import Path

import polars as pl

from factorlab.core.ops.registry import factor_op

Path("{marker}").write_text("executed", encoding="utf-8")


@factor_op("ts_mean", kind="ts", version="9.9.9")
def ts_mean(x: pl.Expr, d: int = 1) -> pl.Expr:
    return x * 0


@factor_op("ts_new_probe", kind="ts", version="0.0.1")
def ts_new_probe(x: pl.Expr, d: int = 1) -> pl.Expr:
    return x - d
'''


def _run_subprocess(code: str) -> str:
    env = dict(**__import__("os").environ)
    env["PYTHONPATH"] = f"{PLATFORM / 'src'}:{PLATFORM / 'tests'}:{env.get('PYTHONPATH', '')}"
    out = subprocess.run([sys.executable, "-c", code], cwd=str(REPO), env=env,
                         capture_output=True, text=True)
    return (out.stdout + out.stderr).strip()


def probe_i2() -> None:
    print("[I2] 插件 AST 安全扫描：from os import system 绕过")
    from factorlab.adapters import plugins

    for src in ("from os import system\n", "from os.path import join\n",
                "from subprocess import run\n", "import os.path\n"):
        try:
            plugins._scan_plugin_ast(src)
            print(f"  扫描 {src.strip()!r}: PASS（旁路未修复）")
        except Exception as exc:  # noqa: BLE001
            print(f"  扫描 {src.strip()!r}: REJECT {str(exc)[:100]}")
    SCRATCH.mkdir(parents=True, exist_ok=True)
    evil = SCRATCH / "evil_plugin.py"
    marker = SCRATCH / "plugin_executed"
    if marker.exists():
        marker.unlink()
    evil.write_text(_EVIL_PLUGIN.format(marker=marker), encoding="utf-8")
    code = f'''
import pathlib
from factorlab.adapters import plugins
try:
    names = plugins.add_plugin(pathlib.Path(r"{evil}"), plugin_dir=pathlib.Path(r"{SCRATCH}/plugins_i2"))
    print("add_plugin evil: ACCEPTED", names)
except Exception as e:
    print("add_plugin evil: REJECT", type(e).__name__, str(e)[:140])
print("marker exists:", pathlib.Path(r"{marker}").exists())
'''
    print("  " + _run_subprocess(code).replace("\n", "\n  "))


def probe_i3() -> None:
    print("[I3] 插件静默覆盖内建算子（import 先于冲突检查）")
    SCRATCH.mkdir(parents=True, exist_ok=True)
    meta = SCRATCH / "meta_plugin.py"
    marker = SCRATCH / "plugin_meta_executed"
    if marker.exists():
        marker.unlink()
    meta.write_text(_META_PLUGIN.format(marker=marker), encoding="utf-8")
    code = f'''
import pathlib
from factorlab.app.bootstrap import ensure_assembly
from factorlab.core.ops import registry
from factorlab.adapters import plugins
ensure_assembly(plugin_dir=pathlib.Path(r"{SCRATCH}/plugins_i3_empty"))
before = registry.get_op("ts_mean")
print("before ts_mean:", before.version, before.func.__module__)
try:
    names = plugins.add_plugin(pathlib.Path(r"{meta}"), plugin_dir=pathlib.Path(r"{SCRATCH}/plugins_i3"), force=False)
    print("add_plugin meta: ACCEPTED", names)
except Exception as e:
    print("add_plugin meta: REJECT", type(e).__name__, str(e)[:140])
after = registry.get_op("ts_mean")
print("after  ts_mean:", after.version, after.func.__module__)
print("builtin unchanged:", after == before)
print("marker exists:", pathlib.Path(r"{marker}").exists())
'''
    print("  " + _run_subprocess(code).replace("\n", "\n  "))


def probe_i4() -> None:
    print("[I4] cs_regression_resid/cs_resid 注册与实际可调用")
    import polars as pl

    from factorlab.app.bootstrap import ensure_assembly
    from factorlab.core.engine.compute import compute_formula
    from factorlab.core.ops import registry

    ensure_assembly()
    for name in ("cs_resid", "cs_regression_resid"):
        print(f"  registry.has_op({name}): {registry.has_op(name)}")
    df = pl.DataFrame({
        "date": [1, 1, 1, 1, 2, 2, 2, 2],
        "code": ["a", "b", "c", "d", "a", "b", "c", "d"],
        "close": [1.0, 2.0, 3.0, 4.0, 2.0, 4.0, 6.0, 8.0],
        "volume": [1.0, 2.0, 4.0, 8.0, 2.0, 3.0, 5.0, 9.0],
    })
    for fn in ("cs_resid", "cs_regression_resid"):
        try:
            r = compute_formula(df, f"signal = {fn}(close, volume)")
            vals = r["signal"].to_list()
            print(f"  compute_formula {fn}(close, volume): OK values={vals[:4]}")
        except Exception as exc:  # noqa: BLE001
            print(f"  compute_formula {fn}(close, volume): FAIL "
                  f"{type(exc).__name__}: {str(exc)[:120]}")


def probe_i5() -> None:
    print("[I5] factorlab lint 语义门")
    yamls = [
        REPO / "docs/reviews/r01-2026-09-15-strict-review/evidence/engine-dsl/future_subscript.yaml",
        REPO / "docs/reviews/r01-2026-09-15-strict-review/evidence/engine-dsl/future_const.yaml",
        REPO / "docs/reviews/r01-2026-09-15-strict-review/evidence/engine-dsl/neg_shift_lint.yaml",
    ]
    for path in yamls:
        out = subprocess.run([str(PLATFORM / ".venv/bin/factorlab"), "lint", str(path)],
                             cwd=str(REPO), capture_output=True, text=True)
        msg = (out.stdout + out.stderr).strip().splitlines()
        print(f"  {path.name}: exit={out.returncode} out={msg[-1] if msg else ''!r}")


PROBES = {"C1": probe_c1, "C2": probe_c2, "I1": probe_i1, "I2": probe_i2,
          "I3": probe_i3, "I4": probe_i4, "I5": probe_i5}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("finding", choices=[*PROBES, "all"])
    args = ap.parse_args()
    print(f"# probe_eng.py {args.finding} @ {datetime.datetime.now().isoformat(timespec='seconds')}")
    targets = PROBES.values() if args.finding == "all" else [PROBES[args.finding]]
    for fn in targets:
        fn()


if __name__ == "__main__":
    main()
