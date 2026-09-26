"""Shared xscore pipeline lockbox and manifest adapter.

The legacy xscore pipeline and Prefect xscore flow share this boundary so neither
imports the other's tool implementation. FactorLab remains the source of lockbox
ledger and artifact semantics.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np


def _ensure_platform_src() -> None:
    """Load FactorLab from this checkout and reject a different installation."""
    platform_src = Path(__file__).resolve().parents[3] / "platform" / "src"
    if str(platform_src) not in sys.path:
        sys.path.insert(0, str(platform_src))
    import factorlab

    resolved = Path(factorlab.__file__).resolve()
    if platform_src != resolved.parent and platform_src not in resolved.parents:
        raise RuntimeError(
            f"factorlab 解析到 {resolved}，不在 {platform_src} 之下"
        )


def file_sig(path: Path) -> str:
    p = Path(path)
    if not p.is_file():
        return "missing"
    st = p.stat()
    return hashlib.sha256(f"{p.resolve()}:{st.st_size}:{int(st.st_mtime)}".encode()).hexdigest()[:16]

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

    并发：读-并-写全程持 `<manifest>.lock` 的 flock（仓内单写者模式），同目标的
    并行写者串行化；lock 文件为 0 字节哨兵、可再生。
    """
    written: list[Path] = []
    for p in (Path(run_manifest), Path(campaign_manifest)):
        if p in written:
            continue
        lock = p.with_name(p.name + ".lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "a", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                merged = dict(updates)
                if "access_ids" in merged:
                    merged["access_ids"] = _merged_access_ids(p, merged["access_ids"])
                write_manifest(p, merged)
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        written.append(p)
    return written

def _merged_access_ids(path: Path, new_ids: Any) -> list[str]:
    merged = list(preserved_access_ids(path))
    for access_id in (new_ids if isinstance(new_ids, list) else []):
        if access_id not in merged:
            merged.append(access_id)
    return merged

def file_content_sha(path: Path) -> str:
    """文件内容 sha256（前 16 hex）；缺失 → "missing"。

    候选身份用（对齐平台 `spec_fingerprint` 语义：**改内容=新候选**，改路径/改 mtime 不算）。
    """
    p = Path(path)
    if not p.is_file():
        return "missing"
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]

def lockbox_env_disabled() -> bool:
    """env `FACTORLAB_LOCKBOX` 显式 0/off/false（与平台 `guard_run` 同一开关语义）。"""
    return os.environ.get("FACTORLAB_LOCKBOX", "1").strip().lower() in ("0", "off", "false")

