"""锁箱存储（SQLite）：状态表 + 季度 roll + 登记表 schema。

窗口数学/指纹在 `core.lockbox`（纯核）；本模块只做文件/DB IO：
- `connect`：WAL + schema（`lockbox_state` 单行 + `lockbox_access` append-only）
- `roll`：状态推进（拒绝倒退；同窗幂等，配额可显式更新）
- `current_window`：state 与当前季度一致性检查（无 state/陈旧各有稳定错误码）
- `published_days`/`latest_data_date`/`run_calendar`：health 已发布日目录扫描
  （执行层日历单点）
- `register_access`/`update_result_ref`/`final_count`/`final_exists`/`require_final`：
  登记（append-only）、结果回填、终评唯一性与配额校验
- `RunGuard`/`guard_run`：execute 层硬门（env 开关 → 角色判定 → 自动登记 +
  `summary.sample` 声明 + `result_ref` 回填）
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
from pathlib import Path
from typing import Any, Mapping, Sequence

from factorlab.core.lockbox import (ACCESS_KINDS, DEFAULT_QUOTA_FINAL,
                                    LockboxError, LockboxWindow, _canonical,
                                    actor, candidate_fingerprint,
                                    compute_window, new_access_id, role_for,
                                    spec_fingerprint, window_sort_key)

_DDL = """
CREATE TABLE IF NOT EXISTS lockbox_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    window_id TEXT NOT NULL,
    window_start TEXT NOT NULL,
    quota_final INTEGER NOT NULL DEFAULT 20,
    rolled_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lockbox_access (
    access_id TEXT PRIMARY KEY,
    ts_utc TEXT NOT NULL,
    window_id TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('exploration','final')),
    fingerprint TEXT NOT NULL,
    artifact TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',
    command TEXT NOT NULL,
    result_ref TEXT,
    reason TEXT NOT NULL,
    actor TEXT NOT NULL,
    tool TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lockbox_win_kind_fp
    ON lockbox_access(window_id, kind, fingerprint);
CREATE UNIQUE INDEX IF NOT EXISTS uq_lockbox_final
    ON lockbox_access(window_id, fingerprint) WHERE kind = 'final';
CREATE TRIGGER IF NOT EXISTS lockbox_access_no_delete
    BEFORE DELETE ON lockbox_access
    BEGIN SELECT RAISE(ABORT, 'lockbox_access is append-only'); END;
CREATE TRIGGER IF NOT EXISTS lockbox_access_no_update
    BEFORE UPDATE ON lockbox_access
    WHEN NEW.access_id != OLD.access_id
      OR NEW.ts_utc != OLD.ts_utc
      OR NEW.window_id != OLD.window_id
      OR NEW.window_start != OLD.window_start
      OR NEW.window_end != OLD.window_end
      OR NEW.kind != OLD.kind
      OR NEW.fingerprint != OLD.fingerprint
      OR NEW.artifact != OLD.artifact
      OR NEW.params != OLD.params
      OR NEW.command != OLD.command
      OR NEW.reason != OLD.reason
      OR NEW.actor != OLD.actor
      OR NEW.tool != OLD.tool
    BEGIN SELECT RAISE(ABORT, 'lockbox_access is append-only'); END;
