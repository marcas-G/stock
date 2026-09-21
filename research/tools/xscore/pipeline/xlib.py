"""xscore pipeline 共享库：数据/训练/组合评估的纯函数（供 pipeline 各 step 与 Prefect flow 复用）。

约定：本模块在 **platform venv** 下运行（numpy/polars/clickhouse_connect/平台依赖齐全）；
Prefect 只做编排，通过 subprocess 调用本目录脚本。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import yaml

QR = Path("/data/students/gaolei/quantresearch")
STOCK = Path("/data/students/gaolei/stock")
PLATFORM_SRC = STOCK / "platform/src"
PLATFORM_PY = STOCK / "platform/.venv/bin/python"
sys.path.insert(0, str(QR))


def load_service_env() -> dict:
    """加载 CH 只读账号凭据（~/.config/factorlab/service.env；不覆盖已设环境变量）。

    返回 {"user": ...} 便于日志；文件缺失时保持 default 账号（本地 dev 兼容）。
    """
    env_file = Path(os.environ.get(
        "FACTORLAB_SVC_ENV_FILE", Path.home() / ".config/factorlab/service.env"))
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())
    return {"user": os.environ.get("FACTORLAB_CH_USER", "default")}


def git_commit() -> str:
    try:
        sha = subprocess.run(["git", "-C", str(STOCK), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(STOCK), "status", "--porcelain"],
                               capture_output=True, text=True, timeout=10).stdout.strip()
        return f"{sha}{'+dirty' if dirty else ''}"
    except Exception:
        return "unknown"


def file_sig(path: Path) -> str:
    p = Path(path)
    if not p.is_file():
        return "missing"
    st = p.stat()
    return hashlib.sha256(f"{p.resolve()}:{st.st_size}:{int(st.st_mtime)}".encode()).hexdigest()[:16]


def _ensure_platform_src() -> None:
    """让 research venv 也能 import factorlab（flows.py 不在平台 venv 下跑）。"""
    if str(PLATFORM_SRC) not in sys.path:
        sys.path.insert(0, str(PLATFORM_SRC))


def panel_dates(panel_path: Path) -> tuple[dt.date, dt.date] | None:
    """panel npz `dates` 首/末（ISO 字符串）；文件缺失/无 dates/空/非法 → None。"""
    p = Path(panel_path)
    if not p.is_file():
        return None
    try:
        raw = np.load(p, allow_pickle=False)["dates"]
    except (KeyError, OSError, ValueError, EOFError):
        return None
    if raw.size == 0:
        return None
    try:
        return dt.date.fromisoformat(str(raw[0])), dt.date.fromisoformat(str(raw[-1]))
    except ValueError:
        return None


def write_manifest(path: Path, updates: Mapping[str, Any]) -> dict:
    """读-合并-原子写 manifest：已有字段保留，仅覆盖 updates 所列键。"""
    p = Path(path)
    existing: dict = {}
    if p.is_file():
        existing = json.loads(p.read_text(encoding="utf-8"))
    doc = {**existing, **updates}
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)
    return doc


def preserved_access_ids(path: Path) -> list[str]:
    """manifest 既有 `access_ids`：非空列表原样返回；缺失/空/非法 → []。

    流水线每轮刷新 manifest 时用（T10）：`access_ids` 以 [] 起步，但已回填的
    非空值不得被覆盖清空。
    """
    p = Path(path)
    if not p.is_file():
        return []
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    ids = doc.get("access_ids") if isinstance(doc, dict) else None
    return list(ids) if isinstance(ids, list) and ids else []


def write_manifest_pair(run_manifest: Path, campaign_manifest: Path,
                        updates: Mapping[str, Any]) -> list[Path]:
    """双写 run 级 + campaign 级 manifest（T10）：同字段；既有键保留。

    `access_ids` 特例：与目标文件已有非空列表取**并集**（去重保序；T12b）——
    既有回填不被清空，新登记 id 不丢（campaign 跨 run 累积）。同一路径只写一次。
    """
    written: list[Path] = []
    for p in (Path(run_manifest), Path(campaign_manifest)):
        if p in written:
            continue
        merged = dict(updates)
        if "access_ids" in merged:
            merged["access_ids"] = _merged_access_ids(p, merged["access_ids"])
        write_manifest(p, merged)
        written.append(p)
    return written


def _merged_access_ids(path: Path, new_ids: Any) -> list[str]:
    merged = list(preserved_access_ids(path))
    for access_id in (new_ids if isinstance(new_ids, list) else []):
        if access_id not in merged:
            merged.append(access_id)
    return merged


def _lockbox_open(*, panel_start: dt.date | None, panel_end: dt.date | None,
                  today: dt.date | None = None, db_path: Path | None = None,
                  health_root: Path | None = None):
    """连台账并解析 `(conn, state, window, role)`；调用方负责 `conn.close()`。

    - 无 state → `(conn, None, None, "unknown")`；
    - state 陈旧（跨季未 roll，LOCKBOX_WINDOW_STALE）→ 用 state 窗口（与
      lockbox_sample 同一诚实标注口径，不让流水线收尾失败）；
    - panel 区间缺省回退已发布日历 min/max（空日历回退 data_end）。
    """
    _ensure_platform_src()
    from factorlab.adapters import lockbox_store as store
    from factorlab.core.factio.paths import DATA_ROOT
    from factorlab.core.lockbox import LockboxError, LockboxWindow, role_for

    if db_path is None:
        from factorlab.config import settings
        db_path = settings.lockbox_db
    health = Path(health_root) if health_root is not None else Path(DATA_ROOT) / "health"
    today = today or dt.date.today()
    days = store.published_days(health)
    data_end = store.latest_data_date(health) or today
    conn = store.connect(Path(db_path))
    try:
        state = store.load_state(conn)
    except Exception:
        conn.close()
        raise
    if state is None:
        return conn, None, None, "unknown"
    try:
        window = store.current_window(conn, as_of=today, trading_days=days,
                                      data_end=data_end)
    except LockboxError as exc:
        if exc.code != "LOCKBOX_WINDOW_STALE":
            conn.close()
            raise
        window = LockboxWindow(state["window_id"],
                               dt.date.fromisoformat(state["window_start"]), data_end)
    if panel_start is None or panel_end is None:
        panel_start, panel_end = (min(days), max(days)) if days else (data_end, data_end)
    return conn, state, window, role_for(panel_start, panel_end, window)


def lockbox_sample(*, panel_start: dt.date | None, panel_end: dt.date | None,
                   today: dt.date | None = None, db_path: Path | None = None,
                   health_root: Path | None = None) -> dict:
    """读锁箱台账（SQLite state + health 日历）→ manifest 样本声明。

    - 无 state（锁箱未初始化）→ `{"window_id": None, "sample_role": "unknown"}`；
    - 有 state → `current_window` 对账后 `role_for(panel_start, panel_end, window)`
      判 is/mixed/lockbox（panel 区间缺省回退已发布日历 min/max）；
    - state 陈旧（跨季未 roll，LOCKBOX_WINDOW_STALE）→ 用 state 窗口声明，
      不让流水线收尾失败（诚实标注台账登记窗口）。
    """
    conn, state, window, role = _lockbox_open(
        panel_start=panel_start, panel_end=panel_end, today=today,
        db_path=db_path, health_root=health_root)
    try:
        if state is None:
            return {"window_id": None, "sample_role": "unknown"}
        return {"window_id": window.window_id, "sample_role": role}
    finally:
        conn.close()


def lockbox_register_and_manifest(*, run_manifest: Path, campaign_manifest: Path,
                                  panel: Path, panel_sig: str, config_path: str,
                                  base_updates: Mapping[str, Any] | None = None,
                                  db_path: Path | None = None,
                                  health_root: Path | None = None,
                                  today: dt.date | None = None,
                                  result_ref: str | None = None,
                                  panel_start: dt.date | None = None,
                                  panel_end: dt.date | None = None) -> dict:
    """T12b 流水线锁箱接线：角色判定 →（碰箱）幂等 final 登记 → 双写 manifest。

    裁定：流水线 **config = 候选**（一次 config 一次终评）——id 取
    `candidate_fingerprint(artifact_sha256=file_sig(panel),
    params={"config": config_path, "panel_sig": panel_sig}, kind="final")`：

    - 无 state：不登记、不初始化（`sample_role="unknown"`、`window_id=null`、
      `access_ids` 保持既有）；真实台账 roll 由 controller 执行；
    - `is`（面板整段早于窗口起点）：不登记，`sample_role="is"` + 真实 window_id；
    - `mixed`/`lockbox`：`require_final` 命中复用 access_id（重跑幂等、不耗配额），
      否则 `register_access(kind="final", reason="pipeline:<config>")`；
      配额不足 → `LOCKBOX_QUOTA_EXCEEDED`，消息指引 `factorlab lockbox status`；
    - `result_ref` 非空时回填（flow 结束传 run 的 out 目录）；
    - 返回 campaign 级 manifest 文档（access_ids = 既有 ∪ 本次 id）。
    """
    _ensure_platform_src()
    from factorlab.adapters import lockbox_store as store
    from factorlab.core.lockbox import LockboxError, candidate_fingerprint

    panel = Path(panel)
    if panel_start is None or panel_end is None:
        dates = panel_dates(panel)
        if dates is not None:
            panel_start, panel_end = dates
    conn, state, window, role = _lockbox_open(
        panel_start=panel_start, panel_end=panel_end, today=today,
        db_path=db_path, health_root=health_root)
    try:
        access_id: str | None = None
        if window is not None and role != "is":
            params = {"config": str(config_path), "panel_sig": str(panel_sig)}
            fp = candidate_fingerprint(artifact_sha256=file_sig(panel), params=params,
                                       window_id=window.window_id, kind="final")
            access_id = _pipeline_final_access(conn, store, fp=fp, params=params,
                                               panel=panel, config_path=config_path,
                                               window=window)
            if result_ref is not None:
                store.update_result_ref(conn, access_id, result_ref)
        updates = dict(base_updates or {})
        updates.update({"window_id": window.window_id if window is not None else None,
                        "sample_role": role,
                        "access_ids": [access_id] if access_id else []})
        write_manifest_pair(run_manifest, campaign_manifest, updates)
        return json.loads(Path(campaign_manifest).read_text(encoding="utf-8"))
    finally:
        conn.close()


def _pipeline_final_access(conn, store, *, fp: str, params: Mapping[str, Any],
                           panel: Path, config_path: str, window) -> str:
    """流水线 final 登记：先查复用（幂等），缺则登记；配额错误附 status 指引。"""
    from factorlab.core.lockbox import LockboxError

    try:
        return store.require_final(conn, window_id=window.window_id, fingerprint=fp)
    except LockboxError as exc:
        if exc.code != "LOCKBOX_FINAL_REQUIRED":
            raise
    try:
        return store.register_access(
            conn, kind="final", fingerprint=fp, artifact=str(panel), params=params,
            command=f"xscore-pipeline config={config_path}",
            reason=f"pipeline:{config_path}", window=window, tool="xscore-pipeline")
    except LockboxError as exc:
        if exc.code == "LOCKBOX_FINAL_DUPLICATE":  # 并发竞态：回查复用（同 guard）
            return store.require_final(conn, window_id=window.window_id, fingerprint=fp)
        if exc.code == "LOCKBOX_QUOTA_EXCEEDED":
            raise LockboxError(
                exc.code,
                f"{exc.message}；运行 `factorlab lockbox status` 查看配额/已用"
                "（必要时经 `factorlab lockbox roll` 对齐窗口）") from exc
        raise


def load_panel(path: Path):
    sys.path.insert(0, str(QR))
    from lab.autoencoder42 import panel as pm
    return pm.load_panel(path)


def cs_y(target: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """训练目标：逐日截面 robust z（spec 的 CSRobustNormalize）。"""
    sys.path.insert(0, str(STOCK / "research/tools/xscore"))
    import calibrate as C
    out = np.full(target.shape, np.nan)
    for d in range(target.shape[0]):
        ok = valid[d] & np.isfinite(target[d])
        if ok.sum() >= 30:
            out[d, ok] = C.robust_z(target[d, ok])
    return out


def make_score_model(kind: str):
    sys.path.insert(0, str(STOCK / "research/tools/xscore"))
    import score_model as P
    table = {
        "M0a": dict(representation="identity", aggregator="ridge"),
        "M0b": dict(representation="identity", aggregator="huber",
                    agg_kwargs={"max_iter": 20}),
        "M1": dict(representation="core", aggregator="huber",
                   agg_kwargs={"max_iter": 20}),
        "M2": dict(representation="extended", aggregator="ridge"),
        "M3": dict(representation="core", aggregator="enet"),
        "M4": dict(representation="core", aggregator="pls"),
    }
    if kind not in table:
        raise ValueError(f"未知模型 {kind!r}（{sorted(table)}）")
    return P.ScoreModel(**table[kind])


def train_signal(panel, cols: list[int], folds, *, model: str = "M0a",
                 subsample: int | None = None, seed: int = 0) -> dict:
    tgt = panel.target.astype(np.float64)
    valid = panel.mask & np.isfinite(tgt)
    y_cs = cs_y(tgt, valid)
    D, N = tgt.shape
    sig = np.full((D, N), np.nan)
    rng = np.random.default_rng(seed)
    for f in folds:
        vtr = valid[f.train_start:f.train_end] & np.isfinite(y_cs[f.train_start:f.train_end])
        Xtr = panel.raw[f.train_start:f.train_end][:, :, cols][vtr]
        ytr = y_cs[f.train_start:f.train_end][vtr]
        gtr = np.broadcast_to(np.arange(f.train_start, f.train_end)[:, None],
                              vtr.shape)[vtr]
        if subsample and len(ytr) > subsample:
            sel = rng.choice(len(ytr), size=subsample, replace=False)
            Xtr, ytr, gtr = Xtr[sel], ytr[sel], gtr[sel]
        m = make_score_model(model).fit(Xtr, ytr, groups=gtr)
        for d in range(f.test_start, f.test_end):
            idx = np.flatnonzero(valid[d])
            if len(idx):
                sig[d, idx] = m.predict(panel.raw[d][idx][:, cols])
    return {"signal": sig, "target": tgt, "valid": valid}


def score_metrics(sig, tgt, valid, dates):
    sys.path.insert(0, str(QR))
    from lab.autoencoder42 import metrics
    m_eval = valid & np.isfinite(sig)
    ic = metrics.daily_rank_ic(sig, tgt, m_eval)
    st = metrics.ic_stats(ic)
    st["t_nw"] = metrics.newey_west_t(ic)
    ac = metrics.signal_autocorr(sig, m_eval)
    return {"ic": st, "autocorr": ac,
            "ic_by_year": metrics.ic_by_period(dates, ic, freq="Y")}


def mv_quintiles(tgt, valid, mv):
    Q = np.full(tgt.shape, -1, dtype=np.int8)
    v_all = valid & np.isfinite(mv) & (mv > 0)
    for t in range(tgt.shape[0]):
        v = np.flatnonzero(v_all[t])
        if len(v) >= 50:
            ranks = np.argsort(np.argsort(mv[t, v]))
            Q[t, v] = np.minimum(4, ranks * 5 // len(v)).astype(np.int8)
    return Q


def portfolio_eval(sig, *, ret_close, ret_open, valid, tradable_open, domain_mask=None,
                   exec_mode="open", every=5, q=0.1, fee_bps=7.0):
    """返回组合统计；`exec_mode=open` 为 T 信号→T+1 开盘（shift=1），close 为 T 日收盘。"""
    returns, tradable, shift = ((ret_open, tradable_open, 1) if exec_mode == "open"
                                else (ret_close, valid, 0))
    D, N = sig.shape
    vv = tradable.copy()
    if domain_mask is not None:
        vv &= domain_mask
    wts = np.zeros((D, N))
    Wb = np.zeros((D, N))
    prev = np.zeros(N)
    net = np.zeros(D)
    bnet = np.zeros(D)
    turn = np.zeros(D)
    sok = np.isfinite(sig)
    for d in range(D):
        t = d - shift
        if t >= 0 and t % every == 0 and sok[t].any():
            idx = np.flatnonzero(vv[d] & sok[t])
            if len(idx) >= 30:
                k = max(1, int(len(idx) * q))
                wts[d] = 0.0
                wts[d, idx[np.argsort(sig[t, idx], kind="stable")[-k:]]] = 1.0 / k
        elif d > 0:
            wts[d] = wts[d - 1]
        if t >= 0 and t % every == 0:
            v = np.flatnonzero(vv[d])
            Wb[d] = 0.0
            if len(v):
                Wb[d, v] = 1.0 / len(v)
        elif d > 0:
            Wb[d] = Wb[d - 1]
        dw = wts[d] - prev
        turn[d] = 0.5 * np.abs(dw).sum()
        net[d] = np.nansum(wts[d] * np.nan_to_num(returns[d])) - np.abs(dw).sum() * fee_bps / 1e4
        bnet[d] = np.nansum(Wb[d] * np.nan_to_num(returns[d]))
        prev = wts[d]
    first = int(np.flatnonzero(wts.any(axis=1))[0]) if wts.any() else 0
    x, b = net[first:], bnet[first:]
    ex = x - b
    ok = np.isfinite(x) & np.isfinite(b)
    x, b, ex = x[ok], b[ok], ex[ok]
    nav = np.cumprod(1 + x)
    bnav = np.cumprod(1 + b)
    return {
        "ann": float(nav[-1] ** (252.0 / len(x)) - 1),
        "bench": float(bnav[-1] ** (252.0 / len(x)) - 1),
        "excess": float((1 + ex).prod() ** (252.0 / len(ex)) - 1),
        "ir": float(ex.mean() / ex.std(ddof=1) * np.sqrt(252)),
        "turnover": float(turn[first:][ok].mean()),
        "n_days": int(len(x)), "exec_mode": exec_mode,
    }
