#!/usr/bin/env python3
"""R22 值级基线指纹化自动刷新（R31 ci-bootstrap ①；数据更新收尾）。

背景：`platform/tests/test_regression_152.py::test_sample_value_regression` 用真实 CH
把 6 个代表 spec（weekly，`00-baseline/specs/` 副本）逐值与 `00-baseline/*.json`
对拍（|Δ|≤1e-9）。基线是**数据相关锚**：任何 CH 数据更新（daily/bars_1m/moneyflow…）
都会让它漂红；本脚本把它从"人工重刷"升级为"数据更新流程收尾自动刷新"。

流程（全部原子/可回滚）：
1. **数据指纹** = 参与表 `system.parts`（active Σrows + max(modification_time)）逐表
   摘要 → sha256（R31 read-cache 同思路，`bars_source_fingerprint` 的通用版）；
2. 读 `governance/evidence/verification/R22/data-fingerprint.json`（上次记录的指纹）：
   - 未变 → 不动、exit 0（`--auto`：Makefile data-update 收尾用）；
   - `--check`：只报告漂移（exit 1）或一致（exit 0），零写入；指纹档缺失/损坏同样报错；
3. 指纹变（或 `--force`）→ 逐 spec 在同一口径下重跑（真实 CLI + ch 后端，**与
   `test_regression_152` 同参数/窗口/假 PASS health**），全部成功且 summary 通过
   5d 口径检查后才进入提交；
4. 提交：重生成 `00-baseline/*.json`、写 `R22/refresh-<ts>/`（diff.txt 旧→新逐值、
   fingerprint.json、meta.json 含时间/commit/specs）、更新 `data-fingerprint.json`、
   更新测试头注释（`# 基线最近刷新：…` 行，缺则插在 `from __future__` 前）；
   任一步失败 → 已写文件全部回滚、留痕目录清除（不半写）；
5. 任一阶段失败 exit 非零（2=指纹/档不可读，3=跑批/校验失败，4=提交失败已回滚）。

失败与原子性单测：`governance/ops/tests/test_refresh_r22_baseline.py`。
运行（重任务带 8GB 护栏）：`FACTORLAB_MAX_MEMORY=8GB platform/.venv/bin/python
governance/ops/refresh_r22_baseline.py --auto`。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parents[2]
R22 = REPO / "governance/evidence/verification/R22"
BASELINE = R22 / "00-baseline"
FP_FILE = R22 / "data-fingerprint.json"
TEST_FILE = REPO / "platform/tests/test_regression_152.py"

#: 6 个代表 spec（基线副本相对 00-baseline/specs/；与 test_regression_152.py 同源）
SPECS = {
    "reversal_20d": "reversal_20d/reversal_20d.yaml",
    "momentum_20d": "momentum_20d/momentum_20d.yaml",
    "vol_run_energy_symrun": "vol_run_energy/symrun.yaml",
    "low_vol_20d": "volatility/low_vol_20d.yaml",
    "turnover_accel": "liquidity/accel.yaml",
    "reversal_20d_wcorr": "reversal_20d/wcorr.yaml",
}
IC_KEYS = ("mean", "t_stat", "ir")

#: 指纹参与表：引擎读面（ENGINE_SURFACE_TABLES 子集）+ 分钟面 bars_1m。
#: 保守口径：任一表 parts 统计/时间变化即视为数据面漂移（宁可多刷，不可漏刷）。
FINGERPRINT_TABLES = (
    "daily", "adj_factor", "daily_basic", "stk_limit", "stock_basic",
    "trade_cal", "moneyflow", "index_daily", "bars_1m",
)
#: 必参与表（无 active 分区 = 主锚数据缺失，指纹不可判定 → fail loud）
REQUIRED_TABLES = ("daily",)

MARKER = "# 基线最近刷新："
_SCHEMA = 1

Out = Callable[[str], None]
FingerprintFn = Callable[[], dict]
Runner = Callable[[str, Path], dict]
Writer = Callable[[Path, str], None]


class RefreshError(RuntimeError):
    """刷新流程内的可预期失败（跑批/校验/写档），main 映射非零退出。"""


# ---------------------------------------------------------------------------
# 指纹
# ---------------------------------------------------------------------------

def table_fingerprint(rd, *, database: str,
                      tables: tuple[str, ...] = FINGERPRINT_TABLES) -> dict:
    """`system.parts` 指纹摘要：逐表 active Σrows + max(modification_time) → sha256。

    `rd` 需提供 `query_rows(sql, params)`（ch 读句柄/客户端适配）。表无 active
    分区 → `{"rows": 0, "mtime": None}`（键仍在，便于"表出现/消失"也触发漂移）；
    REQUIRED_TABLES 无 active 分区 → RuntimeError（不静默用假指纹）。
    """
    rows = rd.query_rows(
        "SELECT table, sum(rows), max(modification_time) FROM system.parts "
        "WHERE database = %(db)s AND active GROUP BY table ORDER BY table",
        {"db": database})
    present = {r[0]: r for r in rows}
    stats: dict[str, dict] = {}
    for t in tables:
        r = present.get(t)
        stats[t] = ({"rows": 0, "mtime": None} if r is None or r[1] is None
                    else {"rows": int(r[1]), "mtime": str(r[2])})
    for t in REQUIRED_TABLES:
        if t in stats and stats[t]["mtime"] is None:
            raise RuntimeError(
                f"{t} 无 active 分区（库 {database}）——数据指纹不可判定；"
                f"检查 CH 灌入状态")
    return {"schema": _SCHEMA, "database": database, "tables": stats,
            "digest": fingerprint_digest(stats)}


def fingerprint_digest(stats: dict) -> str:
    """逐表摘要 → 稳定 sha256（键排序、紧凑分隔，任何行数/时间变化都变）。"""
    payload = json.dumps(stats, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def ch_fingerprint(*, tables: tuple[str, ...] = FINGERPRINT_TABLES) -> dict:
    """真实 CH 指纹（ch_read 模块即 `query_rows(sql, params)` 适配面）。"""
    from factorlab.adapters import ch_read
    from factorlab.config import settings

    return table_fingerprint(ch_read, database=settings.ch_database, tables=tables)


def load_recorded(path: Path | str) -> dict | None:
    """读上次记录的指纹档；不存在 → None；损坏/结构非法 → RefreshError。"""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RefreshError(f"指纹档损坏（{p}）: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("digest"), str) \
            or not doc["digest"] or not isinstance(doc.get("tables"), dict):
        raise RefreshError(f"指纹档结构非法（{p}）：缺 digest/tables")
    return doc


def stats_diff(old: dict, new: dict) -> list[str]:
    """逐表列出变化的 rows/mtime（未变表不列）。"""
    lines = []
    for t in sorted(set(old) | set(new)):
        a = old.get(t) or {"rows": 0, "mtime": None}
        b = new.get(t) or {"rows": 0, "mtime": None}
        if a != b:
            lines.append(f"{t}: rows {a.get('rows')}->{b.get('rows')} "
                         f"mtime {a.get('mtime')}->{b.get('mtime')}")
    return lines


# ---------------------------------------------------------------------------
# 原子写 + 提交（失败回滚）
# ---------------------------------------------------------------------------

def _atomic_write_text(path: Path, text: str) -> None:
    """同目录 tmp + fsync + os.replace（目录 fsync 尽力）——不留半写文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    try:
        dirfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    except OSError:
        pass


