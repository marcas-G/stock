"""cli.py / 补链 / Makefile / 定时器脚本 行为测试（离线：fake listdir/transport/runner）。

需求源：.superpowers/sdd/2026-09-16-pan-data-update-plan/task-9-brief.md
设计：knowledge/design/workspace/2026-09-16-pan-data-update-design.md §3/§4/§5/§8
"""
import json
import os
import resource
import subprocess
from pathlib import Path

import pytest

from pan_update import cli, config, parse_fundamentals_xlsx as fp
from pan_update import share, stages, sync
from pan_update import transfer as xfer

ROOT = config.repo_root()
VENV = ROOT / "platform" / ".venv" / "bin" / "python"
RECONCILE = ROOT / "platform" / "tools" / "ch_ingest" / "reconcile.py"

FIXTURE = Path(__file__).parent / "fixtures" / "fin_sample.xlsx"


# ---------------------------------------------------------------
# 测试替身（只替外部面：分享树/传输/子进程；sync/stages/cli 均真跑）
# ---------------------------------------------------------------

def _tree(files=None):
    """root_fid=0 → level2_detail → 四类别目录，各含 1 文件（默认 <cat>.bin, size=10）。"""
    files = files or {cat: [f"{cat}.bin"] for cat in config.CATEGORIES}
    tree = {"0": [share.Entry(config.SHARE_ROOT_DIR, 0, "L2", "tok",
                              config.SHARE_ROOT_DIR, True)]}
    l2 = []
    for cat, cfg in config.CATEGORIES.items():
        fid = f"cat:{cat}"
        l2.append(share.Entry(cfg.share_dir, 0, fid, "tok", cfg.share_dir, True))
        names = files.get(cat, [])
        if isinstance(names, str):
            names = [names]
        tree[fid] = [share.Entry(n, 10, f"fid:{cat}:{n}", "tok", n, False)
                     for n in names]
    tree["L2"] = l2
    return tree


class FakeListdir:
    def __init__(self, tree, events=None):
        self.tree = tree
        self.calls = []
        self.events = events if events is not None else []

    def __call__(self, fid):
        self.calls.append(fid)
        self.events.append(("listdir", fid))
        if fid not in self.tree:
            raise KeyError(f"fake listdir 无此 fid: {fid}")
        return list(self.tree[fid])


class FakeTransport:
    """offline 传输：name ∈ block → manual；name ∈ url_fail → failed；否则可下。"""

    def __init__(self, block=(), url_fail=(), dl_fail=(), events=None):
        self.block = set(block)
        self.url_fail = set(url_fail)
        self.dl_fail = set(dl_fail)
        self.dl = []
        self.url_calls = 0
        self.events = events if events is not None else []

    def list_urls(self, items):
        self.url_calls += 1
        out = {}
        for i in items:
            if i["name"] in self.block:
                i["_blocked_reason"] = "size limit"
            elif i["name"] in self.url_fail:
                i["_fetch_error"] = "HTTP 500 boom"
            else:
                out[i["fid"]] = f"http://fake/{i['fid']}"
        return out

    def download(self, url, out, size):
        name = Path(out).name.removesuffix(".part")
        self.events.append(("download", name))
        self.dl.append(str(out))
        if name in self.dl_fail:
            raise OSError("boom")
        out.write_bytes(b"x" * size)
        return True


class FakeRunner:
    def __init__(self, fail_on=None, events=None):
        self.calls = []
        self.envs = []
        self.fail_on = fail_on
        self.events = events if events is not None else []

    def __call__(self, cmd, *, log, env=None):
        self.calls.append(list(cmd))
        self.envs.append(dict(env or {}))
        self.events.append(("runner", cmd[1]))
        log(f"fake-run {Path(cmd[1]).name}")
        if self.fail_on is not None and Path(cmd[1]).name == self.fail_on:
            raise stages.StageError(Path(cmd[1]).stem, list(cmd), 3, "boom")


class FakeTransferClient:
    """离线转存客户端：可脚本化 available/fail，记录 fetch 调用。"""

    def __init__(self, *, available=True, fail=None):
        self._available = available
        self.fail = fail
        self.calls = []

    def available(self):
        return self._available

    def fetch(self, item, dest):
        self.calls.append((item["name"], str(dest)))
        if self.fail is not None:
            raise self.fail
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"T")


