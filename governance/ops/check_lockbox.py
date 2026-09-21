#!/usr/bin/env python
"""G-LOCKBOX 判据（T10）：样本声明与锁箱台账交叉核对。

权威层 = campaign 级 `results/<dir>/manifest.json`（与 `research_tidy` 同层，
**不递归** run 子目录）：
- 四键存在与类型复用 `research_tidy._check_manifest_fields`（同一判据，避免漂移）；
- `sample_role ∈ {lockbox,mixed}` → `access_ids` 非空，每个 id 在
  `<root>/data/ledger.sqlite` 的 `lockbox_access` 中 `kind='final'` 且 `window_id`
  与 manifest 一致；台账不存在/无 state → 这些条目 error（`--offline` 跳过台账步）；
- `sample_role ∈ {is,legacy,unknown}` → `access_ids` 必须为空（诚实性：非锁箱声明
  不得挂访问登记）。

档案 = `dossiers/factors/**/*.md`（`_` 前缀模板/无 front matter 文件跳过）：
- `updated_ts >= 2026-09-21`（或 front matter 无 updated_ts）视为新/更新档案，必须有
  `sample_role/window_id/lockbox_access`；lockbox/mixed → `lockbox_access` 非空且 id
  在台账中为 final 且 window 一致；is/legacy/unknown → 空列表；更早档案 grandfather。

`--offline` 只做格式校验（不读台账）；`--selftest` 造假矩阵必抓、干净样本不误伤。
只报告不修改。退出码：0=无 error；1=有 error；root 不存在=SKIP(0)。
用法：platform/.venv/bin/python governance/ops/check_lockbox.py [--root DIR]
      [--offline] [--json] [--selftest]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import NamedTuple

import research_tidy as RT

DEFAULT_ROOT = RT.DEFAULT_ROOT
LEDGER_REL = Path("data") / "ledger.sqlite"
DOSSIER_DIR = Path("dossiers") / "factors"
DOSSIER_REQUIRED = ("sample_role", "window_id", "lockbox_access")
LOCKBOX_ROLES = ("lockbox", "mixed")
OPEN_ROLES = ("is", "legacy", "unknown")
EFFECTIVE_DATE = dt.date(2026, 9, 21)
WINDOW_ID_RE = re.compile(r"^\d{4}Q[1-4]$")
Finding = RT.Finding


class Ledger(NamedTuple):
    present: bool
    has_state: bool
    access: dict  # access_id -> (kind, window_id)


def load_ledger(root: Path) -> Ledger:
    """只读打开 `<root>/data/ledger.sqlite`；不存在/损坏 → 依 state 语义报缺失。

    `mode=ro` 逻辑零写入（SQLite 读取 WAL 库会生成 `-shm`/`-wal` 读侧车，
    属可再生缓存、不改台账内容）；只需 `lockbox_state` 与
    `lockbox_access(access_id, kind, window_id)` 三列（写 schema 在 lockbox_store）。
    """
    path = root / LEDGER_REL
    if not path.is_file():
        return Ledger(False, False, {})
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        return Ledger(True, False, {})
    try:
        state = conn.execute("SELECT window_id FROM lockbox_state WHERE id = 1").fetchone()
        access: dict[str, tuple[str, str]] = {}
        try:
            for aid, kind, window_id in conn.execute(
                    "SELECT access_id, kind, window_id FROM lockbox_access"):
                access[str(aid)] = (str(kind), str(window_id))
        except sqlite3.Error:
            access = {}
    except sqlite3.Error:
        return Ledger(True, False, {})
    finally:
        conn.close()
    return Ledger(True, state is not None, access)


def _check_access_refs(*, path: Path, role: str, ids: list, window_id, ledger: Ledger,
                       offline: bool, where: str) -> list[Finding]:
    """锁箱声明（manifest/档案）的 access_ids × 台账交叉核对。"""
    out: list[Finding] = []
    if not ids:
        out.append(Finding(
            "error", str(path),
            f"sample_role={role} 声明锁箱但 access_ids 为空：锁箱角色必须列出终评登记 id"))
        return out
    window_ok = isinstance(window_id, str) and bool(window_id.strip())
    if not window_ok:
        out.append(Finding("error", str(path),
                           f"{where} 锁箱声明缺 window_id，无法与台账窗口核对"))
    elif not WINDOW_ID_RE.match(window_id):
        out.append(Finding("error", str(path),
                           f"{where} window_id 格式非法：{window_id!r}（应为 YYYYQn）"))
        window_ok = False
    if offline:
        return out
    if not ledger.has_state:
        hint = "台账文件不存在" if not ledger.present else "台账未初始化（无 state）"
        out.append(Finding(
            "error", str(path),
            f"{hint}（{LEDGER_REL}）：access_ids 无法与终评登记核验"))
        return out
    for aid in ids:
        row = ledger.access.get(aid)
        if row is None:
            out.append(Finding("error", str(path),
                               f"access_id {aid!r} 不在台账 lockbox_access 中"))
            continue
        kind, wid = row
        if kind != "final":
            out.append(Finding("error", str(path),
                               f"access_id {aid!r} kind={kind}（探索访问冒充终评）"))
        if window_ok and wid != window_id:
            out.append(Finding("error", str(path),
                               f"access_id {aid!r} window_id={wid!r} 与声明 {window_id!r} 不一致"))
    return out


def check_results(root: Path, ledger: Ledger, *, offline: bool) -> list[Finding]:
    """campaign 级 manifest 层（`results/*/manifest.json`，不递归）。"""
    out: list[Finding] = []
    base = root / RT.RESULTS
    if not base.is_dir():
        return out
    for camp in sorted(base.iterdir()):
        if not camp.is_dir():
            continue
        manifest = camp / RT.MANIFEST
        if not manifest.is_file():
            continue
        fmt = RT._check_manifest_fields(manifest)
        out.extend(fmt)
        if fmt:
            continue  # 键/类型已红：语义核对的输入不可靠，不叠加噪音
        doc = json.loads(manifest.read_text(encoding="utf-8"))
        role, ids = doc["sample_role"], doc["access_ids"]
        if role in LOCKBOX_ROLES:
            out.extend(_check_access_refs(path=manifest, role=role, ids=ids,
                                          window_id=doc.get("window_id"),
                                          ledger=ledger, offline=offline,
                                          where="campaign manifest"))
        elif role in OPEN_ROLES and ids:
            out.append(Finding(
                "error", str(manifest),
                f"sample_role={role} 但 access_ids 非空（诚实性：非锁箱声明不得挂访问登记）"))
    return out


def _clean_scalar(value: str) -> str:
    value = value.split("<!--", 1)[0].strip()
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def _front_matter(text: str) -> tuple[dict[str, str] | None, str | None]:
    """极简 front matter 解析（无 yaml 依赖）：返回 (键值, 错误)。

    None = 无 front matter（非档案，跳过）；错误 = 有 `---` 但未闭合。
    列表块状续行（`- id`）拼回上一键值（空值才吸收），交由 `_parse_list` 明确报格式错，
    不允许静默当空列表。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, None
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return None, "front matter 未闭合"
    fm: dict[str, str] = {}
    prev_key: str | None = None
    for raw in lines[1:end]:
        stripped = raw.split("<!--", 1)[0].strip()
        if not stripped or stripped.startswith("#"):
            continue
        if (stripped.startswith("-") and prev_key is not None
                and fm.get(prev_key, "").strip() == ""):
            fm[prev_key] = f"{fm[prev_key]} {stripped}".strip()
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        fm[key] = _clean_scalar(value)
        prev_key = key
    return fm, None