def commit_files(ops: list[tuple[Path, str]], *, writer: Writer | None = None) -> None:
    """按序原子写 `ops`；任一步失败 → 逆序还原已写文件（新增的删除）后重抛。"""
    write = writer or _atomic_write_text
    originals: dict[Path, str | None] = {}
    done: list[Path] = []
    try:
        for path, text in ops:
            originals[path] = (path.read_text(encoding="utf-8")
                               if path.is_file() else None)
            write(path, text)
            done.append(path)
    except Exception:
        for path in reversed(done):
            orig = originals[path]
            if orig is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_write_text(path, orig)
        raise


# ---------------------------------------------------------------------------
# 测试头注释
# ---------------------------------------------------------------------------

def update_test_header(text: str, line: str) -> str:
    """替换 `# 基线最近刷新：` 行；无则插在 `from __future__` 前；无锚点 → RefreshError。"""
    lines = text.splitlines()
    for i, l in enumerate(lines):
        if l.startswith(MARKER):
            lines[i] = line
            return "\n".join(lines) + "\n"
    for i, l in enumerate(lines):
        if l.startswith("from __future__"):
            lines.insert(i, line)
            return "\n".join(lines) + "\n"
    raise RefreshError("测试文件缺 `from __future__` 插入锚点——无法更新头注释")


