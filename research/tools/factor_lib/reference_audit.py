"""参考库基础指标审计（R37-REF-I2 / issue #31）。

流程（代码级限制的第 1+2 层）：
- 逐员从 5 年产物（`<runs>/<name><suffix>/panel.parquet`）重算 **raw 指标**：
  日频 rank IC（1d/20d）、t、IR、方向一致率、有效天数；
- 对照 `_reference_policy.yaml` 的 **声明窗口**（h20 家族按 20d）与门槛，判 PASS/FAIL；
- 写 sidecar `_reference_metrics.json` + `.md`，附产物**指纹**（size+mtime_ns）；
- `--check`：结构校验（sidecar 存在、产物齐全、指纹未过期）失败 → exit 1；
  门槛违规按 `enforcement: report/enforce`（或 `--strict`）决定是否 exit 1。

设计纪律：
- 直读 parquet 仅 `_scan_panel` 一处（G-READ 登记点；不碰事实库分区）；
- 纯函数可测：metrics/evaluate/check 均不吞异常，输入即输出；
- 指标口径 = 5 年产物（使用口径），**不**替代条件检验（条件证据仍在 `_reference.yaml`）。

用法：
  python reference_audit.py                 # 重算 + 写 sidecar（quantresearch/factor/）
  python reference_audit.py --check         # 门用：结构必须绿；违规报告
  python reference_audit.py --check --strict  # 违规也 exit 1
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import polars as pl
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import quantresearch_paths as QP  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
METRICS_JSON = "_reference_metrics.json"
METRICS_MD = "_reference_metrics.md"
DEFAULT_RUNS = ((QP.ROOT / "results" / "platform")
                if (QP.ROOT / "results" / "platform").is_dir()
                else REPO / "runs" / "platform")

H1 = "forward_return_1d"
H20 = "forward_return_20d"


# ── 直读点（G-READ 登记）────────────────────────────────────────────────
def _scan_panel(path: Path) -> pl.LazyFrame:
    """成员产物 panel 只读扫描（登记理由：参考库审计输入 = 研究产物区自产 panel，
    非事实库分区；只取 signal/forward_return 列）。"""
    return pl.scan_parquet(str(path)).select(
        ["date", "code", "signal", H1, H20])


# ── 指标（纯函数）───────────────────────────────────────────────────────
def _daily_ic(lf: pl.LazyFrame, col: str) -> pl.DataFrame:
    return (lf.filter(pl.col("signal").is_finite() & pl.col(col).is_finite())
              .group_by("date")
              .agg(pl.corr("signal", col, method="spearman").alias("ic"))
              .collect()
              .filter(pl.col("ic").is_finite()))


def _stats(ics: pl.Series, direction: int | None) -> dict:
    n = ics.len()
    if n == 0:
        return {"n_days": 0, "ic": None, "t": None, "ir": None,
                "consistency": None}
    mean, std = ics.mean(), ics.std()
    t = mean / (std / math.sqrt(n)) if std else None
    cons = None
    if direction is not None:
        cons = float(((ics > 0) == (direction == 1)).mean())
    return {"n_days": n, "ic": mean, "t": t,
            "ir": (mean / std) if std else None, "consistency": cons}


def compute_metrics(panel_path: Path, direction: int | None) -> dict:
    """1d/20d raw 指标（缺失列 → polars 报错，不静默）。"""
    lf = _scan_panel(panel_path)
    out: dict = {}
    for tag, col in (("1d", H1), ("20d", H20)):
        out[tag] = _stats(_daily_ic(lf, col)["ic"], direction)
    return out


# ── 判定（纯函数）───────────────────────────────────────────────────────
def declared_horizon(name: str, policy: dict) -> str:
    return "20d" if name in set(policy.get("horizons", {}).get("h20", [])) \
        else "1d"


def evaluate(name: str, metrics: dict, policy: dict) -> tuple[str, list[str]]:
    """按声明窗口判定 PASS/FAIL；返回 (status, failures)。"""
    floors = policy["floors"]
    tag = declared_horizon(name, policy)
    m = metrics[tag]
    fails: list[str] = []
    if (m["t"] is None) or abs(m["t"]) < floors["min_abs_t"]:
        fails.append(f"{tag}_t")
    if (m["ir"] is None) or abs(m["ir"]) < floors["min_ir"]:
        fails.append(f"{tag}_ir")
    if tag == "20d":
        # h20：符号校验（raw 符号 == 使用方向）；逐日一致率对 20d 重叠样本不适用
        if m["ic"] is None or (m["ic"] > 0) != (metrics.get("direction") == 1):
            fails.append("h20_sign")
    else:
        if m["consistency"] is None or m["consistency"] < floors[
                "min_direction_consistency"]:
            fails.append("consistency")
    if m["n_days"] < floors["min_days"]:
        fails.append("n_days")
    return ("PASS" if not fails else "FAIL"), fails


# ── 产物指纹 ────────────────────────────────────────────────────────────
def fingerprint(path: Path) -> dict | None:
    if not path.is_file():
        return None
    st = path.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


# ── 方向解析（spec 为准；产物 summary 兜底）────────────────────────────
_SPEC_CACHE: dict[str, dict[str, Path]] = {}


def _spec_index(factor_dir: Path) -> dict[str, Path]:
    key = str(factor_dir)
    if key not in _SPEC_CACHE:
        idx: dict[str, Path] = {}
        if factor_dir.is_dir():
            for p in factor_dir.glob("*/*.yaml"):
                t = p.read_text(encoding="utf-8")
                m = re.match(r"^name: (\S+)\s*$", t, re.M)
                if m:
                    idx[m.group(1)] = p
        _SPEC_CACHE[key] = idx
    return _SPEC_CACHE[key]


def member_direction(name: str, factor_dir: Path, runs: Path,
                     suffix: str) -> int | None:
    p = _spec_index(factor_dir).get(name)
    if p is not None:
        m = re.search(r"^direction: (-?\d+)", p.read_text(encoding="utf-8"),
                      re.M)
        if m:
            return int(m.group(1))
    s = runs / f"{name}{suffix}" / "summary.json"
    if s.is_file():
        return json.load(s.open(encoding="utf-8")).get("direction")
    return None


# ── 报告 ────────────────────────────────────────────────────────────────
def build_report(*, research_root: Path, runs: Path, suffix: str,
                 members: list[dict], policy: dict) -> dict:
    """members: [{'name':..., 'scale':...}]（顺序 = 参考库声明序）。"""
    factor_dir = research_root / "factor"
    out_members = []
    for m in members:
        name = m["name"]
        d = member_direction(name, factor_dir, runs, suffix)
        run_dir = runs / f"{name}{suffix}"
        panel = run_dir / "panel.parquet"
        entry = {"name": name, "scale": m.get("scale"), "direction": d,
                 "horizon": declared_horizon(name, policy),
                 "artifact_dir": f"{name}{suffix}"}
        if not panel.is_file():
            entry.update({"status": "MISSING", "failures": ["panel"],
                          "metrics": None, "null_ratio": None,
                          "fingerprint": None})
            out_members.append(entry)
            continue
        metrics = compute_metrics(panel, d)
        metrics["direction"] = d
        summary = {}
        sp = run_dir / "summary.json"
        if sp.is_file():
            summary = json.load(sp.open(encoding="utf-8"))
        entry.update({"metrics": metrics,
                      "null_ratio": summary.get("signal_null_ratio"),
                      "fingerprint": fingerprint(panel)})
        status, fails = evaluate(name, metrics, policy)
        entry["status"], entry["failures"] = status, fails
        out_members.append(entry)
    return {"policy_version": policy.get("version"),
            "artifact_suffix": suffix,
            "n_members": len(out_members),
            "n_fail": sum(1 for x in out_members if x["status"] != "PASS"),
            "members": out_members}


def _fmt(v, p=4) -> str:
    return "—" if v is None else f"{v:+.{p}f}"


def write_sidecar(report: dict, out_dir: Path) -> tuple[Path, Path]:
    jp = out_dir / METRICS_JSON
    mp = out_dir / METRICS_MD
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                  encoding="utf-8")
    lines = [f"# 参考库基础指标（{report['n_members']} 员；FAIL "
             f"{report['n_fail']}）", "",
             "| 成员 | 窗口 | 方向 | IC(1d) | t(1d) | 一致率 | t(20d) | 状态 | 失败项 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for x in report["members"]:
        m = x.get("metrics") or {}
        m1, m20 = (m.get("1d") or {}), (m.get("20d") or {})
        cons = m1.get("consistency")
        lines.append(
            f"| {x['name']} | {x.get('horizon')} | {x.get('direction')} | "
            f"{_fmt(m1.get('ic'))} | {_fmt(m1.get('t'), 1)} | "
            f"{'—' if cons is None else f'{cons*100:.0f}%'} | "
            f"{_fmt(m20.get('t'), 1)} | {x['status']} | "
            f"{','.join(x.get('failures') or [])} |")
    mp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jp, mp


# ── 门用检查 ────────────────────────────────────────────────────────────
def check(*, research_root: Path, runs: Path, policy: dict,
          strict: bool) -> tuple[int, list[str]]:
    """结构必须绿；门槛违规按 enforcement/strict。返回 (exit, messages)。"""
    msgs: list[str] = []
    side = research_root / "factor" / METRICS_JSON
    if not side.is_file():
        return 1, [f"sidecar 缺失：{side}（先跑 reference_audit.py 写入）"]
    rep = json.loads(side.read_text(encoding="utf-8"))
    bad_struct = False
    for x in rep["members"]:
        if x["status"] == "MISSING":
            msgs.append(f"[MISSING] {x['name']}（产物不存在）")
            bad_struct = True
            continue
        panel = runs / x["artifact_dir"] / "panel.parquet"
        now = fingerprint(panel)
        if now != x.get("fingerprint"):
            msgs.append(f"[STALE] {x['name']}：产物指纹变化（重跑后需重写 sidecar）")
            bad_struct = True
    fails = [x for x in rep["members"] if x["status"] == "FAIL"]
    if fails:
        msgs.append(f"门槛违规 {len(fails)} 员：" + ", ".join(
            f"{x['name']}({','.join(x['failures'])})" for x in fails))
    if bad_struct:
        return 1, msgs
    if fails and (strict or policy.get("enforcement") == "enforce"):
        return 1, msgs
    return 0, msgs


# ── CLI ─────────────────────────────────────────────────────────────────
def _members_from_reference(research_root: Path) -> list[dict]:
    ref = yaml.safe_load((research_root / "factor" / "_reference.yaml")
                         .read_text(encoding="utf-8"))
    out = []
    for scale, ms in ref["scales"].items():
        for m in ms:
            out.append({"name": m["name"], "scale": scale})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="参考库基础指标审计")
    ap.add_argument("--research-root", default=str(QP.ROOT))
    ap.add_argument("--runs-root", default=str(DEFAULT_RUNS))
    ap.add_argument("--policy", default=None)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args(argv)
    research_root = Path(a.research_root)
    runs = Path(a.runs_root)
    policy_path = Path(a.policy) if a.policy else \
        research_root / "factor" / "_reference_policy.yaml"
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    if a.check:
        code, msgs = check(research_root=research_root, runs=runs,
                           policy=policy, strict=a.strict)
        for m in msgs:
            print(("  ✗ " if code else "  ! ") + m)
        print(f"[G-REF] {'失败' if code else '结构绿'}"
              f"（enforcement={policy.get('enforcement')}）")
        return code
    report = build_report(
        research_root=research_root, runs=runs,
        suffix=policy.get("artifact_suffix", ""),
        members=_members_from_reference(research_root), policy=policy)
    jp, mp = write_sidecar(report, research_root / "factor")
    print(f"审计完成：{report['n_members']} 员，FAIL {report['n_fail']}；"
          f"sidecar: {jp.name} / {mp.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