@pytest.fixture
def cookie_ok(monkeypatch):
    monkeypatch.setattr(cli.quark_client, "cookies", lambda: "cookie=1")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("FACTORLAB_MAX_MEMORY", "FACTORLAB_MIN_AVAILABLE_MEMORY",
              "FACTORLAB_DATA_BACKEND", "MALLOC_ARENA_MAX"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture(autouse=True)
def _cookie_env_guard():
    """CLI 会用 os.environ 直接接线 QUARK_COOKIE_FILE（非 monkeypatch）→ 逐测试存取复原。"""
    saved_env = os.environ.get("QUARK_COOKIE_FILE")
    saved_path = cli.quark_client.COOKIE_PATH
    yield
    if saved_env is None:
        os.environ.pop("QUARK_COOKIE_FILE", None)
    else:
        os.environ["QUARK_COOKIE_FILE"] = saved_env
    cli.quark_client.COOKIE_PATH = saved_path


def _run(argv, tmp_path, *, tree=None, transport=None, runner=None,
         verify_runner=None, categories=None, raw_root=None, events=None,
         transfer_client=None):
    events = events if events is not None else []
    argv = list(argv)
    if categories is not None:
        argv += ["--categories", categories]
    state_path = tmp_path / "pan_state.json"
    lock_path = tmp_path / "pan_update.lock"
    if transfer_client is None:
        transfer_client = FakeTransferClient(available=False)
    return cli.main(
        argv,
        listdir=FakeListdir(tree if tree is not None else _tree(), events),
        transport=transport if transport is not None else FakeTransport(events=events),
        runner=runner if runner is not None else FakeRunner(events=events),
        verify_runner=verify_runner or (lambda cmd: 0),
        state_path=state_path,
        lock_path=lock_path,
        log_dir=tmp_path / "logs",
        raw_root=raw_root if raw_root is not None else tmp_path / "raw",
        transfer_client=transfer_client,
    )


# ---------------------------------------------------------------
# 补链：STAGE_CHAINS 四类别齐全、每步脚本存在、顺序正确
# ---------------------------------------------------------------

def test_stage_chains_cover_all_categories_and_scripts_exist():
    assert set(stages.STAGE_CHAINS) == set(config.CATEGORIES)
    fund_flow = [Path(c[1]).relative_to(ROOT).as_posix()
                 for c in stages.STAGE_CHAINS["fund_flow"]]
    assert fund_flow == ["platform/tools/ch_ingest/ingest_moneyflow.py"]
    financials = [Path(c[1]).name for c in stages.STAGE_CHAINS["financials"]]
    assert financials == ["parse_fundamentals_xlsx.py", "ingest_fundamentals.py"]
    for cat, chain in stages.STAGE_CHAINS.items():
        assert chain, f"{cat} 链为空"
        for cmd in chain:
            assert cmd[0] == str(VENV), f"{cat}: 必须平台 venv 解释器"
            assert Path(cmd[0]).is_file()
            assert Path(cmd[1]).is_file(), f"{cat}: 脚本不存在 {cmd[1]}"


def test_financials_chain_orders_parse_before_ingest():
    names = [Path(c[1]).name for c in stages.STAGE_CHAINS["financials"]]
    assert names.index("parse_fundamentals_xlsx.py") < names.index("ingest_fundamentals.py")


# ---------------------------------------------------------------
# sync：--dry-run 列清单不下载不写盘；未知类别 exit 2；cookie 缺失 exit 2
# ---------------------------------------------------------------

def test_dry_run_lists_every_category_and_writes_nothing(tmp_path, capsys, cookie_ok):
    tp = FakeTransport()
    rc = _run(["sync", "--dry-run"], tmp_path, transport=tp)
    assert rc == 0
    out = capsys.readouterr().out
    for cat in config.CATEGORIES:
        assert f"[{cat}]" in out, f"缺类别清单：{cat}"
    assert "每日沪深京" not in out  # 不打印 share_dir 噪声（只类别键）
    assert "daily.bin" in out and "fetch" in out
    assert tp.url_calls == 0 and tp.dl == []
    assert not (tmp_path / "pan_state.json").exists()
    assert not (tmp_path / "pan_update.lock").exists()


def test_find_dir_wiring_uses_share_root_then_category_dir(tmp_path, capsys, cookie_ok):
    fl = FakeListdir(_tree())
    rc = cli.main(["sync", "--dry-run"], listdir=fl, transport=FakeTransport(),
                  state_path=tmp_path / "s.json", lock_path=tmp_path / "l.lock",
                  log_dir=tmp_path / "logs", raw_root=tmp_path / "raw")
    assert rc == 0
    assert fl.calls[0] == "0", "先取分享根 fid=0"
    assert fl.calls.count("L2") == len(config.CATEGORIES), "每类别在 level2_detail 下找目录"
    for cat in config.CATEGORIES:
        assert f"cat:{cat}" in fl.calls, "按 share_dir 找到类别 fid 并遍历"


def test_default_listdir_is_share_default(monkeypatch, tmp_path, capsys, cookie_ok):
    tree = _tree()
    calls = []

    def fake_default(fid):
        calls.append(fid)
        return list(tree[fid])

    monkeypatch.setattr(share, "_default_listdir", fake_default)
    rc = cli.main(["sync", "--dry-run", "--categories", "daily"],
                  transport=FakeTransport(), state_path=tmp_path / "s.json",
                  lock_path=tmp_path / "l.lock", log_dir=tmp_path / "logs",
                  raw_root=tmp_path / "raw")
    assert rc == 0
    assert calls and calls[0] == "0", "缺省必须走生产 listdir（_default_listdir）"


def test_unknown_category_exits_2(tmp_path, capsys, cookie_ok):
    rc = _run(["sync", "--categories", "bogus"], tmp_path)
    assert rc == 2
    err = capsys.readouterr().err
    assert "bogus" in err
    assert "daily" in err and "financials" in err


def test_cookie_missing_exits_2_with_hint(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli.config, "COOKIE_PATH", tmp_path / "nope-root.txt")
    monkeypatch.setattr(cli.quark_client, "COOKIE_PATH", str(tmp_path / "nope.txt"))
    monkeypatch.setattr(cli.quark_client, "_FALLBACK_COOKIE", str(tmp_path / "nope2.txt"))
    monkeypatch.setattr(cli.quark_client, "_REPO_FALLBACK_COOKIE",
                        str(tmp_path / "nope3.txt"))
    rc = _run(["sync"], tmp_path)
    assert rc == 2
    err = capsys.readouterr().err
    assert "quark_cookies.txt" in err
    assert str(cli.config.COOKIE_PATH) in err


def test_cli_wires_repo_root_cookie_when_env_unset(tmp_path, monkeypatch):
    """P1：仓根文件在 + env 未设 + /tmp/tool 位缺失 → CLI 启动接线 QUARK_COOKIE_FILE。"""
    cookie = tmp_path / "root" / "quark_cookies.txt"
    cookie.parent.mkdir(parents=True)
    cookie.write_text("root=1", encoding="utf-8")
    monkeypatch.setattr(cli.config, "COOKIE_PATH", cookie)
    monkeypatch.setattr(cli.quark_client, "COOKIE_PATH", str(tmp_path / "missing1.txt"))
    monkeypatch.setattr(cli.quark_client, "_FALLBACK_COOKIE", str(tmp_path / "missing2.txt"))
    monkeypatch.setattr(cli.quark_client, "_REPO_FALLBACK_COOKIE",
                        str(tmp_path / "missing3.txt"))
    assert "QUARK_COOKIE_FILE" not in os.environ
    rc = _run(["sync", "--dry-run", "--categories", "daily"], tmp_path)
    assert rc == 0, "接线后 cookie 检查必须通过"
    assert os.environ["QUARK_COOKIE_FILE"] == str(cookie)
    assert cli.quark_client.COOKIE_PATH == str(cookie)


def test_cli_keeps_explicit_cookie_env(tmp_path, monkeypatch):
    """P1 边界：已显式设 QUARK_COOKIE_FILE → 不得被仓根接线覆盖。"""
    cookie = tmp_path / "root" / "quark_cookies.txt"
    cookie.parent.mkdir(parents=True)
    cookie.write_text("root=1", encoding="utf-8")
    explicit = tmp_path / "explicit.txt"
    explicit.write_text("explicit=1", encoding="utf-8")
    monkeypatch.setattr(cli.config, "COOKIE_PATH", cookie)
    monkeypatch.setattr(cli.quark_client, "COOKIE_PATH", str(explicit))
    monkeypatch.setenv("QUARK_COOKIE_FILE", str(explicit))
    assert _run(["sync", "--dry-run", "--categories", "daily"], tmp_path) == 0
    assert os.environ["QUARK_COOKIE_FILE"] == str(explicit)
    assert cli.quark_client.COOKIE_PATH == str(explicit), "显式 env 不得被仓根接线覆盖"


def test_cli_uses_repo_fallback_without_wiring(tmp_path, monkeypatch):
    """P1 备选口径：接线不生效（仓根路径被指走）时，cookies() 链的仓根回退仍兜底。"""
    cookie = tmp_path / "repo.txt"
    cookie.write_text("repo=1", encoding="utf-8")
    monkeypatch.setattr(cli.config, "COOKIE_PATH", tmp_path / "absent-root.txt")
    monkeypatch.setattr(cli.quark_client, "COOKIE_PATH", str(tmp_path / "missing1.txt"))
    monkeypatch.setattr(cli.quark_client, "_FALLBACK_COOKIE", str(tmp_path / "missing2.txt"))
    monkeypatch.setattr(cli.quark_client, "_REPO_FALLBACK_COOKIE", str(cookie))
    assert _run(["sync", "--dry-run", "--categories", "daily"], tmp_path) == 0


def test_empty_cookie_treated_as_missing(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli.quark_client, "cookies", lambda: "   ")
    rc = _run(["sync"], tmp_path)
    assert rc == 2
    assert "quark_cookies.txt" in capsys.readouterr().err


# ---------------------------------------------------------------
# sync 落盘：state_path 固定 data/raw/pan_state.json，files 记账
# ---------------------------------------------------------------

def test_default_state_path_is_design_path():
    assert cli.STATE_PATH == ROOT / "data" / "raw" / "pan_state.json"


def test_sync_persists_state_with_files(tmp_path, capsys, cookie_ok):
    tp = FakeTransport()
    rc = _run(["sync", "--categories", "daily,financials"], tmp_path, transport=tp)
    assert rc == 0
    data = json.loads((tmp_path / "pan_state.json").read_text(encoding="utf-8"))
    assert data["files"]["daily/daily.bin"]["size"] == 10
    assert data["files"]["financials/financials.bin"]["size"] == 10
    assert data["runs"] and data["runs"][-1]["status"] == "ok"
    assert (tmp_path / "raw" / "daily" / "daily.bin").read_bytes() == b"x" * 10


def test_sync_passes_state_path_to_sync_category(tmp_path, monkeypatch, cookie_ok):
    seen = {}
    real = sync.sync_category

    def spy(state, category, **kw):
        seen[category] = kw
        return real(state, category, **kw)

    monkeypatch.setattr(sync, "sync_category", spy)
    rc = _run(["sync", "--categories", "daily"], tmp_path)
    assert rc == 0
    assert seen["daily"]["state_path"] == tmp_path / "pan_state.json"
    assert seen["daily"]["dry_run"] is False


# ---------------------------------------------------------------
# freshness/闩锁：新文件清全部标记；无新增阶段跳过且命令未调
# ---------------------------------------------------------------

def test_new_files_clear_all_stage_marks(tmp_path, capsys, cookie_ok):
    state_path = tmp_path / "pan_state.json"
    from pan_update import state as st
    st.save_state_atomic(state_path, {
        "version": 1, "files": {}, "runs": [],
        "stages": {"daily": {"build": "t0", "publish": "t0", "verify": "t0"}},
    })
    rc = cli.main(["sync", "--categories", "daily"], listdir=FakeListdir(_tree()),
                  transport=FakeTransport(), state_path=state_path,
                  lock_path=tmp_path / "l.lock", log_dir=tmp_path / "logs",
                  raw_root=tmp_path / "raw")
    assert rc == 0
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["stages"]["daily"] == {}, "有新文件 → 清全部阶段标记（build/publish 重跑）"
    assert data["files"]["daily/daily.bin"]


def test_no_new_files_keeps_marks_and_skips_commands(tmp_path, capsys, cookie_ok):
    runner = FakeRunner()
    rc = _run(["sync", "--categories", "daily"], tmp_path, runner=runner)
    assert rc == 0
    rc = _run(["build", "--categories", "daily"], tmp_path, runner=runner)
    assert rc == 0 and len(runner.calls) == 4, "首跑应执行 daily 四步链"
    runner.calls.clear()
    tp2 = FakeTransport()
    rc = cli.main(["sync", "--categories", "daily"], listdir=FakeListdir(_tree()),
                  transport=tp2, state_path=tmp_path / "pan_state.json",
                  lock_path=tmp_path / "l.lock", log_dir=tmp_path / "logs",
                  raw_root=tmp_path / "raw")
    assert rc == 0
    assert tp2.url_calls == 0, "无新增 → 不取链"
    data = json.loads((tmp_path / "pan_state.json").read_text(encoding="utf-8"))
    assert data["stages"]["daily"]["build"], "无新增不清标记"
    runner.calls.clear()
    rc = _run(["build", "--categories", "daily"], tmp_path, runner=runner)
    assert rc == 0
    assert runner.calls == [], "阶段已标记 → 命令不得再调（幂等跳过）"


def test_adopted_local_file_clears_stage_marks(tmp_path, capsys, cookie_ok):
    """P2a：人工放入的 manual 大件（分享清单存在 + size 匹配）→ adopted 记账 +
    视为新数据清阶段标记；不取链、不下载。"""
    state_path = tmp_path / "pan_state.json"
    from pan_update import state as st
    st.save_state_atomic(state_path, {
        "version": 1, "files": {}, "runs": [],
        "stages": {"daily": {"build": "t0", "publish": "t0"}},
    })
    raw = tmp_path / "raw" / "daily"
    raw.mkdir(parents=True)
    (raw / "daily.bin").write_bytes(b"y" * 10)  # 与分享清单 size=10 匹配
    tp = FakeTransport()
    rc = cli.main(["sync", "--categories", "daily"], listdir=FakeListdir(_tree()),
                  transport=tp, state_path=state_path,
                  lock_path=tmp_path / "l.lock", log_dir=tmp_path / "logs",
                  raw_root=tmp_path / "raw")
    assert rc == 0
    out = capsys.readouterr().out
    assert "adopted=1" in out and "daily.bin" in out
    data = json.loads(state_path.read_text(encoding="utf-8"))
    entry = data["files"]["daily/daily.bin"]
    assert entry["adopted"] is True and entry["synced_at"]
    assert data["stages"]["daily"] == {}, "adopted 视为新数据 → 清阶段标记"
    assert tp.url_calls == 0 and tp.dl == [], "已就位文件不得再取链/下载"


def test_size_mismatch_blocks_adoption_and_freshness(tmp_path, capsys, cookie_ok):
    """P2a 边界：本地 size 不符 → 不 adopted；照常走下载失败路径（不清阶段标记）。"""
    state_path = tmp_path / "pan_state.json"
    from pan_update import state as st
    st.save_state_atomic(state_path, {
        "version": 1, "files": {}, "runs": [],
        "stages": {"daily": {"build": "t0"}},
    })
    raw = tmp_path / "raw" / "daily"
    raw.mkdir(parents=True)
    (raw / "daily.bin").write_bytes(b"y" * 3)  # 清单要 10
    tp = FakeTransport(dl_fail=("daily.bin",))
    rc = cli.main(["sync", "--categories", "daily"], listdir=FakeListdir(_tree()),
                  transport=tp, state_path=state_path,
                  lock_path=tmp_path / "l.lock", log_dir=tmp_path / "logs",
                  raw_root=tmp_path / "raw")
    assert rc == 1
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert "daily/daily.bin" not in data["files"]
    assert data["stages"]["daily"]["build"] == "t0", "无 adopted/下载 → 不清标记"


# ---------------------------------------------------------------
# workers 透传 / 内存护栏 / env 透传
# ---------------------------------------------------------------

def test_workers_passed_through_default_8(tmp_path, monkeypatch, cookie_ok):
    seen = []
    real = sync.sync_category

    def spy(state, category, **kw):
        seen.append(kw)
        return real(state, category, **kw)

    monkeypatch.setattr(sync, "sync_category", spy)
    assert _run(["sync", "--categories", "daily", "--workers", "3"], tmp_path) == 0
    assert _run(["sync", "--categories", "daily"], tmp_path) == 0
    assert seen[0]["workers"] == 3
    assert seen[1]["workers"] == 8, "默认 8"


def test_memory_guard_sets_rlimit_only_when_env_set(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.quark_client, "cookies", lambda: "cookie=1")
    calls = []
    monkeypatch.setattr(resource, "setrlimit",
                        lambda which, lim: calls.append((which, lim)))
    monkeypatch.setenv("FACTORLAB_MAX_MEMORY", "1GB")
    rc = _run(["sync", "--dry-run", "--categories", "daily"], tmp_path)
    assert rc == 0
    assert len(calls) == 1, "显式 FACTORLAB_MAX_MEMORY → 落一次 RLIMIT_AS"
    assert calls[0][0] == resource.RLIMIT_AS
    assert calls[0][1][0] > 0 and calls[0][1][0] == calls[0][1][1]
    calls.clear()
    monkeypatch.delenv("FACTORLAB_MAX_MEMORY")
    rc = _run(["sync", "--dry-run", "--categories", "daily"], tmp_path)
    assert rc == 0 and calls == [], "未设置 → 不得动进程资源"


def test_invalid_memory_spec_exits_2(tmp_path, capsys, cookie_ok, monkeypatch):
    monkeypatch.setenv("FACTORLAB_MAX_MEMORY", "8 gigawatts")
    rc = _run(["sync", "--dry-run", "--categories", "daily"], tmp_path)
    assert rc == 2
    assert "FACTORLAB_MAX_MEMORY" in capsys.readouterr().err


def test_env_passthrough_to_stage_runner(tmp_path, monkeypatch, cookie_ok):
    monkeypatch.setattr(resource, "setrlimit", lambda *a: None)
    monkeypatch.setenv("FACTORLAB_DATA_BACKEND", "ch")
    monkeypatch.setenv("FACTORLAB_MAX_MEMORY", "1GB")
    runner = FakeRunner()
    rc = _run(["build", "--categories", "daily"], tmp_path, runner=runner)
    assert rc == 0
    assert runner.envs, "runner 应被调用"
    assert runner.envs[0].get("FACTORLAB_DATA_BACKEND") == "ch"
    assert runner.envs[0].get("FACTORLAB_MAX_MEMORY") == "1GB"


def test_stage_env_caps_malloc_arenas_by_default(tmp_path, monkeypatch, cookie_ok):
    """T10 实测（R30）：40 核 glibc arena VA 预留 ~18GB → ingest_daily VmPeak 26.0GB
    > RLIMIT_AS 24GiB（3×8GB）→ ArrowMemoryError。stage env 默认压 arena 数（=2）。"""
    monkeypatch.setattr(resource, "setrlimit", lambda *a: None)
    monkeypatch.delenv("MALLOC_ARENA_MAX", raising=False)
    runner = FakeRunner()
    rc = _run(["build", "--categories", "daily"], tmp_path, runner=runner)
    assert rc == 0
    assert runner.envs[0].get("MALLOC_ARENA_MAX") == "2", (
        "未显式设置时必须给 stage 子进程压 glibc arena 数（VA 护栏）")


def test_stage_env_respects_explicit_malloc_arena_max(tmp_path, monkeypatch, cookie_ok):
    monkeypatch.setattr(resource, "setrlimit", lambda *a: None)
    monkeypatch.setenv("MALLOC_ARENA_MAX", "8")
    runner = FakeRunner()
    rc = _run(["build", "--categories", "daily"], tmp_path, runner=runner)
    assert rc == 0
    assert runner.envs[0].get("MALLOC_ARENA_MAX") == "8", "显式设置优先（不覆盖）"


# ---------------------------------------------------------------
# verify：reconcile 全量 + 退出码传播
# ---------------------------------------------------------------

def test_verify_calls_full_reconcile_and_propagates_rc(tmp_path):
    seen = []

    def vr(cmd):
        seen.append(list(cmd))
        return 1

    assert _run(["verify"], tmp_path, verify_runner=vr) == 1
    assert seen == [[str(VENV), str(RECONCILE)]], "全量 reconcile（不带表名参数）"
    assert RECONCILE.is_file()
    assert _run(["verify"], tmp_path, verify_runner=lambda cmd: 0) == 0


# ---------------------------------------------------------------
# sync→build 失败：退出非零、publish/verify 不跑、阶段未标
# ---------------------------------------------------------------

def test_all_build_failure_exits_nonzero_without_marks_or_verify(tmp_path, capsys, cookie_ok):
    calls = []

    def vr(cmd):
        calls.append("verify")
        return 0

    runner = FakeRunner(fail_on="import_daily.py")
    rc = _run(["all", "--categories", "daily"], tmp_path,
              runner=runner, verify_runner=vr)
    assert rc == 1
    assert calls == [], "build 失败 → publish/verify 不得跑"
    data = json.loads((tmp_path / "pan_state.json").read_text(encoding="utf-8"))
    assert data["files"]["daily/daily.bin"], "sync 结果保留"
    assert not data["stages"].get("daily", {}).get("build"), "失败阶段不得标"
    assert data["runs"][-1]["status"] == "failed"
    captured = capsys.readouterr()
    assert "boom" in captured.out + captured.err


def test_all_runs_sync_then_build_then_verify_in_order(tmp_path, cookie_ok, capsys):
    events = []
    tp = FakeTransport(events=events)
    runner = FakeRunner(events=events)

    def vr(cmd):
        events.append(("verify", str(cmd[-1])))
        return 0

    assert _run(["all", "--categories", "daily"], tmp_path, transport=tp,
                runner=runner, verify_runner=vr, events=events) == 0
    kinds = [e[0] for e in events]
    assert kinds.index("download") < kinds.index("runner") < kinds.index("verify")
    assert runner.calls, "build 链应执行"
    assert [Path(c[1]).name for c in runner.calls] == [
        "import_daily.py", "ingest_daily.py", "derive_stk_limit.py", "adj_backfill.py"]


def test_all_propagates_verify_rc(tmp_path, cookie_ok):
    assert _run(["all", "--categories", "daily"], tmp_path,
                verify_runner=lambda cmd: 1) == 1, "all 必须传播 reconcile 退出码"
    assert _run(["all", "--categories", "daily", "--dry-run"], tmp_path,
                verify_runner=lambda cmd: 1) == 0, "dry-run 不执行 reconcile"


def test_publish_is_idempotent_alias_of_build_phase(tmp_path, cookie_ok):
    runner = FakeRunner()
    assert _run(["build", "--categories", "daily"], tmp_path, runner=runner) == 0
    n = len(runner.calls)
    assert n == 4
    runner.calls.clear()
    assert _run(["publish", "--categories", "daily"], tmp_path, runner=runner) == 0
    assert runner.calls == [], "publish 不重放已 build 的链（同一阶段标记）"


# ---------------------------------------------------------------
# manual_required / failed 汇总与退出码
# ---------------------------------------------------------------

def test_manual_required_not_error_and_printed(tmp_path, capsys, cookie_ok):
    tp = FakeTransport(block=("daily.bin",))
    rc = _run(["sync", "--categories", "daily"], tmp_path, transport=tp)
    assert rc == 0, "manual 不为错"
    out = capsys.readouterr().out
    assert "manual" in out and "daily.bin" in out and "size limit" in out
    assert str(tmp_path / "raw" / "daily") in out, "manual 清单给出放置目录"


def test_failed_download_exits_1_and_printed(tmp_path, capsys, cookie_ok):
    tp = FakeTransport(dl_fail=("daily.bin",))
    rc = _run(["sync", "--categories", "daily"], tmp_path, transport=tp)
    assert rc == 1
    out = capsys.readouterr().out
    assert "failed" in out and "daily.bin" in out
    assert "总结" in out


# ---------------------------------------------------------------
# --prune：清本地不在清单里的残留；dry-run 不删
# ---------------------------------------------------------------

def test_prune_removes_files_not_in_share_manifest(tmp_path, capsys, cookie_ok):
    raw = tmp_path / "raw"
    (raw / "daily").mkdir(parents=True)
    (raw / "daily" / "extra.bin").write_bytes(b"old")
    tp = FakeTransport()
    assert _run(["sync", "--categories", "daily"], tmp_path, transport=tp) == 0
    assert (raw / "daily" / "extra.bin").exists(), "默认不 prune"
    assert _run(["sync", "--categories", "daily", "--prune"], tmp_path,
                transport=FakeTransport()) == 0
    assert not (raw / "daily" / "extra.bin").exists(), "--prune 删不在网盘清单的残留"
    assert (raw / "daily" / "daily.bin").exists()


def test_prune_dry_run_lists_without_deleting(tmp_path, capsys, cookie_ok):
    raw = tmp_path / "raw"
    (raw / "daily").mkdir(parents=True)
    (raw / "daily" / "extra.bin").write_bytes(b"old")
    assert _run(["sync", "--categories", "daily", "--dry-run", "--prune"],
                tmp_path) == 0
    assert (raw / "daily" / "extra.bin").exists()
    assert "extra.bin" in capsys.readouterr().out


# ---------------------------------------------------------------
# 解析入口：--raw-dir/--out/--prev（链第一步可执行）
# ---------------------------------------------------------------

def test_parse_entry_raw_dir_out_prev(tmp_path, capsys):
    src = tmp_path / "financial"
    src.mkdir()
    (src / "2026-09-04更新简化个股基本面数据.xlsx").write_bytes(FIXTURE.read_bytes())
    out = tmp_path / "fundamentals_snapshot.parquet"
    prev = tmp_path / "rollback.parquet"
    out.write_bytes(b"old-fact")
    rc = fp.main(["--raw-dir", str(src), "--out", str(out), "--prev", str(prev)])
    assert rc == 0
    assert out.read_bytes() != b"old-fact"
    assert prev.read_bytes() == b"old-fact", "旧 fact 轮换到 --prev 指定路径"
    import polars as pl
    assert pl.read_parquet(out).height == 200


def test_parse_entry_default_prev_name(tmp_path):
    src = tmp_path / "financial"
    src.mkdir()
    (src / "2026-09-04更新简化个股基本面数据.xlsx").write_bytes(FIXTURE.read_bytes())
    out = tmp_path / "fundamentals_snapshot.parquet"
    out.write_bytes(b"old")
    assert fp.main(["--raw-dir", str(src), "--out", str(out)]) == 0
    assert (tmp_path / "fundamentals_snapshot.parquet.prev").read_bytes() == b"old"


# ---------------------------------------------------------------
# Makefile / 定时器安装脚本
# ---------------------------------------------------------------

def test_makefile_data_update_target_wires_cli_with_memory_guard():
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "data-update:" in text
    assert "FACTORLAB_MAX_MEMORY=8GB" in text
    assert "platform/tools/pan_update/cli.py all" in text


def test_install_timer_script_exists_and_has_subcommands():
    p = ROOT / "governance" / "ops" / "install_pan_timer.sh"
    assert p.is_file(), "缺安装脚本"
    r = subprocess.run(["bash", "-n", str(p)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    text = p.read_text(encoding="utf-8")
    for sub in ("install)", "uninstall)", "status)"):
        assert sub in text
    assert "systemctl --user" in text
    assert "crontab" in text
    assert "OnCalendar=*-*-* 08:10:00" in text, "每日 08:10 必须写在 timer 单元里"


def test_install_script_falls_back_to_crontab_without_user_systemd(tmp_path):
    p = ROOT / "governance" / "ops" / "install_pan_timer.sh"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "systemctl"
    stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    stub.chmod(0o755)
    log_dir = tmp_path / "logs" / "platform"
    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env["PAN_TIMER_LOG_DIR"] = str(log_dir)
    r = subprocess.run(["bash", str(p), "install"], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "crontab" in out
    assert "10 8 * * *" in out
    assert log_dir.is_dir(), "crontab 回退前必须建日志目录（否则 cron 输出重定向失败）"
    assert str(log_dir) in out


def test_install_script_readonly_status_runs(tmp_path):
    p = ROOT / "governance" / "ops" / "install_pan_timer.sh"
    r = subprocess.run(["bash", str(p), "status"], capture_output=True, text=True,
                       env={**os.environ, "HOME": str(tmp_path)})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "pan-data-update" in (r.stdout + r.stderr)


# ---------------------------------------------------------------
# 转存回退（transfer）：默认启用、--no-transfer 关闭、--keep-drive-copy 透传
# ---------------------------------------------------------------

def test_transfer_flags_default_on_and_opt_out():
    p = cli._parser()
    assert p.parse_args(["sync"]).transfer is True, "默认启用转存回退"
    assert p.parse_args(["sync", "--no-transfer"]).transfer is False
    assert p.parse_args(["sync"]).keep_drive_copy is False
    assert p.parse_args(["sync", "--keep-drive-copy"]).keep_drive_copy is True


def test_cli_blocked_item_goes_through_transfer_and_records_state(tmp_path, capsys, cookie_ok):
    x = FakeTransferClient()
    tp = FakeTransport(block=("daily.bin",))
    rc = _run(["sync", "--categories", "daily"], tmp_path, transport=tp,
              transfer_client=x)
    assert rc == 0
    assert x.calls == [("daily.bin", str(tmp_path / "raw" / "daily" / "daily.bin"))]
    data = json.loads((tmp_path / "pan_state.json").read_text(encoding="utf-8"))
    assert data["files"]["daily/daily.bin"]["size"] == 10
    out = capsys.readouterr().out
    assert "manual_required" not in out, "已转存成功不得再报 manual"


def test_cli_no_transfer_flag_keeps_manual_required(tmp_path, capsys, cookie_ok):
    x = FakeTransferClient()
    rc = _run(["sync", "--categories", "daily", "--no-transfer"], tmp_path,
              transport=FakeTransport(block=("daily.bin",)), transfer_client=x)
    assert rc == 0 and x.calls == [], "--no-transfer 必须关闭回退"
    out = capsys.readouterr().out
    assert "manual" in out and "daily.bin" in out and "size limit" in out


def test_cli_transfer_failure_exits_1_and_prints(tmp_path, capsys, cookie_ok):
    x = FakeTransferClient(fail=xfer.TransferTimeout("转存 task 超时（600s）"))
    rc = _run(["sync", "--categories", "daily"], tmp_path,
              transport=FakeTransport(block=("daily.bin",)), transfer_client=x)
    assert rc == 1, "转存失败必须 loud fail"
    out = capsys.readouterr().out
    assert "failed" in out and "transfer:" in out and "超时" in out
    data = json.loads((tmp_path / "pan_state.json").read_text(encoding="utf-8"))
    assert "daily/daily.bin" not in data["files"]


def test_cli_builds_production_transfer_with_flags(tmp_path, monkeypatch, cookie_ok):
    made = []

    class Rec:
        def __init__(self, transport, **kw):
            made.append({"transport": transport, **kw})

        def available(self):
            return True

        def fetch(self, item, dest):
            dest = Path(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"T")

    monkeypatch.setattr(cli.transfer, "DriveTransfer", Rec)
    rc = cli.main(
        ["sync", "--categories", "daily", "--keep-drive-copy"],
        listdir=FakeListdir(_tree()), transport=FakeTransport(block=("daily.bin",)),
        runner=FakeRunner(), verify_runner=lambda cmd: 0,
        state_path=tmp_path / "s.json", lock_path=tmp_path / "l.lock",
        log_dir=tmp_path / "logs", raw_root=tmp_path / "raw")
    assert rc == 0
    assert made and made[0]["keep_copy"] is True, "--keep-drive-copy 必须透传"
    assert isinstance(made[0]["transport"], xfer.QuarkPcTransport), "生产 transport"
    data = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert data["files"]["daily/daily.bin"], "转存成功要记账"


# ---------------------------------------------------------------
# 文档防漂移：转存回退的开关/临时目录/不可逆删除必须与实现同步
# ---------------------------------------------------------------

def test_readme_documents_transfer_fallback():
    text = (Path(cli.__file__).parent / "README.md").read_text(encoding="utf-8")
    for token in ("--transfer", "--no-transfer", "--keep-drive-copy",
                  xfer.TMP_DIR_NAME, "TransferError"):
        assert token in text, f"README 缺转存回退说明：{token}"
    assert "删除" in text, "不可逆删除（网盘临时副本）必须显式告知"


def test_design_doc_documents_transfer_fallback():
    doc = (ROOT / "knowledge" / "design" / "workspace"
           / "2026-09-16-pan-data-update-design.md").read_text(encoding="utf-8")
    assert "transfer.py" in doc and xfer.TMP_DIR_NAME in doc


def test_interface_contract_documents_transfer_fallback():
    text = (ROOT / "knowledge" / "contracts" / "interface.md").read_text(encoding="utf-8")
    assert "--no-transfer" in text and xfer.TMP_DIR_NAME in text