# ---------------------------------------------------------------------------
# 真实运行（与 test_regression_152 同参数/窗口/health 口径）
# ---------------------------------------------------------------------------

def _fake_health_root(stock_root: Path, ends: list[str]) -> None:
    """与 `test_regression_152._dq_fake_health_env` 同形状的临时 PASS health。

    基线 spec 窗口（end=2026-07-31 等）早于 health 发布区（data/health 仅近期），
    读取门 fail-closed → 重跑必须走与值级测试一致的临时 STOCK_ROOT（不碰生产 data/）。
    """
    for as_of in sorted(set(ends)):
        p = stock_root / "data" / "health" / "ashare_daily" / f"{as_of}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "dataset_id": "ashare_daily", "partition": as_of,
            "data_version": "vTEST", "dq_policy_version": "daily-v1",
            "health_status": "PASS", "verification_state": "VERIFIED",
            "completeness": {"status": "COMPLETE", "expected_count": 1,
                             "actual_count": 1, "coverage": 1.0},
            "quality": {"fatal_count": 0, "error_count": 0, "warning_count": 0,
                        "quarantine_count": 0, "error_rate": 0.0,
                        "systematic_issue": False, "systemic_detail": None},
            "freshness": {"latest_trade_date": as_of}, "rules": {},
            "validated_at": "2026-09-19T00:00:00+08:00",
            "raw_lineage": {"source_version": None, "raw_sha256": None},
        }, ensure_ascii=False), encoding="utf-8")


def factorlab_runner(repo: Path = REPO, *, timeout: int = 3600) -> Runner:
    """真实 CLI runner：`factorlab run --eval-frequency weekly --output-dir <tmp>`。"""
    platform = repo / "platform"
    factorlab = platform / ".venv/bin/factorlab"

    def _run(name: str, spec_path: Path) -> dict:
        import yaml

        doc = yaml.safe_load(Path(spec_path).read_text(encoding="utf-8"))
        end = str(doc["date"]["end"])
        env = {**os.environ, "FACTORLAB_DATA_BACKEND": "ch",
               "FACTORLAB_RESULTS_DIR": str(repo / "runs" / "platform")}
        with tempfile.TemporaryDirectory(prefix="r22-health-") as td:
            stock_root = Path(td) / "stock_root"
            _fake_health_root(stock_root, [end])
            env["FACTORLAB_STOCK_ROOT"] = str(stock_root)
            with tempfile.TemporaryDirectory(prefix="r22-out-") as out_td:
                r = subprocess.run(
                    [str(factorlab), "run", "--eval-frequency", "weekly",
                     "--output-dir", out_td, str(spec_path)],
                    cwd=str(platform), env=env, capture_output=True, text=True,
                    timeout=timeout)
                if r.returncode != 0:
                    raise RefreshError(
                        f"{name} run 失败 exit={r.returncode}:\n"
                        f"{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
                return json.loads(
                    (Path(out_td) / "summary.json").read_text(encoding="utf-8"))

    return _run


def _summary_checks(name: str, summary: dict) -> None:
    """5d 口径检查（与 R30 刷新脚本同断言；D3 不重叠采样对 h≤5 零适用）。"""
    ev = summary.get("evaluation") if isinstance(summary, dict) else None
    if not isinstance(ev, dict):
        raise RefreshError(f"{name}: summary 缺 evaluation")
    if str(ev.get("target")) != "forward_return_5d":
        raise RefreshError(f"{name}: target={ev.get('target')!r} ≠ forward_return_5d")
    if "sampling" in ev:
        raise RefreshError(f"{name}: 5d 路径出现 sampling（D3 不应生效）")
    ic = ev.get("ic") or {}
    if "t_stat_nw" in ic:
        raise RefreshError(f"{name}: 5d 路径出现 t_stat_nw")
    for k in IC_KEYS:
        if not isinstance(ic.get(k), (int, float)) or isinstance(ic.get(k), bool):
            raise RefreshError(f"{name}: ic.{k} 非数值: {ic.get(k)!r}")
    if not isinstance(ev.get("n_weeks"), int) or ev["n_weeks"] <= 0:
        raise RefreshError(f"{name}: n_weeks 非法: {ev.get('n_weeks')!r}")
    cov = ev.get("coverage") or {}
    for k in ("total_rows", "valid_rows"):
        if not isinstance(cov.get(k), int):
            raise RefreshError(f"{name}: coverage.{k} 非法: {cov.get(k)!r}")


def _head_commit(repo: Path) -> str:
    try:
        r = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                           capture_output=True, text=True)
    except OSError:
        return "unknown"
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "unknown"


