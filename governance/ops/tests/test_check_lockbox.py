"""G-LOCKBOX 检查器单测（T10 + T12b 修复轮1 + R42 语义同步）：manifest / 档案 × 真 tmp 台账。

行为要求（T10 裁定 + T12b 修复轮1 门规则，R42 后不变）：
- 权威层 = campaign 级 `results/<dir>/manifest.json`（与 tidy 同层，不递归 run 子目录）：
  四键存在与类型（复用 research_tidy 判据）；
  - `sample_role ∈ {lockbox,mixed}` → `access_ids` 非空，每个 id 在
    `<root>/data/ledger.sqlite` 中 `kind='final'`，且**至少一条 id 的 window_id 与
    manifest 的 window_id 一致**（campaign 并集可含历史窗 id，不判违规）；
    台账不存在/无 state → error（`--offline` 跳过台账步）；
  - `sample_role ∈ {is,legacy,unknown}` → access_ids 可空；**非空不违规**（历史记录=如实
    披露），但引用亦须存在且 kind='final'；
- 档案 `dossiers/factors/**/*.md`：`updated_ts >= 2026-09-21`（或无 updated_ts）必须声明
  `sample_role/window_id/lockbox_access`；lockbox/mixed → lockbox_access 非空且每个 id
  在台账中为 final 且 window 一致；is/legacy/unknown → 空列表；更早档案 grandfather；
  无 front matter 与 `_` 前缀文件跳过。
- `--offline`：仅格式校验，不读台账；`--json`；root 不存在 → SKIP(0)；
  `--selftest` 造假矩阵必抓且干净样本不误伤。

R42：探索不再登记（store 拒收），`exploration` 行只作为**历史遗留只读行**存在——
用原始 SQL 直接写入模拟；检查器仍禁止其冒充 final 引用。

突变必杀：台账交叉核对/字段必填/窗口至少一匹配/引用真实性任一存根化 → 对应测试失败。
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pytest

import check_lockbox as CL

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "governance/ops/check_lockbox.py"
EFFECTIVE = "2026-09-21"
SNAP = "snapshot: 历史快照（验证数字不可复跑；R21 标注，见 ../README.md）"

from factorlab.adapters import lockbox_store as store  # noqa: E402
from factorlab.core.lockbox import LockboxWindow  # noqa: E402


def _manifest(role="is", ids=(), window=None, **over):
    doc = {"campaign": "c", "window_id": window, "sample_role": role,
           "config_path": "research/tools/xscore/pipeline/configs/quick.yaml",
           "access_ids": list(ids), "platform_commit": "abc1234"}
    doc.update(over)
    return doc


def _put_manifest(root: Path, name: str, doc) -> Path:
    p = root / "results" / name / "manifest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def _dossier_text(role=None, updated=EFFECTIVE, window=None, lockbox=None,
                  snapshot=True) -> str:
    lines = ["---", "xname: x"]
    if updated is not None:
        lines.append(f"updated_ts: {updated}")
    if snapshot:
        lines.append(SNAP)
    if role is not None:
        lines.append(f"sample_role: {role}")
    if window is not None:
        lines.append(f"window_id: {window}")
    if lockbox is not None:
        lines.append(f"lockbox_access: {lockbox}")
    lines += ["---", "", "# x"]
    return "\n".join(lines) + "\n"


def _put_dossier(root: Path, rel: str, text: str) -> Path:
    p = root / "dossiers" / "factors" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _ledger(root: Path, window_id="2026Q2", *, with_state=True):
    """真 tmp 台账（平台 store 写 schema/state/final 登记）；返回 (final_id, legacy_exp_id)。

    R42：store 拒收新 exploration 登记（历史行只读）；`exp` 用原始 SQL 直写一条
    `kind='exploration'` 遗留行，供"探索行冒充终评引用"的检查用例（禁删改触发器
    只挡 UPDATE/DELETE，INSERT 合法）。
    """
    db = root / "data" / "ledger.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = store.connect(db)
    final = exp = None
    try:
        if with_state:
            win = LockboxWindow(window_id, dt.date(2025, 7, 1), dt.date(2026, 7, 3))
            store.roll(conn, window=win)
            final = store.register_access(conn, kind="final", fingerprint="fp-final",
                                          artifact="a.yaml", params={}, command="cmd",
                                          reason="终评", window=win, tool="t")
            exp = f"LEGACY-EXP-{window_id}"
            conn.execute(
                "INSERT INTO lockbox_access (access_id, ts_utc, window_id,"
                " window_start, window_end, kind, fingerprint, artifact, params,"
                " command, result_ref, reason, actor, tool)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (exp, "2024-01-01T00:00:00+00:00", window_id, "2025-07-01",
                 "2026-07-03", "exploration", "fp-exp", "a.yaml", "{}", "cmd",
                 None, "历史探索（R42 前遗留）", "u@h", "test"))
    finally:
        conn.close()
    return final, exp


def _root(tmp_path, *, manifests=(), dossiers=(), ledger=True, with_state=True,
          window_id="2026Q2"):
    root = tmp_path / "qr"
    root.mkdir()
    final = exp = None
    if ledger:
        final, exp = _ledger(root, window_id, with_state=with_state)
    for name, doc in manifests:
        _put_manifest(root, name, doc)
    for rel, text in dossiers:
        _put_dossier(root, rel, text)
    return root, final, exp


def _names(fs):
    return {Path(f.path).name for f in fs}


# ── campaign manifest ────────────────────────────────────────────────

def test_clean_root_green_with_real_final_ids(tmp_path):
    root, final, _ = _root(tmp_path)
    _put_manifest(root, "is-camp", _manifest())
    _put_manifest(root, "lb-camp", _manifest("lockbox", [final], "2026Q2"))
    _put_dossier(root, "fam/is.md", _dossier_text(role="is", window="", lockbox="[]"))
    _put_dossier(root, "fam/lb.md",
                 _dossier_text(role="lockbox", window="2026Q2", lockbox=f'["{final}"]'))
    assert CL.findings(root) == []
    assert CL.main(["--root", str(root)]) == 0


def test_manifest_missing_key_is_error(tmp_path):
    doc = _manifest()
    del doc["window_id"]
    root, _, _ = _root(tmp_path, manifests=[("bad", doc)])
    fs = CL.findings(root)
    assert len(fs) == 1
    assert Path(fs[0].path) == root / "results" / "bad" / "manifest.json"
    assert "window_id" in fs[0].message
    assert CL.main(["--root", str(root)]) == 1


def test_manifest_field_types_reused_from_tidy(tmp_path):
    root, _, _ = _root(tmp_path, manifests=[("bad", _manifest("is", [], platform_commit=""))])
    fs = CL.findings(root)
    assert len(fs) == 1
    assert "platform_commit" in fs[0].message


def test_lockbox_claim_empty_access_ids_is_error(tmp_path):
    root, _, _ = _root(tmp_path, manifests=[("bad", _manifest("lockbox", [], "2026Q2"))])
    fs = CL.findings(root)
    assert len(fs) == 1
    assert "access_ids" in fs[0].message
    assert "lockbox" in fs[0].message


def test_lockbox_claim_unknown_access_id_is_error(tmp_path):
    root, _, _ = _root(tmp_path, manifests=[("bad", _manifest("lockbox", ["ghost"], "2026Q2"))])
    fs = CL.findings(root)
    assert len(fs) == 1
    assert "ghost" in fs[0].message
    assert "台账" in fs[0].message


def test_legacy_exploration_id_masquerading_as_final_is_error(tmp_path):
    """R42：探索不再登记，但历史 exploration 行仍不得冒充终评引用。"""
    root, _, exp = _root(tmp_path)
    _put_manifest(root, "bad", _manifest("mixed", [exp], "2026Q2"))
    fs = CL.findings(root)
    assert len(fs) == 1
    assert exp in fs[0].message
    assert "exploration" in fs[0].message


def test_window_mismatch_is_error(tmp_path):
    root, final, _ = _root(tmp_path)
    _put_manifest(root, "bad", _manifest("lockbox", [final], "2026Q1"))
    fs = CL.findings(root)
    assert len(fs) == 1
    assert "2026Q1" in fs[0].message and "2026Q2" in fs[0].message


def test_lockbox_claim_empty_window_id_is_error(tmp_path):
    root, final, _ = _root(tmp_path)
    _put_manifest(root, "bad", _manifest("lockbox", [final], None))
    fs = CL.findings(root)
    assert len(fs) == 1
    assert "window_id" in fs[0].message


def _add_final(root: Path, window_id: str, start: dt.date) -> str:
    """在真 tmp 台账补登记一个历史窗口 final（与 state 窗口不同）。"""
    db = root / "data" / "ledger.sqlite"
    conn = store.connect(db)
    try:
        win = LockboxWindow(window_id, start, dt.date(2026, 7, 3))
        return store.register_access(conn, kind="final", fingerprint=f"fp-{window_id}",
                                     artifact="a.yaml", params={}, command="cmd",
                                     reason="历史窗", window=win, tool="t")
    finally:
        conn.close()


@pytest.mark.parametrize("role", ["is", "legacy", "unknown"])
def test_open_role_with_historical_final_ids_passes(tmp_path, role):
    """历史记录=如实披露：非锁箱角色挂真实 final id（窗口不一致）不再违规。"""
    root, _, _ = _root(tmp_path)
    old = _add_final(root, "2026Q1", dt.date(2024, 7, 1))
    _put_manifest(root, "ok", _manifest(role, [old], "2026Q2"))
    assert CL.findings(root) == []
    assert CL.main(["--root", str(root)]) == 0


def test_open_role_with_ghost_id_is_error(tmp_path):
    root, _, _ = _root(tmp_path)
    _put_manifest(root, "bad", _manifest("unknown", ["ghost"], None))
    fs = CL.findings(root)
    assert len(fs) == 1
    assert "ghost" in fs[0].message


def test_open_role_with_legacy_exploration_id_is_error(tmp_path):
    """非锁箱角色可挂历史真实 final id；历史 exploration 行仍非法。"""
    root, _, exp = _root(tmp_path)
    _put_manifest(root, "bad", _manifest("is", [exp], "2026Q2"))
    fs = CL.findings(root)
    assert len(fs) == 1
    assert "exploration" in fs[0].message


def test_lockbox_only_old_window_id_is_error(tmp_path):
    """campaign 并集只含旧窗 id、无本次窗 → 无法证明本次锁箱窗口，报错。"""
    root, _, _ = _root(tmp_path)
    old = _add_final(root, "2026Q1", dt.date(2024, 7, 1))
    _put_manifest(root, "bad", _manifest("lockbox", [old], "2026Q2"))
    fs = CL.findings(root)
    assert len(fs) == 1
    assert "2026Q2" in fs[0].message and "2026Q1" in fs[0].message


def test_lockbox_union_old_plus_current_id_passes(tmp_path):
    """campaign 并集 = 旧窗 id ∪ 本次窗 id → 至少一条匹配即通过。"""
    root, final, _ = _root(tmp_path)
    old = _add_final(root, "2026Q1", dt.date(2024, 7, 1))
    _put_manifest(root, "ok", _manifest("lockbox", [old, final], "2026Q2"))
    assert CL.findings(root) == []


def test_lockbox_claim_without_ledger_is_error(tmp_path):
    root, _, _ = _root(tmp_path, manifests=[("bad", _manifest("lockbox", ["F1"], "2026Q2"))],
                       ledger=False)
    fs = CL.findings(root)
    assert len(fs) == 1
    assert "台账" in fs[0].message


def test_lockbox_claim_with_ledger_without_state_is_error(tmp_path):
    root, _, _ = _root(tmp_path, manifests=[("bad", _manifest("lockbox", ["F1"], "2026Q2"))],
                       with_state=False)
    fs = CL.findings(root)
    assert len(fs) == 1
    assert "state" in fs[0].message


def test_run_subdir_manifest_not_scanned(tmp_path):
    root, _, _ = _root(tmp_path, manifests=[("camp", _manifest())])
    _put_manifest(root, "camp/run1", _manifest("lockbox", ["ghost"], "2026Q2"))
    assert CL.findings(root) == []


# ── --offline ────────────────────────────────────────────────────────

def test_offline_skips_ledger_crosscheck(tmp_path):
    root, _, _ = _root(tmp_path, manifests=[
        ("ghost", _manifest("lockbox", ["ghost"], "2026Q2")),
        ("empty", _manifest("lockbox", [], "2026Q2")),
    ], ledger=False)
    fs = CL.findings(root, offline=True)
    assert len(fs) == 1
    assert Path(fs[0].path) == root / "results" / "empty" / "manifest.json"


def test_offline_does_not_read_ledger_file(tmp_path):
    root, _, _ = _root(tmp_path, manifests=[("ghost", _manifest("lockbox", ["ghost"], "2026Q2"))])
    (root / "data" / "ledger.sqlite").write_text("not a database", encoding="utf-8")
    assert CL.findings(root, offline=True) == []
    assert CL.findings(root, offline=False) != []


# ── 档案 ─────────────────────────────────────────────────────────────

def test_recent_dossier_missing_sample_role_is_error(tmp_path):
    root, _, _ = _root(tmp_path, dossiers=[
        ("fam/a.md", _dossier_text(role=None, window="2026Q2", lockbox="[]"))])
    fs = CL.findings(root)
    assert len(fs) == 1
    assert fs[0].path.endswith("fam/a.md")
    assert "sample_role" in fs[0].message


def test_recent_dossier_missing_all_fields_is_error(tmp_path):
    root, _, _ = _root(tmp_path, dossiers=[
        ("fam/a.md", _dossier_text(role=None, window=None, lockbox=None))])
    fs = CL.findings(root)
    assert len(fs) == 1
    for key in ("sample_role", "window_id", "lockbox_access"):
        assert key in fs[0].message


def test_dossier_without_updated_ts_counts_as_recent(tmp_path):
    root, _, _ = _root(tmp_path, dossiers=[
        ("fam/a.md", _dossier_text(updated=None, role=None, window=None, lockbox=None))])
    fs = CL.findings(root)
    assert len(fs) == 1
    assert "sample_role" in fs[0].message


def test_old_dossier_grandfathered(tmp_path):
    root, _, _ = _root(tmp_path, dossiers=[
        ("fam/old.md", _dossier_text(updated="2026-09-18", role=None,
                                     window=None, lockbox=None))])
    assert CL.findings(root) == []


def test_template_and_non_frontmatter_skipped(tmp_path):
    root, _, _ = _root(tmp_path, dossiers=[
        ("_template.md",
         "---\nxname: <x>\nupdated_ts: <YYYY-MM-DD>\nsample_role: <is|mixed|lockbox>\n"
         "window_id: <YYYYQn>\nlockbox_access: []\n---\n"),
        ("README.md", "# 说明（无 front matter）\n"),
    ])
    assert CL.findings(root) == []


def test_dossier_invalid_sample_role_is_error(tmp_path):
    root, _, _ = _root(tmp_path, dossiers=[
        ("fam/a.md", _dossier_text(role="in-sample", window="2026Q2", lockbox="[]"))])
    fs = CL.findings(root)
    assert len(fs) == 1
    assert "sample_role" in fs[0].message


def test_dossier_is_with_nonempty_lockbox_access_is_error(tmp_path):
    root, final, _ = _root(tmp_path)
    _put_dossier(root, "fam/a.md",
                 _dossier_text(role="is", window="2026Q2", lockbox=f'["{final}"]'))
    fs = CL.findings(root)
    assert len(fs) == 1
    assert "诚实" in fs[0].message


def test_dossier_lockbox_empty_or_ghost_id_is_error(tmp_path):
    root, _, _ = _root(tmp_path, dossiers=[
        ("fam/empty.md", _dossier_text(role="lockbox", window="2026Q2", lockbox="[]")),
        ("fam/ghost.md", _dossier_text(role="mixed", window="2026Q2", lockbox='["ghost"]')),
    ])
    fs = CL.findings(root)
    assert _names(fs) == {"empty.md", "ghost.md"}
    assert any("access_ids" in f.message for f in fs)
    assert any("ghost" in f.message for f in fs)


def test_dossier_lockbox_legacy_exploration_id_is_error(tmp_path):
    root, _, exp = _root(tmp_path)
    _put_dossier(root, "fam/a.md",
                 _dossier_text(role="lockbox", window="2026Q2", lockbox=f'["{exp}"]'))
    fs = CL.findings(root)
    assert len(fs) == 1
    assert "exploration" in fs[0].message


def test_dossier_offline_skips_ledger_but_keeps_format(tmp_path):
    root, _, _ = _root(tmp_path, dossiers=[
        ("fam/ghost.md", _dossier_text(role="lockbox", window="2026Q2", lockbox='["ghost"]')),
        ("fam/norole.md", _dossier_text(role=None, window=None, lockbox=None)),
    ], ledger=False)
    fs = CL.findings(root, offline=True)
    assert len(fs) == 1
    assert Path(fs[0].path).name == "norole.md"


def test_dossier_block_list_is_format_error_online(tmp_path):
    """块状 YAML 列表（`- id` 续行）不得被静默当 []——否则绕过锁箱角色非空/匹配判据。"""
    root, _, _ = _root(tmp_path)
    _put_dossier(root, "fam/block.md",
                 "---\nxname: x\nupdated_ts: 2026-09-21\nsample_role: is\n"
                 "window_id: 2026Q2\nlockbox_access:\n  - acc1\n---\n\n# x\n")
    fs = CL.findings(root)
    assert len(fs) == 1
    assert "块状" in fs[0].message
    assert "lockbox_access" in fs[0].message
    assert CL.main(["--root", str(root)]) == 1


def test_dossier_block_list_is_format_error_offline(tmp_path):
    root, _, _ = _root(tmp_path, ledger=False)
    _put_dossier(root, "fam/block.md",
                 "---\nxname: x\nupdated_ts: 2026-09-21\nsample_role: lockbox\n"
                 "window_id: 2026Q2\nlockbox_access:\n  - ghost\n---\n\n# x\n")
    fs = CL.findings(root, offline=True)
    assert len(fs) == 1
    assert "块状" in fs[0].message
    assert Path(fs[0].path).name == "block.md"


# ── CLI / selftest / 只读 ────────────────────────────────────────────

def test_json_output_shape_and_exit(tmp_path, capsys):
    root, _, _ = _root(tmp_path, manifests=[
        ("good", _manifest()),
        ("bad", _manifest("lockbox", [], "2026Q2")),
    ])
    rc = CL.main(["--root", str(root), "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert doc["root"] == str(root)
    assert doc["errors"] == 1
    assert [Path(f["path"]).parent.name for f in doc["findings"]] == ["bad"]
    assert "access_ids" in doc["findings"][0]["message"]


def test_plain_output_summary(tmp_path, capsys):
    root, _, _ = _root(tmp_path, manifests=[("good", _manifest())])
    rc = CL.main(["--root", str(root)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "errors=0" in out


def test_missing_root_skips(tmp_path, capsys):
    rc = CL.main(["--root", str(tmp_path / "absent")])
    assert rc == 0
    assert "SKIP" in capsys.readouterr().out


def test_root_env_used_when_flag_absent(tmp_path, monkeypatch, capsys):
    root, _, _ = _root(tmp_path, manifests=[("bad", _manifest("lockbox", [], "2026Q2"))])
    monkeypatch.setenv("QUANTRESEARCH_ROOT", str(root))
    assert CL.main([]) == 1
    assert str(root) in capsys.readouterr().out


def test_selftest_api_and_cli():
    assert CL.selftest() == 0
    r = subprocess.run([sys.executable, str(SCRIPT), "--selftest"],
                       capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "✓" in r.stdout


def test_selftest_fails_when_checker_is_blind(monkeypatch, capsys):
    monkeypatch.setattr(CL, "findings", lambda root, offline=False: [])
    assert CL.selftest() == 1, "自检必须能识别『恒空』的失效检查器"
    assert "✗" in capsys.readouterr().out


def test_selftest_fails_when_checker_overflags(monkeypatch, capsys):
    monkeypatch.setattr(CL, "findings", lambda root, offline=False: [
        CL.Finding("error", str(root / "zzz"), "boom")])
    assert CL.selftest() == 1, "自检必须能识别『全红』的失效检查器"
    assert "✗" in capsys.readouterr().out


def test_checker_does_not_modify_tree(tmp_path):
    root, _, _ = _root(tmp_path,
                       manifests=[("bad", _manifest("lockbox", [], "2026Q2"))],
                       dossiers=[("fam/a.md", _dossier_text(role="is", window="", lockbox="[]"))])

    def fingerprint():
        # SQLite 读取 WAL 库会生成 -shm/-wal 读侧车（可再生缓存，不改台账内容）——
        # 指纹排除之；台账文件本身逐字节不变才是判据。
        return sorted(str(p.relative_to(root)) + ":" + str(p.stat().st_size)
                      for p in root.rglob("*")
                      if not p.name.endswith(("-shm", "-wal")))

    ledger = root / "data" / "ledger.sqlite"
    before = fingerprint()
    ledger_before = (ledger.stat().st_size, ledger.read_bytes())
    CL.main(["--root", str(root)])
    assert fingerprint() == before
    assert (ledger.stat().st_size, ledger.read_bytes()) == ledger_before