"""


def published_days(health_root: Path) -> list[dt.date]:
    """<health_root>/ashare_daily/*.json 的文件名日期（升序；非法文件名忽略）。"""
    dataset_dir = Path(health_root) / "ashare_daily"
    if not dataset_dir.is_dir():
        return []
    days: list[dt.date] = []
    for path in dataset_dir.glob("*.json"):
        try:
            days.append(dt.date.fromisoformat(path.stem))
        except ValueError:
            continue
    return sorted(days)


def latest_data_date(health_root: Path) -> dt.date | None:
    """最新已发布数据日；目录缺失或无可解析文件名返回 None。"""
    days = published_days(health_root)
    return max(days) if days else None


def run_calendar(health_root: Path | None = None) -> tuple[list[dt.date], dt.date]:
    """执行层锁箱日历单点：`(已发布交易日, 最新数据日)`。

    `health_root` 缺省 = `DATA_ROOT/health`（与 CLI `_lockbox_health_root` 同源）；
    data_end 缺省回退 `today`（`published_days` 已升序，空日历无 max 可回退；
    env off 时也会被调用，guard 内部才短路——只读扫描，无副作用）。
    """
    if health_root is None:
        from factorlab.core.factio.paths import DATA_ROOT
        health_root = Path(DATA_ROOT) / "health"
    days = published_days(health_root)
    data_end = days[-1] if days else dt.date.today()
    return days, data_end


def connect(db_path: Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA recursive_triggers = ON")
    conn.executescript(_DDL)
    return conn


def load_state(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM lockbox_state WHERE id = 1").fetchone()
    return dict(row) if row is not None else None


def roll(conn: sqlite3.Connection, *, window: LockboxWindow,
         quota_final: int | None = None,
         now: dt.datetime | None = None) -> tuple[LockboxWindow, bool]:
    state = load_state(conn)
    if state is not None:
        if window_sort_key(window.window_id) < window_sort_key(state["window_id"]):
            raise LockboxError("LOCKBOX_ROLL_BACKWARD",
                               f"窗口倒退：state={state['window_id']} < roll={window.window_id}")
        if window.window_id == state["window_id"]:
            if quota_final is None or int(quota_final) == int(state["quota_final"]):
                return LockboxWindow(state["window_id"],
                                     dt.date.fromisoformat(state["window_start"]),
                                     window.end), False
            window = LockboxWindow(state["window_id"],
                                   dt.date.fromisoformat(state["window_start"]),
                                   window.end)
    quota = int(quota_final if quota_final is not None
                else (state or {}).get("quota_final", DEFAULT_QUOTA_FINAL))
    now = now or dt.datetime.now(dt.timezone.utc)
    conn.execute(
        "INSERT OR REPLACE INTO lockbox_state"
        " (id, window_id, window_start, quota_final, rolled_at) VALUES (1,?,?,?,?)",
        (window.window_id, window.start.isoformat(), quota,
         now.isoformat(timespec="seconds")))
    return window, True


def current_window(conn: sqlite3.Connection, *, as_of: dt.date,
                   trading_days: Sequence[dt.date],
                   data_end: dt.date) -> LockboxWindow:
    state = load_state(conn)
    if state is None:
        raise LockboxError("LOCKBOX_NO_STATE",
                           "锁箱未初始化：先 `factorlab lockbox roll`（IS 运行不需要）")
    expected = compute_window(as_of=as_of, trading_days=trading_days,
                              data_end=data_end)
    if expected.window_id != state["window_id"]:
        raise LockboxError(
            "LOCKBOX_WINDOW_STALE",
            f"状态窗口 {state['window_id']} 与当前季度窗口 {expected.window_id} 不一致："
            "先 `factorlab lockbox roll` 对齐（解封旧窗并入 IS）")
    return LockboxWindow(state["window_id"],
                         dt.date.fromisoformat(state["window_start"]), data_end)


def status(conn: sqlite3.Connection, *, trading_days: Sequence[dt.date],
           data_end: dt.date) -> dict[str, Any]:
    """锁箱状态摘要；未初始化（无 state）返回 `{"initialized": False}`。

    `is_end` = window_start 的前一交易日（设计 §12/§13：供挖矿 spec 的
    `date.end` 直接使用）；日历中无更早交易日时回退 `window_start - 1 天`。
    """
    state = load_state(conn)
    if state is None:
        return {"initialized": False}
    start = dt.date.fromisoformat(state["window_start"])
    earlier = [d for d in trading_days if d < start]
    is_end = max(earlier) if earlier else start - dt.timedelta(days=1)
    used = final_count(conn, state["window_id"])
    return {
        "initialized": True,
        "window_id": state["window_id"],
        "window_start": state["window_start"],
        "window_end": data_end.isoformat(),
        "quota_final": int(state["quota_final"]),
        "final_used": used,
        "exploration_used": exploration_count(conn, state["window_id"]),
        "final_remaining": max(0, int(state["quota_final"]) - used),
        "rolled_at": state["rolled_at"],
        "is_end": is_end.isoformat(),
    }


def final_count(conn: sqlite3.Connection, window_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM lockbox_access WHERE window_id = ? AND kind = 'final'",
        (window_id,)).fetchone()
    return int(row[0])


def exploration_count(conn: sqlite3.Connection, window_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM lockbox_access WHERE window_id = ? AND kind = 'exploration'",
        (window_id,)).fetchone()
    return int(row[0])


def _insert_access(conn: sqlite3.Connection, *, kind: str, fingerprint: str,
                   artifact: str, params: Mapping[str, Any], command: str,
                   reason: str, window: LockboxWindow, tool: str,
                   result_ref: str | None) -> str:
    access_id = new_access_id()
    conn.execute(
        "INSERT INTO lockbox_access (access_id, ts_utc, window_id, window_start,"
        " window_end, kind, fingerprint, artifact, params, command, result_ref,"
        " reason, actor, tool) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (access_id, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
         window.window_id, window.start.isoformat(), window.end.isoformat(),
         kind, fingerprint, artifact, _canonical(dict(params)), command,
         result_ref, reason.strip(), actor(), tool))
    return access_id


def _final_access_id(conn: sqlite3.Connection, window_id: str,
                     fingerprint: str) -> str | None:
    row = conn.execute(
        "SELECT access_id FROM lockbox_access WHERE window_id = ? AND kind = 'final'"
        " AND fingerprint = ? LIMIT 1", (window_id, fingerprint)).fetchone()
    return str(row[0]) if row is not None else None


def register_access(conn: sqlite3.Connection, *, kind: str, fingerprint: str,
                    artifact: str, params: Mapping[str, Any], command: str,
                    reason: str, window: LockboxWindow, tool: str,
                    result_ref: str | None = None) -> str:
    if kind not in ACCESS_KINDS:
        raise ValueError(f"未知访问类型 {kind!r}；可选 {ACCESS_KINDS}")
    if not (reason or "").strip():
        raise LockboxError("LOCKBOX_REASON_REQUIRED", "锁箱访问必须给出非空理由")
    insert = dict(kind=kind, fingerprint=fingerprint, artifact=artifact,
                  params=params, command=command, reason=reason, window=window,
                  tool=tool, result_ref=result_ref)
    if kind != "final":
        return _insert_access(conn, **insert)
    # final 唯一性+配额检查与插入必须原子：BEGIN IMMEDIATE 拿写锁后再读。
    conn.execute("BEGIN IMMEDIATE")
    try:
        if final_exists(conn, window.window_id, fingerprint):
            raise LockboxError("LOCKBOX_FINAL_DUPLICATE",
                               f"候选 {fingerprint[:12]}… 在窗口 {window.window_id} 已有终评")
        state = load_state(conn)
        quota = int((state or {}).get("quota_final", DEFAULT_QUOTA_FINAL))
        if final_count(conn, window.window_id) >= quota:
            raise LockboxError("LOCKBOX_QUOTA_EXCEEDED",
                               f"窗口 {window.window_id} 终评配额 {quota} 已用尽")
        access_id = _insert_access(conn, **insert)
    except LockboxError:
        conn.execute("ROLLBACK")
        raise
    except sqlite3.IntegrityError as e:
        conn.execute("ROLLBACK")
        raise LockboxError(
            "LOCKBOX_FINAL_DUPLICATE",
            f"候选 {fingerprint[:12]}… 在窗口 {window.window_id} 已有终评") from e
    conn.execute("COMMIT")
    return access_id


def update_result_ref(conn: sqlite3.Connection, access_id: str,
                      result_ref: str) -> None:
    cur = conn.execute("UPDATE lockbox_access SET result_ref = ?"
                       " WHERE access_id = ?", (result_ref, access_id))
    if cur.rowcount == 0:
        raise ValueError(f"未知 access_id: {access_id}")


def final_exists(conn: sqlite3.Connection, window_id: str,
                 fingerprint: str) -> bool:
    return _final_access_id(conn, window_id, fingerprint) is not None


def require_final(conn: sqlite3.Connection, *, window_id: str,
                  fingerprint: str) -> str:
    access_id = _final_access_id(conn, window_id, fingerprint)
    if access_id is None:
        raise LockboxError(
            "LOCKBOX_FINAL_REQUIRED",
            f"窗口 {window_id} 缺终评登记（候选 {fingerprint[:12]}…）："
            "先 `flab factor run <spec> --lockbox final --lockbox-reason <理由>`")
    return access_id


class RunGuard:
    """一次评估的锁箱守卫结果：IS=空登记；碰箱=自动登记 + 可回填 result_ref。"""

    def __init__(self, info: dict[str, Any], *,
                 db_path: Path | None = None,
                 intent: str | None = None) -> None:
        self.info = info
        self._db_path = db_path
        self._intent = intent

    @property
    def access_id(self) -> str | None:
        return self.info.get("access_id")

    @property
    def conclusion_eligible(self) -> bool:
        """产物是否可作结论证据（终审裁定：仅 admit/ref add 是固化门）。

        `intent=="final"` → True；exploration（mixed/lockbox 碰箱非终评）→ False；
        `is`/env off（无访问意图）→ True。
        """
        return self._intent != "exploration"

    def attach(self, summary: dict[str, Any]) -> None:
        """产物声明：`summary.sample = {role, window_id, access_id, 窗口端点}`。"""
        summary["sample"] = dict(self.info)

    def sample(self) -> dict[str, Any]:
        """compose/strategy 产物样本声明：info + `conclusion_eligible` 标注。"""
        return {**self.info, "conclusion_eligible": self.conclusion_eligible}

    def mark_result(self, result_ref: str) -> None:
        """产物落盘后回填 `result_ref`（唯一允许回填的列；失败=登记保持 NULL）。"""
        if self.access_id is None or self._db_path is None:
            return
        conn = connect(self._db_path)
        try:
            update_result_ref(conn, self.access_id, result_ref)
        finally:
            conn.close()


def guard_run(*, panel_start: dt.date, panel_end: dt.date,
              intent: str | None, reason: str | None,
              spec_doc: Mapping[str, Any], artifact: str, command: str,
              tool: str, db_path: Path, trading_days: Sequence[dt.date],
              data_end: dt.date) -> RunGuard:
    """执行层硬门（设计 §7）：按面板区间判定 is/mixed/lockbox 并自动登记。

    env `FACTORLAB_LOCKBOX` 显式为 0/off/false → 直接 IS 放行（不读 state、
    不登记）；启用时日历为空/最新数据早于窗口起点由 compute_window 响亮失败
    （LOCKBOX_NO_CALENDAR / LOCKBOX_EMPTY_DATA）。未初始化（无 state）：
    IS 照常放行；碰箱 → LOCKBOX_NO_STATE（先 `factorlab lockbox roll`）。

    final 幂等（T7）：同 `(window_id, fingerprint)` 已有终评 → 复用 access_id，
    不要求本次 reason、不重复登记；并发竞态（check-then-act 间隙被先到者登记）
    捕获 DUPLICATE 后回查复用。登记层 `register_access` 语义不变。
    """
    if (os.environ.get("FACTORLAB_LOCKBOX", "1").strip().lower()
            in ("0", "off", "false")):
        return RunGuard({"role": "is"})
    conn = connect(db_path)
    try:
        no_state = False
        try:
            window = current_window(conn, as_of=dt.date.today(),
                                    trading_days=trading_days, data_end=data_end)
        except LockboxError as exc:
            if exc.code != "LOCKBOX_NO_STATE":
                raise
            no_state = True
            window = compute_window(as_of=dt.date.today(),
                                    trading_days=trading_days, data_end=data_end)
        role = role_for(panel_start, panel_end, window)
        if role == "is":
            return RunGuard({"role": "is"})
        if no_state:
            raise LockboxError(
                "LOCKBOX_NO_STATE",
                f"评估窗口 [{panel_start}~{panel_end}] 与锁箱（{window.window_id}，"
                f"起点 {window.start}）相交但锁箱未初始化：先 `factorlab lockbox roll`")
        if intent is None:
            raise LockboxError(
                "LOCKBOX_INTENT_REQUIRED",
                f"评估窗口 [{panel_start}~{panel_end}] 与锁箱（{window.window_id}，"
                f"起点 {window.start}）相交：加 `--lockbox exploration|final` 与"
                " `--lockbox-reason <理由>`")
        if intent not in ACCESS_KINDS:
            raise ValueError(f"--lockbox 取值 {intent!r}；可选 {ACCESS_KINDS}")
        fp = candidate_fingerprint(artifact_sha256=spec_fingerprint(spec_doc),
                                   params={"intent": intent},
                                   window_id=window.window_id, kind=intent)
        # T7 幂等（修复轮1）：final 先查复用——已有同 fp 登记直接放行，不再要求本次
        # reason（admit/ref add 补终评后无 reason 重跑同一候选）；仅缺登记时校验
        # reason 并登记。探索路径不变（reason 必填）。
        access_id = (_final_access_id(conn, window.window_id, fp)
                     if intent == "final" else None)
        if access_id is None:
            if not (reason or "").strip():
                raise LockboxError("LOCKBOX_REASON_REQUIRED", "锁箱访问必须给出非空理由")
            try:
                access_id = register_access(
                    conn, kind=intent, fingerprint=fp, artifact=artifact,
                    params={"intent": intent}, command=command, reason=reason,
                    window=window, tool=tool)
            except LockboxError as exc:
                # 并发竞态（check-then-act 间隙被先到者登记）：final DUPLICATE →
                # 回查复用；登记层语义保持严格（本回退只发生在 guard 内部）。
                if intent != "final" or exc.code != "LOCKBOX_FINAL_DUPLICATE":
                    raise
                access_id = _final_access_id(conn, window.window_id, fp)
                if access_id is None:
                    raise
        info = {"role": role, "window_id": window.window_id,
                "window_start": window.start.isoformat(),
                "window_end": window.end.isoformat(), "access_id": access_id}
        return RunGuard(info, db_path=db_path, intent=intent)
    finally:
        conn.close()
