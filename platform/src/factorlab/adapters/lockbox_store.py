"""锁箱存储（SQLite）：状态表 + 季度 roll + 登记表 schema（R42 最终测试一次语义）。

窗口数学/指纹在 `core.lockbox`（纯核）；本模块只做文件/DB IO：
- `connect`：WAL + schema（`lockbox_state` 单行 + `lockbox_access` append-only；
  旧版 `uq_lockbox_final` 唯一索引在连接时迁移删除——唯一性改由登记层按
  "每版本一次 + `re_final` 逃生" 实施）
- `roll`：状态推进（拒绝倒退；同窗幂等；`quota_final` 列保留但不再读写）
- `current_window`：state 与当前季度一致性检查（无 state/陈旧各有稳定错误码）
- `published_days`/`latest_data_date`/`run_calendar`：health 已发布日目录扫描
  （执行层日历单点）
- `register_access`/`update_result_ref`/`final_count`/`final_exists`/`require_final`：
  登记（append-only，新登记仅 final）、结果回填、每版本一次唯一性（无配额）
- `RunGuard`/`guard_run`：execute 层硬门（env 开关 → 角色判定 → 探索碰测试段拒 +
  最终测试自动登记 + `summary.sample` 声明 + `result_ref` 回填）
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Mapping, Sequence

from factorlab.core.lockbox import (ACCESS_KINDS, LockboxError, LockboxWindow,
                                    _canonical, actor, candidate_fingerprint,
                                    compute_window, new_access_id, role_for,
                                    spec_fingerprint, window_sort_key)

_DDL = """
CREATE TABLE IF NOT EXISTS lockbox_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    window_id TEXT NOT NULL,
    window_start TEXT NOT NULL,
    -- R42 历史列：保留以兼容既有台账（活代码不读不写；迁移见模块 docstring）
    quota_final INTEGER NOT NULL DEFAULT 20,
    rolled_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lockbox_access (
    access_id TEXT PRIMARY KEY,
    ts_utc TEXT NOT NULL,
    window_id TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    -- R42：'exploration' 仅为历史行兼容（新登记由 register_access 限 final）
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
-- R42 迁移：旧"每候选每窗一次"的 DB 级唯一索引删除（历史行保留）；
-- "每版本一次最终测试"改由 register_access 原子检查（re_final 逃生=操作员留痕重测）。
DROP INDEX IF EXISTS uq_lockbox_final;
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
CREATE TRIGGER IF NOT EXISTS lockbox_state_no_delete
    BEFORE DELETE ON lockbox_state
    BEGIN SELECT RAISE(ABORT, 'lockbox_state protected (no delete/replace)'); END;
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


def roll(conn: sqlite3.Connection, *,
         window: LockboxWindow,
         now: dt.datetime | None = None) -> tuple[LockboxWindow, bool]:
    """状态推进（拒绝倒退；同窗幂等）。

    R42：`quota_final` 参数与语义删除（列保留仅供历史行迁移兼容，不读不写）。
    """
    state = load_state(conn)
    if state is not None:
        if window_sort_key(window.window_id) < window_sort_key(state["window_id"]):
            raise LockboxError("LOCKBOX_ROLL_BACKWARD",
                               f"窗口倒退：state={state['window_id']} < roll={window.window_id}")
        if window.window_id == state["window_id"]:
            return LockboxWindow(state["window_id"],
                                 dt.date.fromisoformat(state["window_start"]),
                                 window.end), False
    now = now or dt.datetime.now(dt.timezone.utc)
    if state is None:
        # quota_final 列走 DDL DEFAULT，不显式写（迁移兼容）
        conn.execute(
            "INSERT INTO lockbox_state"
            " (id, window_id, window_start, rolled_at) VALUES (1,?,?,?)",
            (window.window_id, window.start.isoformat(),
             now.isoformat(timespec="seconds")))
    else:
        # state 禁 DELETE/REPLACE（触发器）；滚动走 UPDATE（不触碰 quota_final 列）
        conn.execute(
            "UPDATE lockbox_state SET window_id=?, window_start=?, rolled_at=?"
            " WHERE id=1",
            (window.window_id, window.start.isoformat(),
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

    R42：仅 `initialized/window_id/window_start/window_end/is_end/finals_total`
    （配额/探索计数删除）。`is_end` = window_start 的前一交易日（供挖矿 spec 的
    `date.end` 直接使用）；日历中无更早交易日时回退 `window_start - 1 天`。
    """
    state = load_state(conn)
    if state is None:
        return {"initialized": False}
    start = dt.date.fromisoformat(state["window_start"])
    earlier = [d for d in trading_days if d < start]
    is_end = max(earlier) if earlier else start - dt.timedelta(days=1)
    return {
        "initialized": True,
        "window_id": state["window_id"],
        "window_start": state["window_start"],
        "window_end": data_end.isoformat(),
        "is_end": is_end.isoformat(),
        "finals_total": final_count(conn, state["window_id"]),
    }


def final_count(conn: sqlite3.Connection, window_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM lockbox_access WHERE window_id = ? AND kind = 'final'",
        (window_id,)).fetchone()
    return int(row[0])


def final_version_fingerprint(*, spec_doc: Mapping[str, Any],
                              window_id: str) -> str:
    """最终测试版本指纹（单点）：spec 内容 sha + `final_test` 参数 + window_id。

    execute 层 `guard_run` 与入库车道冻结件（`test_diagnostics.json`）同源调用，
    防"登记指纹与冻结件指纹漂移"（改了 spec 内容 = 新版本 = 新指纹）。
    """
    return candidate_fingerprint(
        artifact_sha256=spec_fingerprint(spec_doc),
        params={"final_test": True}, window_id=window_id, kind="final")


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
    """同版本 final 的最新一次登记（`re-final` 重测后以新行为准）。"""
    row = conn.execute(
        "SELECT access_id FROM lockbox_access WHERE window_id = ? AND kind = 'final'"
        " AND fingerprint = ? ORDER BY rowid DESC LIMIT 1",
        (window_id, fingerprint)).fetchone()
    return str(row[0]) if row is not None else None


def _unfinished_final_access_id(conn: sqlite3.Connection, window_id: str,
                                fingerprint: str,
                                attempt_sha256: str) -> str | None:
    """Return the latest exact-version access for this unfinished flow attempt."""
    row = conn.execute(
        "SELECT access_id, params, result_ref FROM lockbox_access WHERE window_id = ?"
        " AND kind = 'final' AND fingerprint = ? ORDER BY rowid DESC LIMIT 1",
        (window_id, fingerprint)).fetchone()
    if row is None or row["result_ref"] is not None:
        return None
    try:
        params = json.loads(row["params"])
    except (TypeError, json.JSONDecodeError):
        return None
    if params.get("flow_attempt_sha256") != attempt_sha256:
        return None
    return str(row["access_id"])


def register_access(conn: sqlite3.Connection, *, kind: str, fingerprint: str,
                    artifact: str, params: Mapping[str, Any], command: str,
                    reason: str, window: LockboxWindow, tool: str,
                    result_ref: str | None = None,
                    re_final: bool = False) -> str:
    """登记一次访问（append-only）。R42：新登记仅 `kind="final"`，无配额。

    - 同 `(window_id, fingerprint)` final 已存在且非 `re_final` →
      `LOCKBOX_FINAL_DUPLICATE`（每版本一次）；
    - `re_final=True`（操作员 `FACTORLAB_RE_FINAL=1`）仅在**已有同版本登记**时
      放行再登记并给 `reason` 追加 `|re-final` 审计标记；无既有登记视为首次登记，
      不得产生假审计标记（历史行保留，新行为准）。
    - 既有判定与插入必须原子：`BEGIN IMMEDIATE` 拿写锁后再读（本函数是
      re_final 权威语义所在，调用方预检不作为依据）。
    """
    if kind not in ACCESS_KINDS:
        raise ValueError(
            f"新登记仅支持 final（历史 exploration 行只读）；收到 {kind!r}")
    if not (reason or "").strip():
        raise LockboxError("LOCKBOX_REASON_REQUIRED", "锁箱访问必须给出非空理由")
    reason = reason.strip()
    conn.execute("BEGIN IMMEDIATE")
    try:
        existed = final_exists(conn, window.window_id, fingerprint)
        if existed:
            if not re_final:
                raise LockboxError("LOCKBOX_FINAL_DUPLICATE",
                                   f"版本 {fingerprint[:12]}… 在窗口"
                                   f" {window.window_id} 已做过最终测试（每版本一次）")
            reason = f"{reason}|re-final"
        access_id = _insert_access(
            conn, kind=kind, fingerprint=fingerprint, artifact=artifact,
            params=params, command=command, reason=reason, window=window,
            tool=tool, result_ref=result_ref)
    except LockboxError:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    return access_id


def update_result_ref(conn: sqlite3.Connection, access_id: str,
                      result_ref: str) -> None:
    cur = conn.execute("UPDATE lockbox_access SET result_ref = ?"
                       " WHERE access_id = ?", (result_ref, access_id))
    if cur.rowcount == 0:
        raise ValueError(f"未知 access_id: {access_id}")


def finalize_attempt_result(conn: sqlite3.Connection, *, access_id: str,
                            attempt_sha256: str, result_ref: str) -> None:
    """Idempotently complete a flow attempt after its artifact is published.

    The registry only permits the existing result_ref column to change. This
    helper additionally binds that backfill to the exact immutable flow
    attempt, and rejects changing an already completed reference.
    """
    row = conn.execute(
        "SELECT params, result_ref FROM lockbox_access"
        " WHERE access_id = ? AND kind = 'final'",
        (access_id,)).fetchone()
    if row is None:
        raise ValueError(f"未知 final access_id: {access_id}")
    try:
        params = json.loads(row["params"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"access_id={access_id} params 不可读取") from exc
    if params.get("flow_attempt_sha256") != attempt_sha256:
        raise ValueError(
            f"access_id={access_id} 不属于 flow attempt {attempt_sha256}"
        )
    current = row["result_ref"]
    if current not in (None, result_ref):
        raise ValueError(
            f"access_id={access_id} 已关联不同结果 {current!r}，拒绝覆盖"
        )
    if current is None:
        cursor = conn.execute(
            "UPDATE lockbox_access SET result_ref = ?"
            " WHERE access_id = ? AND result_ref IS NULL",
            (result_ref, access_id))
        if cursor.rowcount == 0:
            latest = conn.execute(
                "SELECT result_ref FROM lockbox_access WHERE access_id = ?",
                (access_id,)).fetchone()
            if latest is None or latest["result_ref"] != result_ref:
                raise ValueError(
                    f"access_id={access_id} 已并发关联其他结果，拒绝覆盖"
                )


def final_exists(conn: sqlite3.Connection, window_id: str,
                 fingerprint: str) -> bool:
    return _final_access_id(conn, window_id, fingerprint) is not None


def require_final(conn: sqlite3.Connection, *, window_id: str,
                  fingerprint: str) -> str:
    access_id = _final_access_id(conn, window_id, fingerprint)
    if access_id is None:
        raise LockboxError(
            "LOCKBOX_FINAL_REQUIRED",
            f"窗口 {window_id} 缺最终测试登记（版本 {fingerprint[:12]}…）："
            "走流水线 `make xpipe` 或入库车道 `flab factor admit`")
    return access_id


class RunGuard:
    """一次评估的锁箱守卫结果：IS=空登记；最终测试=自动登记 + 可回填 result_ref。"""

    def __init__(self, info: dict[str, Any], *,
                 db_path: Path | None = None) -> None:
        self.info = info
        self._db_path = db_path

    @property
    def access_id(self) -> str | None:
        return self.info.get("access_id")

    @property
    def conclusion_eligible(self) -> bool:
        """产物是否可作结论证据（仅 admit/ref add 是固化门）。

        R42：探索碰测试段已被 guard 直接拒绝，能过门的路（is/off/final）产物
        均为运行记录（是否可作结论仍由 admit/ref add 裁定）→ 恒 True。
        """
        return True

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
              final_mode: bool, reason: str | None,
              spec_doc: Mapping[str, Any], artifact: str, command: str,
              tool: str, db_path: Path, trading_days: Sequence[dt.date],
              data_end: dt.date) -> RunGuard:
    """执行层硬门（R42 设计 §3）：按面板区间判定段角色，最终测试一次登记。

    - `FACTORLAB_LOCKBOX ∈ {0,off,false}` → 短路不读不写台账，
      返回 `{"role": "unknown"}`（诚实标注：纪律未启用）；
    - `role=="is"`（整段训练段）→ 放行不登记；
    - `role!="is"` 且 `final_mode=False` → `LOCKBOX_TEST_ONLY_FINAL`（探索/调试
      只准训练段；想看测试段 → 最终测试，每版本一次，走流水线/入库车道）；
    - `final_mode=True`：要求 `FACTORLAB_PIPELINE=1`（否则
      `LOCKBOX_PIPELINE_REQUIRED`）→ 版本指纹（spec 内容 + `final_test` 参数 +
      window_id）→ 已有登记默认 `LOCKBOX_FINAL_DUPLICATE`；操作员
      `FACTORLAB_RE_FINAL=1` 允许再登记（`reason` 追加 `|re-final` 审计标记）→
      否则 `register_access(kind="final")`（无配额）。重复默认拒绝。只有
      Prefect producer 在确认 attempt marker 身份匹配后注入
      `FACTORLAB_LOCKBOX_REPLAY=1` 和其 SHA-256，且最新登记的
      `flow_attempt_sha256` 与之相同、`result_ref IS NULL` 时才复用原 access id
      续跑未完成 attempt；结果引用已回填或 attempt 不匹配时仍拒绝重新计算。
    - 未初始化（无 state）：IS 照常放行；碰测试段非 final → TEST_ONLY_FINAL；
      final 登记 → `LOCKBOX_NO_STATE`（先 `factorlab lockbox roll`）。
    """
    if (os.environ.get("FACTORLAB_LOCKBOX", "1").strip().lower()
            in ("0", "off", "false")):
        return RunGuard({"role": "unknown"})
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
        if not final_mode:
            raise LockboxError(
                "LOCKBOX_TEST_ONLY_FINAL",
                f"评估窗口 [{panel_start}~{panel_end}] 与测试段（{window.window_id}，"
                f"起点 {window.start}）相交：探索只准训练段（`factorlab lockbox "
                "status` 的 is_end 可直接写进 spec.date.end）。测试段只准最终测试"
                "（每版本一次）：走流水线 `make xpipe` 或入库车道 `flab factor admit`")
        if os.environ.get("FACTORLAB_PIPELINE", "").strip() != "1":
            raise LockboxError(
                "LOCKBOX_PIPELINE_REQUIRED",
                "最终测试必须经研究工作流（make xpipe / UI 4200）或入库车道执行"
                "（flab factor admit）；host 直跑不算（FACTORLAB_PIPELINE=1 由流水线"
                "子进程注入）。见 $QUANTRESEARCH_ROOT/knowledge/pipeline-usage.md")
        if no_state:
            raise LockboxError(
                "LOCKBOX_NO_STATE",
                f"评估窗口 [{panel_start}~{panel_end}] 与测试段（{window.window_id}，"
                f"起点 {window.start}）相交但锁箱未初始化：先 `factorlab lockbox roll`")
        fp = final_version_fingerprint(spec_doc=spec_doc,
                                       window_id=window.window_id)
        attempt_sha256 = os.environ.get(
            "FACTORLAB_LOCKBOX_ATTEMPT_SHA256", ""
        ).strip()
        if attempt_sha256 and not re.fullmatch(r"[0-9a-f]{64}", attempt_sha256):
            raise LockboxError(
                "LOCKBOX_ATTEMPT_ID_INVALID",
                "FACTORLAB_LOCKBOX_ATTEMPT_SHA256 必须是完整小写 SHA-256",
            )
        re_final = os.environ.get("FACTORLAB_RE_FINAL", "").strip() == "1"
        exists = final_exists(conn, window.window_id, fp)
        if exists and not re_final:
            if (os.environ.get("FACTORLAB_LOCKBOX_REPLAY", "").strip() == "1"
                    and attempt_sha256):
                access_id = _unfinished_final_access_id(
                    conn, window.window_id, fp, attempt_sha256
                )
                if access_id is not None:
                    info = {"role": role, "window_id": window.window_id,
                            "window_start": window.start.isoformat(),
                            "window_end": window.end.isoformat(),
                            "access_id": access_id}
                    return RunGuard(info, db_path=db_path)
            raise LockboxError(
                "LOCKBOX_FINAL_DUPLICATE",
                f"版本 {fp[:12]}… 在窗口 {window.window_id} 已做过最终测试"
                "（每版本一次）：改 spec 内容/参数=新版本可再测；操作员重测设"
                " FACTORLAB_RE_FINAL=1（审计留痕）")
        # 预检只为错误文案；`re_final` 是否生效由 register_access 事务内权威判定
        params = {"final_test": True}
        if attempt_sha256:
            params["flow_attempt_sha256"] = attempt_sha256
        access_id = register_access(
            conn, kind="final", fingerprint=fp, artifact=artifact,
            params=params, command=command, reason=reason,
            window=window, tool=tool, re_final=re_final)
        info = {"role": role, "window_id": window.window_id,
                "window_start": window.start.isoformat(),
                "window_end": window.end.isoformat(), "access_id": access_id}
        return RunGuard(info, db_path=db_path)
    finally:
        conn.close()
