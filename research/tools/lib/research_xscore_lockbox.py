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
import re
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np


@contextmanager
def single_writer_lock(path: Path):
    """Serialize one xscore version across Prefect processes and workers."""
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _ensure_platform_src() -> None:
    """Load FactorLab from this checkout and reject a different installation."""
    from _env import ensure_platform

    ensure_platform()


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
    return file_content_sha256(p)[:16]

def file_content_sha256(path: Path) -> str:
    """Stream a file's full SHA-256, independent of its path and mtime."""
    p = Path(path)
    if not p.is_file():
        return "missing"
    digest = hashlib.sha256()
    with p.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

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

def _matching_final_by_params(
    conn, *, window_id: str, params: Mapping[str, Any]
) -> Any | None:
    """Return the latest access for the same logical xscore candidate.

    Older xscore rows used a path/stat-based artifact fingerprint. The stable
    `config` and content `panel_sig` params let current runs recognize those
    rows after upgrading the fingerprint algorithm.
    """
    rows = conn.execute(
        "SELECT access_id, params, result_ref FROM lockbox_access "
        "WHERE window_id = ? AND kind = 'final' ORDER BY rowid DESC",
        (window_id,),
    )
    for row in rows:
        try:
            row_params = json.loads(row["params"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(row_params, Mapping):
            continue
        if (
            row_params.get("config") == params.get("config")
            and row_params.get("panel_sig") == params.get("panel_sig")
        ):
            return row
    return None


def _matching_final_by_config(
    conn, *, window_id: str, config_sha: str
) -> Any | None:
    """Return a prior final row for this immutable config in the current window."""
    rows = conn.execute(
        "SELECT access_id, params, result_ref FROM lockbox_access "
        "WHERE window_id = ? AND kind = 'final' ORDER BY rowid DESC",
        (window_id,),
    )
    for row in rows:
        try:
            row_params = json.loads(row["params"])
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(row_params, Mapping) and row_params.get("config") == config_sha:
            return row
    return None


def _same_result_ref(actual: str | None, expected: str | None) -> bool:
    if actual is None or expected is None:
        return actual is expected
    if actual == expected:
        return True
    try:
        return Path(actual).resolve() == Path(expected).resolve()
    except (OSError, ValueError):
        return actual == expected


def _matching_published_final(
    conn,
    *,
    access_ids: Sequence[str],
    config_sha: str,
    window_id: str,
    panel: Path,
    panel_sha256: str,
    panel_sig: str,
    expected_result_ref: str,
) -> tuple[Any, str] | None:
    """Validate an access ID already embedded in a published xscore result.

    Old legacy xscore runs persisted a stat signature before scoring. A
    completed report can therefore be replayed by its published access ID even
    after the panel mtime or path changes; the flow must return that existing
    report without rescoring. An unfinished old run can resume only if the old
    stat signature still matches. Once a manifest stores a content SHA, that
    hash binds both completed replay and pending resume to the panel bytes.
    """
    if not access_ids or not panel_sig:
        return None
    is_content_sha = re.fullmatch(r"[0-9a-f]{64}", panel_sig) is not None
    if is_content_sha and panel_sha256 != panel_sig:
        return None
    if not is_content_sha and file_sig(panel) != panel_sig:
        # Historical stat identity can only resume an unfinished run while
        # the original path/size/mtime tuple is unchanged.
        allow_pending = False
    else:
        allow_pending = True

    # `access_ids` is an append-only manifest union. Check newest first so a
    # later re-final supersedes an older published result or pending attempt.
    for access_id in reversed(access_ids):
        if not isinstance(access_id, str) or not access_id:
            continue
        row = conn.execute(
            "SELECT access_id, kind, window_id, params, result_ref "
            "FROM lockbox_access WHERE access_id = ?",
            (access_id,),
        ).fetchone()
        if (
            row is None
            or row["kind"] != "final"
            or row["window_id"] != window_id
        ):
            continue
        try:
            row_params = json.loads(row["params"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(row_params, Mapping) or row_params.get("config") != config_sha:
            continue
        stored_panel_sig = row_params.get("panel_sig")
        # The ledger signature must agree with the signature published beside
        # its access ID. Matching the current panel bytes alone cannot bridge
        # a mismatch: that would let a stale access ID masquerade as this run.
        if stored_panel_sig != panel_sig:
            continue
        if row["result_ref"] is not None:
            if not _same_result_ref(row["result_ref"], expected_result_ref):
                continue
            return row, "published"
        if allow_pending:
            return row, "pending"
    return None


def lockbox_register(*, panel: Path, panel_sig: str, config_path: str,
                     replay_ok: bool = False,
                     flow_attempt_sha256: str | None = None,
                     resume_pending: bool = False,
                     published_access_ids: Sequence[str] = (),
                     published_panel_sig: str | None = None,
                     published_window_id: str | None = None,
                     published_sample_role: str | None = None,
                     published_result_ref: str | None = None,
                     db_path: Path | None = None, health_root: Path | None = None,
                     today: dt.date | None = None,
                     panel_start: dt.date | None = None,
                     panel_end: dt.date | None = None) -> dict:
    """R42 flow 起点：钉死候选身份 + 碰箱登记 final（每版本一次；replay 复用）。

    身份（**起点钉死，收尾不得重算**）：
    `artifact_sha256 = file_content_sha256(panel)`（面板内容 SHA-256）、config 部分 = **config 文件内容 sha**
    （对齐平台 `spec_fingerprint`：改内容=新候选），
    `candidate_fingerprint(artifact_sha256, params={"config": config_sha,
    "panel_sig": panel_sig}, window_id, kind="final")`。

    `flow_attempt_sha256` is audit metadata, not part of the candidate
    fingerprint. A pending final row may be resumed only when
    `resume_pending=True` and the latest row for that exact fingerprint has
    `result_ref IS NULL` and carries the same attempt SHA.

    - `FACTORLAB_LOCKBOX ∈ {0,off,false}` → 与无 state 同：不读/不写台账、
      `window_id=None`、`sample_role="unknown"`、`access_id=None`（不再写 `lockbox_off`）；
    - 无 state：不登记、不初始化（真实 roll 由 controller 执行）；
    - `is`（面板整段早于窗口起点）：不登记；
    - `mixed`/`lockbox`：同版本已有 final 登记时——
      ① `FACTORLAB_RE_FINAL=1` → 操作员重测：新登记（`reason` 追加 `|re-final`）；
      ② 否则 `replay_ok=True` 且 manifest access ID、panel 签名、窗口、样本角色、
        `result_ref` 与台账一致 → 复用已发布 `access_id`；目录存在本身不授权复用；
      ③ 否则 → `LOCKBOX_FINAL_DUPLICATE`（改 config/参数=新版本，或
        `FACTORLAB_RE_FINAL=1` 重测）；未命中 → `register_access(kind="final",
      reason="pipeline:<config>")`（无配额）；
    - 碰箱登记而 panel/config 文件缺失（身份=missing）→ `FileNotFoundError`；
    - stale state → `current_window` 原样抛 `LOCKBOX_WINDOW_STALE`（消息指引
      `factorlab lockbox roll`），不得按旧窗静默登记。
    """
    panel = Path(panel)
    artifact = file_content_sha256(panel)
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
        if flow_attempt_sha256 is not None and not re.fullmatch(
            r"[0-9a-f]{64}", flow_attempt_sha256
        ):
            raise ValueError("flow_attempt_sha256 必须是完整小写 SHA-256")
        re_final = os.environ.get("FACTORLAB_RE_FINAL", "").strip() == "1"
        if resume_pending and not flow_attempt_sha256:
            raise ValueError("resume_pending 必须提供 flow_attempt_sha256")
        published_prior = None
        if (
            replay_ok
            and published_access_ids
            and published_window_id == window.window_id
            and published_sample_role == role
            and published_result_ref is not None
        ):
            published_prior = _matching_published_final(
                conn,
                access_ids=published_access_ids,
                config_sha=ctx["config_sha"],
                window_id=window.window_id,
                panel=panel,
                panel_sha256=artifact,
                panel_sig=str(published_panel_sig or ""),
                expected_result_ref=published_result_ref,
            )
            if published_prior is not None and not re_final:
                prior_row, replay_kind = published_prior
                ctx["access_id"] = str(prior_row["access_id"])
                if replay_kind == "published":
                    ctx["published_replay"] = True
                    print(
                        "[lockbox] 复用 manifest 引用的已发布最终测试："
                        f"window={window.window_id} access_id={ctx['access_id']}"
                    )
                else:
                    ctx["pending_resume"] = True
                    print(
                        "[lockbox] 恢复 manifest 引用的未完成旧版 xscore："
                        f"window={window.window_id} access_id={ctx['access_id']}"
                    )
                return ctx
        if not re_final and not store.final_exists(conn, window.window_id, fp):
            # Migrate rows created before file_sig became content based. The
            # stored config and panel hashes identify the logical candidate;
            # keep the decision atomic so a concurrent re-final cannot be
            # mistaken for the row this attempt is allowed to resume.
            conn.execute("BEGIN IMMEDIATE")
            try:
                prior = _matching_final_by_params(
                    conn, window_id=window.window_id, params=params
                )
                if prior is not None:
                    if resume_pending and prior["result_ref"] is None:
                        try:
                            prior_params = json.loads(prior["params"])
                        except (TypeError, json.JSONDecodeError):
                            prior_params = {}
                        if (
                            isinstance(prior_params, Mapping)
                            and prior_params.get("flow_attempt_sha256")
                            == flow_attempt_sha256
                        ):
                            conn.execute("COMMIT")
                            ctx["access_id"] = str(prior["access_id"])
                            print(
                                "[lockbox] 恢复旧指纹未完成 xscore attempt："
                                f"window={window.window_id} "
                                f"access_id={ctx['access_id']}"
                            )
                            return ctx
                    conn.execute("ROLLBACK")
                    raise LockboxError(
                        "LOCKBOX_FINAL_DUPLICATE",
                        f"版本 {fp[:12]}… 在窗口 {window.window_id} 已做过最终测试"
                        "（每版本一次）：改 config/参数=新版本可再测；或设 "
                        "FACTORLAB_RE_FINAL=1 重测（审计留痕）",
                    )
                conn.execute("ROLLBACK")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        if store.final_exists(conn, window.window_id, fp) and not re_final:
            if resume_pending:
                # BEGIN IMMEDIATE makes "latest row + pending + exact attempt"
                # one atomic decision across processes. A newer re-final row
                # supersedes an older pending row and therefore cannot be
                # accidentally resumed.
                conn.execute("BEGIN IMMEDIATE")
                try:
                    row = conn.execute(
                        "SELECT access_id, params, result_ref FROM lockbox_access "
                        "WHERE window_id = ? AND kind = 'final' AND fingerprint = ? "
                        "ORDER BY rowid DESC LIMIT 1",
                        (window.window_id, fp),
                    ).fetchone()
                    matching = False
                    if row is not None and row["result_ref"] is None:
                        try:
                            row_params = json.loads(row["params"])
                        except (TypeError, json.JSONDecodeError):
                            row_params = {}
                        matching = (
                            row_params.get("flow_attempt_sha256")
                            == flow_attempt_sha256
                        )
                    if matching:
                        conn.execute("COMMIT")
                        ctx["access_id"] = str(row["access_id"])
                        print(
                            "[lockbox] 恢复同一未完成 xscore attempt："
                            f"window={window.window_id} access_id={ctx['access_id']}"
                        )
                        return ctx
                    conn.execute("ROLLBACK")
                except BaseException:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    raise
            raise LockboxError(
                "LOCKBOX_FINAL_DUPLICATE",
                f"版本 {fp[:12]}… 在窗口 {window.window_id} 已做过最终测试"
                "（每版本一次）：改 config/参数=新版本可再测；或设 "
                "FACTORLAB_RE_FINAL=1 重测（审计留痕）")
        prior_config = _matching_final_by_config(
            conn, window_id=window.window_id, config_sha=ctx["config_sha"]
        )
        if prior_config is not None and not re_final:
            raise LockboxError(
                "LOCKBOX_FINAL_DUPLICATE",
                f"配置 {ctx['config_sha'][:12]}… 在窗口 {window.window_id} "
                "已有最终测试，但旧面板签名无法与当前结果安全映射；"
                "需使用已有 published manifest replay、创建新不可变配置，"
                "或设 FACTORLAB_RE_FINAL=1 留痕重测",
            )
        exact_fp_exists = store.final_exists(conn, window.window_id, fp)
        manual_re_final_marker = bool(
            re_final
            and not exact_fp_exists
            and (prior_config is not None or published_prior is not None)
        )
        access_params = dict(params)
        if flow_attempt_sha256:
            access_params["flow_attempt_sha256"] = flow_attempt_sha256
        ctx["access_id"] = _pipeline_final_access(conn, fp=fp, params=access_params,
                                                  panel=panel, config_path=config_path,
                                                  window=window,
                                                  re_final=re_final,
                                                  manual_re_final_marker=manual_re_final_marker)
        return ctx
    finally:
        conn.close()

def lockbox_finalize(ctx: Mapping[str, Any], *, run_manifest: Path,
                     campaign_manifest: Path,
                     base_updates: Mapping[str, Any] | None = None,
                     db_path: Path | None = None,
                     result_ref: str | None = None,
                     flow_attempt_sha256: str | None = None) -> dict:
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
            if flow_attempt_sha256 is not None:
                store.finalize_attempt_result(
                    conn,
                    access_id=str(access_id),
                    attempt_sha256=flow_attempt_sha256,
                    result_ref=result_ref,
                )
            else:
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
                           re_final: bool = False,
                           manual_re_final_marker: bool = False) -> str:
    """流水线 final 登记（strict 每版本一次；`re_final` 操作员重测审计留痕）。

    同版本已有登记且非 `re_final` → 原样抛 `LOCKBOX_FINAL_DUPLICATE`（附
    改版本/`FACTORLAB_RE_FINAL=1` 重测指引）；重复即拒，不自动回查复用。
    """
    _ensure_platform_src()
    from factorlab.adapters import lockbox_store as store
    from factorlab.core.lockbox import LockboxError

    reason = f"pipeline:{config_path}"
    if manual_re_final_marker:
        reason += "|re-final"
    try:
        return store.register_access(
            conn, kind="final", fingerprint=fp, artifact=str(panel), params=params,
            command=f"xscore-pipeline config={config_path}", reason=reason,
            window=window, tool="xscore-pipeline",
            re_final=re_final and not manual_re_final_marker)
    except LockboxError as exc:
        if exc.code == "LOCKBOX_FINAL_DUPLICATE":
            raise LockboxError(
                exc.code,
                f"{exc.message}；改 config/参数=新版本可再测；或设 "
                "FACTORLAB_RE_FINAL=1 重测（审计留痕）") from exc
        raise