def _lockbox_open(*, panel_start: dt.date | None, panel_end: dt.date | None,
                  today: dt.date | None = None, db_path: Path | None = None,
                  health_root: Path | None = None, allow_stale: bool = False):
    """连台账并解析 `(conn, state, window, role)`；调用方负责 `conn.close()`。

    - 无 state → `(conn, None, None, "unknown")`；
    - state 陈旧（跨季未 roll，LOCKBOX_WINDOW_STALE）：`allow_stale=True`（仅 T9
      `lockbox_sample` 读路径）→ 用 state 窗口诚实标注；登记路径缺省严格原样抛
      （不静默解封，flow 侧 fail-fast）；
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
        if exc.code != "LOCKBOX_WINDOW_STALE" or not allow_stale:
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
    """读锁箱台账（SQLite state + health 日历）→ manifest 样本声明（T9 读路径）。

    - 无 state（锁箱未初始化）→ `{"window_id": None, "sample_role": "unknown"}`；
    - 有 state → `current_window` 对账后 `role_for(panel_start, panel_end, window)`
      判 is/mixed/lockbox（panel 区间缺省回退已发布日历 min/max）；
    - state 陈旧（跨季未 roll，LOCKBOX_WINDOW_STALE）→ 用 state 窗口声明，
      不让流水线收尾失败（诚实标注台账登记窗口）。
    """
    conn, state, window, role = _lockbox_open(
        panel_start=panel_start, panel_end=panel_end, today=today,
        db_path=db_path, health_root=health_root, allow_stale=True)
    try:
        if state is None:
            return {"window_id": None, "sample_role": "unknown"}
        return {"window_id": window.window_id, "sample_role": role}
    finally:
        conn.close()

def lockbox_register(*, panel: Path, panel_sig: str, config_path: str,
                     replay_ok: bool = False,
                     db_path: Path | None = None, health_root: Path | None = None,
                     today: dt.date | None = None,
                     panel_start: dt.date | None = None,
                     panel_end: dt.date | None = None) -> dict:
    """R42 flow 起点：钉死候选身份 + 碰箱登记 final（每版本一次；replay 复用）。

    身份（**起点钉死，收尾不得重算**）：
    `artifact_sha256 = file_sig(panel)`（stat 签名）、config 部分 = **config 文件内容 sha**
    （对齐平台 `spec_fingerprint`：改内容=新候选），
    `candidate_fingerprint(artifact_sha256, params={"config": config_sha,
    "panel_sig": panel_sig}, window_id, kind="final")`。

    - `FACTORLAB_LOCKBOX ∈ {0,off,false}` → 与无 state 同：不读/不写台账、
      `window_id=None`、`sample_role="unknown"`、`access_id=None`（不再写 `lockbox_off`）；
    - 无 state：不登记、不初始化（真实 roll 由 controller 执行）；
    - `is`（面板整段早于窗口起点）：不登记；
    - `mixed`/`lockbox`：同版本已有 final 登记时——
      ① `FACTORLAB_RE_FINAL=1` → 操作员重测：新登记（`reason` 追加 `|re-final`）；
      ② 否则 `replay_ok=True`（run 产物已在）→ 复用既有 `access_id`：不登记、不报错，
        打印"复用既有最终测试（replay）"；
      ③ 否则 → `LOCKBOX_FINAL_DUPLICATE`（改 config/参数=新版本，或
        `FACTORLAB_RE_FINAL=1` 重测）；未命中 → `register_access(kind="final",
      reason="pipeline:<config>")`（无配额）；
    - 碰箱登记而 panel/config 文件缺失（身份=missing）→ `FileNotFoundError`；
    - stale state → `current_window` 原样抛 `LOCKBOX_WINDOW_STALE`（消息指引
      `factorlab lockbox roll`），不得按旧窗静默登记。
    """
    panel = Path(panel)
    artifact = file_sig(panel)
    ctx: dict[str, Any] = {"window_id": None, "sample_role": "unknown",
                           "access_id": None, "fingerprint": None,
                           "artifact_sha256": artifact,
                           "config_sha": file_content_sha(config_path),
                           "panel_sig": str(panel_sig)}
    if lockbox_env_disabled():
        return ctx
    if panel_start is None or panel_end is None:
        dates = panel_dates(panel)
        if dates is not None:
            panel_start, panel_end = dates
    conn, _state, window, role = _lockbox_open(
        panel_start=panel_start, panel_end=panel_end, today=today,
        db_path=db_path, health_root=health_root)
    try:
        if window is not None:
            ctx["window_id"] = window.window_id
        ctx["sample_role"] = role
        if window is None or role == "is":
            return ctx
        if artifact == "missing":
            raise FileNotFoundError(
                f"panel 文件缺失，拒绝以 artifact_sha256=missing 登记锁箱访问：{panel}")
        if ctx["config_sha"] == "missing":
            raise FileNotFoundError(
                f"config 文件缺失，无法钉死候选指纹：{config_path}")
        _ensure_platform_src()
        from factorlab.adapters import lockbox_store as store
        from factorlab.core.lockbox import LockboxError, candidate_fingerprint
        params = {"config": ctx["config_sha"], "panel_sig": ctx["panel_sig"]}
        fp = candidate_fingerprint(artifact_sha256=artifact, params=params,
                                   window_id=window.window_id, kind="final")
        ctx["fingerprint"] = fp
        re_final = os.environ.get("FACTORLAB_RE_FINAL", "").strip() == "1"
        if store.final_exists(conn, window.window_id, fp) and not re_final:
            if replay_ok:
                ctx["access_id"] = store.require_final(
                    conn, window_id=window.window_id, fingerprint=fp)
                print(f"[lockbox] 复用既有最终测试（replay）：window="
                      f"{window.window_id} access_id={ctx['access_id']}")
                return ctx
            raise LockboxError(
                "LOCKBOX_FINAL_DUPLICATE",
                f"版本 {fp[:12]}… 在窗口 {window.window_id} 已做过最终测试"
                "（每版本一次）：改 config/参数=新版本可再测；或设 "
                "FACTORLAB_RE_FINAL=1 重测（审计留痕）")
        ctx["access_id"] = _pipeline_final_access(conn, fp=fp, params=params,
                                                  panel=panel, config_path=config_path,
                                                  window=window, re_final=re_final)
        return ctx
    finally:
        conn.close()

def lockbox_finalize(ctx: Mapping[str, Any], *, run_manifest: Path,
                     campaign_manifest: Path,
                     base_updates: Mapping[str, Any] | None = None,
                     db_path: Path | None = None,
                     result_ref: str | None = None) -> dict:
    """T12b flow 收尾：用起点 context 写 manifest（**不重算 fp/角色**）+ 回填 result_ref。

    `access_id` 非空且给出 `result_ref`（flow 传 run 的 out 目录）时回填台账；
    双写 run/campaign，`access_ids` = 各自既有 ∪ 本次 id。
    """
    access_id = ctx.get("access_id")
    if result_ref is not None and access_id:
        _ensure_platform_src()
        from factorlab.adapters import lockbox_store as store
        if db_path is None:
            from factorlab.config import settings
            db_path = settings.lockbox_db
        conn = store.connect(Path(db_path))
        try:
            store.update_result_ref(conn, str(access_id), result_ref)
        finally:
            conn.close()
    updates = dict(base_updates or {})
    updates.update({"window_id": ctx.get("window_id"),
                    "sample_role": ctx.get("sample_role"),
                    "access_ids": [access_id] if access_id else []})
    write_manifest_pair(run_manifest, campaign_manifest, updates)
    return json.loads(Path(campaign_manifest).read_text(encoding="utf-8"))

def _pipeline_final_access(conn, *, fp: str, params: Mapping[str, Any],
                           panel: Path, config_path: str, window,
                           re_final: bool = False) -> str:
    """流水线 final 登记（strict 每版本一次；`re_final` 操作员重测审计留痕）。

    同版本已有登记且非 `re_final` → 原样抛 `LOCKBOX_FINAL_DUPLICATE`（附
    改版本/`FACTORLAB_RE_FINAL=1` 重测指引）；重复即拒，不自动回查复用。
    """
    _ensure_platform_src()
    from factorlab.adapters import lockbox_store as store
    from factorlab.core.lockbox import LockboxError

    try:
        return store.register_access(
            conn, kind="final", fingerprint=fp, artifact=str(panel), params=params,
            command=f"xscore-pipeline config={config_path}",
            reason=f"pipeline:{config_path}", window=window, tool="xscore-pipeline",
            re_final=re_final)
    except LockboxError as exc:
        if exc.code == "LOCKBOX_FINAL_DUPLICATE":
            raise LockboxError(
                exc.code,
                f"{exc.message}；改 config/参数=新版本可再测；或设 "
                "FACTORLAB_RE_FINAL=1 重测（审计留痕）") from exc
        raise