def _parse_list(value: str) -> tuple[list[str] | None, str | None]:
    """解析内联列表：`[]` / `[a, b]` → (list, None)；其它 → (None, 原因)。

    块状 YAML（`lockbox_access:` 后跟 `- id` 续行，已被 `_front_matter` 拼回）**显式报格式
    错误**——静默当空列表会绕过「非锁箱角色必须空 / 锁箱角色必须非空」的诚实性判据。
    """
    value = value.strip()
    if value.startswith("-"):
        return None, ("lockbox_access 块状列表（`- id` 续行）不支持："
                      "请用内联 `[]` 或 `[a, b]`")
    if value in ("", "[]"):
        return [], None
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return [], None
        return [item.strip().strip("\"'") for item in inner.split(",")], None
    return None, "lockbox_access 必须是列表（如 [] 或 [\"<access_id>\"]）"


def _check_dossier(path: Path, fm: dict[str, str], ledger: Ledger,
                   *, offline: bool) -> list[Finding]:
    out: list[Finding] = []
    missing = [key for key in DOSSIER_REQUIRED if key not in fm]
    if missing:
        return [Finding(
            "error", str(path),
            f"新/更新档案（{EFFECTIVE_DATE} 起）缺字段：{', '.join(missing)}")]
    role = fm["sample_role"]
    if role not in RT.SAMPLE_ROLES:
        return [Finding("error", str(path),
                        f"sample_role 非法：{role!r}（允许 {'/'.join(RT.SAMPLE_ROLES)}）")]
    ids, list_err = _parse_list(fm["lockbox_access"])
    if list_err:
        return [Finding("error", str(path), list_err)]
    if role in LOCKBOX_ROLES:
        out.extend(_check_access_refs(path=path, role=role, ids=ids,
                                      window_id=fm["window_id"],
                                      ledger=ledger, offline=offline, where="档案"))
    elif ids:
        out.append(Finding(
            "error", str(path),
            f"sample_role={role} 但 lockbox_access 非空（诚实性：非锁箱声明不得挂访问登记）"))
    return out