def _spec_diff_lines(name: str, old_ev: dict, new_ev: dict) -> list[str]:
    oc, nc = old_ev["coverage"], new_ev["coverage"]
    lines = [f"{name}: n_weeks {old_ev['n_weeks']}->{new_ev['n_weeks']}  "
             f"total_rows {oc['total_rows']}->{nc['total_rows']}  "
             f"valid_rows {oc['valid_rows']}->{nc['valid_rows']}"]
    for k in IC_KEYS:
        a, b = old_ev["ic"][k], new_ev["ic"][k]
        lines.append(f"{name}: ic.{k} {a!r}->{b!r} (Δ={b - a:+.3e})")
    return lines


def _old_new_struct(old_ev: dict, new_ev: dict) -> dict:
    return {
        "n_weeks": {"old": old_ev["n_weeks"], "new": new_ev["n_weeks"]},
        "coverage": {"old": dict(old_ev["coverage"]), "new": dict(new_ev["coverage"])},
        "ic": {k: {"old": old_ev["ic"][k], "new": new_ev["ic"][k]}
               for k in IC_KEYS},
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def refresh(*, repo: Path = REPO, mode: str = "auto", now: float | None = None,
            fingerprint_fn: FingerprintFn | None = None,
            runner: Runner | None = None, writer: Writer | None = None,
            out: Out = print) -> int:
    """指纹门 + 刷新。返回 0=成功/跳过，1=--check 漂移，2/3/4=失败（见模块 docstring）。"""
    now = time.time() if now is None else now
    r22 = repo / "governance/evidence/verification/R22"
    baseline = r22 / "00-baseline"
    fp_file = r22 / "data-fingerprint.json"
    test_file = repo / "platform/tests/test_regression_152.py"

    try:
        current = (fingerprint_fn or ch_fingerprint)()
    except Exception as exc:  # noqa: BLE001 —— 指纹不可得一律 fail loud
        out(f"FAIL 数据指纹计算失败：{type(exc).__name__}: {exc}")
        return 2
    try:
        recorded = load_recorded(fp_file)
    except RefreshError as exc:
        out(f"FAIL {exc}")
        return 2

    recorded_digest = recorded.get("digest") if recorded else None
    changed = recorded is None or recorded_digest != current.get("digest")

    if mode == "check":
        if not changed:
            out(f"指纹一致 ✓ digest={current['digest']} "
                f"（recorded_at={recorded.get('recorded_at')}）")
            return 0
        out(f"指纹漂移：recorded={recorded_digest} current={current['digest']}")
        for line in stats_diff(recorded.get("tables", {}) if recorded else {},
                               current.get("tables", {})):
            out("  " + line)
        return 1

    if mode == "auto" and not changed:
        out(f"指纹未变（{current['digest']}）——跳过刷新（exit 0）")
        return 0

    if changed:
        out(f"指纹变化：{recorded_digest} -> {current['digest']}")
        for line in stats_diff(recorded.get("tables", {}) if recorded else {},
                               current.get("tables", {})):
            out("  " + line)
    else:
        out(f"--force：指纹未变（{current['digest']}）仍刷新")

    # 前置：基线与 spec 副本必须在位
    old_summaries: dict[str, dict] = {}
    for name, rel in SPECS.items():
        spec_path = baseline / "specs" / rel
        base_path = baseline / f"{name}.json"
        if not spec_path.is_file():
            out(f"FAIL 基线 spec 副本缺失: {spec_path}")
            return 3
        if not base_path.is_file():
            out(f"FAIL 基线 JSON 缺失: {base_path}")
            return 3
        old_summaries[name] = json.loads(base_path.read_text(encoding="utf-8"))

    # 跑批（全部成功且过检查才允许进入提交）
    run = runner or factorlab_runner(repo)
    new_summaries: dict[str, dict] = {}
    for name, rel in SPECS.items():
        try:
            summary = run(name, baseline / "specs" / rel)
            _summary_checks(name, summary)
        except Exception as exc:  # noqa: BLE001 —— 任一 spec 失败 → 整体失败
            out(f"FAIL {name}: {type(exc).__name__}: {exc}")
            return 3
        new_summaries[name] = summary
        out(f"OK {name}")

    # 提交阶段
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    refresh_dir = r22 / f"refresh-{ts}"
    seq = 1
    while refresh_dir.exists():
        seq += 1
        refresh_dir = r22 / f"refresh-{ts}-{seq}"
    iso = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now))
    commit = _head_commit(repo)

    diff_lines: list[str] = [
        f"# R22 基线刷新 {iso}（refresh_r22_baseline.py）",
        f"# 指纹 old={recorded_digest}",
        f"# 指纹 new={current['digest']}",
        f"# HEAD={commit}",
        f"# 刷新档={refresh_dir.name}",
    ]
    old_new: dict[str, dict] = {}
    for name in SPECS:
        old_ev = old_summaries[name]["evaluation"]
        new_ev = new_summaries[name]["evaluation"]
        diff_lines.extend(_spec_diff_lines(name, old_ev, new_ev))
        old_new[name] = _old_new_struct(old_ev, new_ev)

    record = {**current, "old_digest": recorded_digest, "recorded_at": iso,
              "refresh": refresh_dir.name, "commit": commit}
    trail_fp = {**current, "old_digest": recorded_digest, "refreshed_at": iso,
                "refresh": refresh_dir.name, "commit": commit}
    meta = {"refreshed_at": iso, "epoch": now, "commit": commit,
            "digest_old": recorded_digest, "digest_new": current["digest"],
            "specs": SPECS, "old_new": old_new}

    header_line = (f"{MARKER}{iso} 指纹 sha256:{current['digest']} "
                   f"刷新档 {refresh_dir.name}/（governance/ops/"
                   f"refresh_r22_baseline.py 自动维护）")
    if not test_file.is_file():
        out(f"FAIL 测试文件缺失: {test_file}")
        return 3
    header_text = update_test_header(
        test_file.read_text(encoding="utf-8"), header_line)

    ops: list[tuple[Path, str]] = [
        (refresh_dir / "diff.txt", "\n".join(diff_lines) + "\n"),
        (refresh_dir / "fingerprint.json",
         json.dumps(trail_fp, ensure_ascii=False, indent=2) + "\n"),
        (refresh_dir / "meta.json",
         json.dumps(meta, ensure_ascii=False, indent=2) + "\n"),
    ]
    for name in SPECS:
        ops.append((baseline / f"{name}.json",
                    json.dumps(new_summaries[name], ensure_ascii=False,
                               indent=2) + "\n"))
    ops.append((fp_file, json.dumps(record, ensure_ascii=False, indent=2) + "\n"))
    ops.append((test_file, header_text))

    try:
        commit_files(ops, writer=writer)
    except Exception as exc:  # noqa: BLE001 —— 回滚后报告，不半写
        shutil.rmtree(refresh_dir, ignore_errors=True)
        out(f"FAIL 提交失败（已回滚）: {type(exc).__name__}: {exc}")
        return 4

    for line in "\n".join(diff_lines[5:]).splitlines():
        out("  " + line)
    out(f"刷新完成：refresh-{ts} 档已留痕，基线/指纹/测试头注释已更新")
    return 0


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="R22 值级基线指纹化刷新（数据更新收尾；默认=指纹变才刷）")
    ap.add_argument("--check", action="store_true",
                    help="只报告指纹漂移（0=一致；1=漂移），零写入")
    ap.add_argument("--auto", action="store_true",
                    help="指纹变才刷新，未变 exit 0（Makefile data-update 收尾用）")
    ap.add_argument("--force", action="store_true",
                    help="无条件刷新（人工重刷；仍走完整校验/留痕）")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    mode = "check" if args.check else "force" if args.force else "auto"
    return refresh(mode=mode)


if __name__ == "__main__":
    sys.exit(main())