def check_dossiers(root: Path, ledger: Ledger, *, offline: bool) -> list[Finding]:
    out: list[Finding] = []
    base = root / DOSSIER_DIR
    if not base.is_dir():
        return out
    for path in sorted(base.rglob("*.md")):
        rel_parts = path.relative_to(base).parts
        if any(part.startswith("_") for part in rel_parts):
            continue  # `_template.md` 等元数据/模板
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            out.append(Finding("error", str(path), f"档案读取失败：{exc}"))
            continue
        fm, err = _front_matter(text)
        if err:
            out.append(Finding("error", str(path), err))
            continue
        if fm is None:
            continue  # 无 front matter（README 等）不是档案
        ts = fm.get("updated_ts")
        updated = None
        if ts:
            try:
                updated = dt.date.fromisoformat(ts)
            except ValueError:
                updated = None  # 无法解析视为新档（从严）
        if updated is not None and updated < EFFECTIVE_DATE:
            continue  # grandfather：生效日前档案不补声明
        out.extend(_check_dossier(path, fm, ledger, offline=offline))
    return out


def findings(root: Path, *, offline: bool = False) -> list[Finding]:
    ledger = Ledger(False, False, {}) if offline else load_ledger(root)
    fs = check_results(root, ledger, offline=offline) + check_dossiers(root, ledger,
                                                                       offline=offline)
    return sorted(fs, key=lambda f: (f.level != "error", f.path, f.message))


def _json_doc(root: Path, fs: list[Finding], *, offline: bool) -> dict:
    n_err = sum(1 for f in fs if f.level == "error")
    return {"root": str(root), "offline": offline, "errors": n_err,
            "warnings": len(fs) - n_err,
            "findings": [{"level": f.level, "path": f.path, "message": f.message}
                         for f in fs]}


def _manifest_doc(role: str, ids: list[str], window_id: str | None) -> dict:
    return {"campaign": "selftest", "window_id": window_id, "sample_role": role,
            "access_ids": list(ids), "platform_commit": "selftest"}


def _dossier_doc(role: str | None, window_id: str | None,
                 lockbox: str | None) -> str:
    lines = ["---", "xname: selftest", f"updated_ts: {EFFECTIVE_DATE.isoformat()}"]
    if role is not None:
        lines.append(f"sample_role: {role}")
    if window_id is not None:
        lines.append(f"window_id: {window_id}")
    if lockbox is not None:
        lines.append(f"lockbox_access: {lockbox}")
    lines += ["---", "", "# selftest"]
    return "\n".join(lines) + "\n"


def selftest() -> int:
    """负向自检：造假矩阵（在线/离线两口径）必抓，干净样本不误伤。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "qr"
        base = root / "results"
        base.mkdir(parents=True)
        docs = root / "dossiers" / "factors" / "fam"
        docs.mkdir(parents=True)

        # 真 tmp 台账：state + 1 final（F1）+ 1 exploration（E1）
        db = root / LEDGER_REL
        db.parent.mkdir(parents=True)
        conn = sqlite3.connect(db)
        conn.executescript(
            "CREATE TABLE lockbox_state (id INTEGER PRIMARY KEY, window_id TEXT NOT NULL,"
            " window_start TEXT NOT NULL, quota_final INTEGER NOT NULL DEFAULT 20,"
            " rolled_at TEXT NOT NULL);"
            "CREATE TABLE lockbox_access (access_id TEXT PRIMARY KEY,"
            " window_id TEXT NOT NULL, kind TEXT NOT NULL);")
        conn.execute("INSERT INTO lockbox_state VALUES (1, '2026Q2', '2025-07-01', 20,"
                     " '2026-09-21T00:00:00Z')")
        conn.execute("INSERT INTO lockbox_access VALUES ('F1', '2026Q2', 'final')")
        conn.execute("INSERT INTO lockbox_access VALUES ('E1', '2026Q2', 'exploration')")
        conn.commit()
        conn.close()

        def put_manifest(name: str, doc: dict) -> Path:
            path = base / name / "manifest.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(doc, ensure_ascii=False) + "\n", encoding="utf-8")
            return path

        def put_dossier(name: str, text: str) -> Path:
            path = docs / name
            path.write_text(text, encoding="utf-8")
            return path

        # 干净样本：is 空声明 + 真 final id 的 lockbox 声明
        clean_is_m = put_manifest("clean-is", _manifest_doc("is", [], None))
        clean_lb_m = put_manifest("clean-lb", _manifest_doc("lockbox", ["F1"], "2026Q2"))
        clean_is_d = put_dossier("clean-is.md", _dossier_doc("is", "", "[]"))
        clean_lb_d = put_dossier("clean-lb.md", _dossier_doc("lockbox", "2026Q2", '["F1"]'))

        bad_fields = _manifest_doc("legacy", [], None)
        del bad_fields["window_id"]
        fakes_online = {
            "manifest 缺字段": put_manifest("fake-missing-fields", bad_fields),
            "claim lockbox 但 access_ids 空": put_manifest(
                "fake-empty-ids", _manifest_doc("lockbox", [], "2026Q2")),
            "claim lockbox 幽灵 id": put_manifest(
                "fake-ghost-id", _manifest_doc("lockbox", ["ghost"], "2026Q2")),
            "探索冒充终评": put_manifest(
                "fake-explore-id", _manifest_doc("mixed", ["E1"], "2026Q2")),
            "is 挂访问登记": put_manifest(
                "fake-is-ids", _manifest_doc("is", ["F1"], "2026Q2")),
            "档案缺 sample_role": put_dossier(
                "fake-no-role.md", _dossier_doc(None, "2026Q2", "[]")),
            "档案 lockbox 幽灵 id": put_dossier(
                "fake-dossier-ghost.md", _dossier_doc("lockbox", "2026Q2", '["ghost"]')),
        }
        fakes_offline = {
            "manifest 缺字段", "claim lockbox 但 access_ids 空",
            "is 挂访问登记", "档案缺 sample_role",
        }

        online = findings(root, offline=False)
        offline = findings(root, offline=True)
        clean_paths = {str(p) for p in (clean_is_m, clean_lb_m, clean_is_d, clean_lb_d)}
        online_hit = {str(fakes_online[k]) for k in fakes_online}
        offline_hit = {str(fakes_online[k]) for k in fakes_offline}

        failed = []
        got_online = {f.path for f in online}
        got_offline = {f.path for f in offline}
        if got_online != online_hit:
            failed.append(f"在线：期望 {len(online_hit)} 造假命中，实得 {len(got_online)}"
                          f"（漏 {sorted(online_hit - got_online)}，误伤 "
                          f"{sorted(got_online - online_hit)}）")
        if got_online & clean_paths:
            failed.append(f"在线误伤干净样本：{sorted(got_online & clean_paths)}")
        if got_offline != offline_hit:
            failed.append(f"离线：期望 {len(offline_hit)} 格式造假命中，实得"
                          f" {len(got_offline)}（漏 {sorted(offline_hit - got_offline)}，"
                          f"误伤 {sorted(got_offline - offline_hit)}）")
        if got_offline & clean_paths:
            failed.append(f"离线误伤干净样本：{sorted(got_offline & clean_paths)}")

        if failed:
            print("  ✗ G-LOCKBOX 负向自检失败")
            for line in failed:
                print(f"      {line}")
            return 1
        print(f"  ✓ G-LOCKBOX 负向自检：在线 {len(fakes_online)} 类造假各命中"
              f"（缺字段/空 id/幽灵 id/探索冒充终评/诚实性/档案缺声明）、"
              f"{len(clean_paths)} 干净样本不误伤；离线 {len(fakes_offline)} 类格式造假命中")
        return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="G-LOCKBOX：样本声明与锁箱登记交叉核对（只报告）")
    ap.add_argument("--root", default=None)
    ap.add_argument("--offline", action="store_true",
                    help="仅格式校验（不读 <root>/data/ledger.sqlite）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    root = RT.resolve_root(args.root)
    if not root.is_dir():
        print(f"SKIP: root 不存在：{root}")
        return 0
    fs = findings(root, offline=args.offline)
    doc = _json_doc(root, fs, offline=args.offline)
    if args.json:
        print(json.dumps(doc, ensure_ascii=False, indent=2))
    else:
        for f in fs:
            print(f"[{f.level.upper()}] {f.path}: {f.message}")
        mode = "离线：仅格式校验，未读台账" if args.offline else "含台账交叉核对"
        print(f"root={root} errors={doc['errors']} warnings={doc['warnings']}（{mode}）")
    return 1 if doc["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
