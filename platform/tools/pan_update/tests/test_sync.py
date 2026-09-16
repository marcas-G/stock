"""sync.py 行为测试：fake transport 离线验证差集消费、manual 分类、原子落盘、幂等。

需求源：.superpowers/sdd/2026-09-16-pan-data-update-plan/task-3-brief.md
设计：knowledge/design/workspace/2026-09-16-pan-data-update-design.md §2.1/§4/§7/§8
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from pan_update import share, sync

def _entries():
    return [
        {"name": "a.zip", "size": 10, "rel_path": "a.zip", "fid": "1", "fid_token": "t"},
        {"name": "big.parquet", "size": 367_000_000, "rel_path": "big.parquet", "fid": "2", "fid_token": "t"},
    ]

class T:
    def __init__(self): self.dl = []
    def list_urls(self, items):
        out = {}
        for i in items:
            if i["size"] > 100_000_000:
                i["_blocked_reason"] = "size limit"
            else:
                out[i["fid"]] = f"http://fake/{i['fid']}"
        return out
    def download(self, url, out, size):
        self.dl.append((url, str(out))); out.write_bytes(b"x" * size); return True

def test_sync_downloads_small_and_marks_big_manual(tmp_path):
    s = {"version": 1, "files": {}, "stages": {}, "runs": []}
    rep = sync.sync_category(s, "financials", entries=_entries(), transport=T(),
                             dest_root=tmp_path, dry_run=False)
    assert rep.downloaded == ["a.zip"]
    assert rep.manual == [{"name": "big.parquet", "reason": "size limit"}]
    assert (tmp_path / "a.zip").read_bytes() == b"x" * 10
    assert s["files"]["financials/a.zip"]["size"] == 10

def test_sync_dry_run_writes_nothing(tmp_path):
    s = {"version": 1, "files": {}, "stages": {}, "runs": []}
    t = T()
    rep = sync.sync_category(s, "daily", entries=_entries(), transport=t,
                             dest_root=tmp_path, dry_run=True)
    assert rep.downloaded == [] and rep.to_fetch == ["a.zip", "big.parquet"]
    assert t.dl == [] and not list(tmp_path.iterdir()) and s["files"] == {}


# —— 增补守卫（红→绿）：dry-run 不触网、幂等、失败分类、.part 原子替换、生产传输 ——

def _entry(name, size, rid="1"):
    return {"name": name, "size": size, "rel_path": name, "fid": rid, "fid_token": "t"}


def test_dry_run_does_not_touch_transport(tmp_path):
    """设计 §7：--dry-run 不触网（list_urls 也不许调）。"""
    class Rec(T):
        def __init__(self):
            super().__init__(); self.url_calls = 0
        def list_urls(self, items):
            self.url_calls += 1; return super().list_urls(items)

    t = Rec()
    s = {"version": 1, "files": {}, "stages": {}, "runs": []}
    rep = sync.sync_category(s, "daily", entries=_entries(), transport=t,
                             dest_root=tmp_path, dry_run=True)
    assert rep.to_fetch == ["a.zip", "big.parquet"]
    assert t.url_calls == 0 and t.dl == [] and s["files"] == {}


def test_second_run_unchanged_is_noop(tmp_path):
    """幂等：同名 size 同 → unchanged 不下；manual 项不记账 → 下次仍 to_fetch。"""
    s = {"version": 1, "files": {}, "stages": {}, "runs": []}
    sync.sync_category(s, "financials", entries=_entries(), transport=T(), dest_root=tmp_path)
    t2 = T()
    rep = sync.sync_category(s, "financials", entries=_entries(), transport=t2,
                             dest_root=tmp_path)
    assert rep.downloaded == [] and rep.unchanged == ["a.zip"]
    assert rep.to_fetch == ["big.parquet"] and rep.manual == [{"name": "big.parquet", "reason": "size limit"}]
    assert t2.dl == [] and s["files"]["financials/a.zip"]["size"] == 10


def test_size_mismatch_marks_failed_and_leaves_no_final_file(tmp_path):
    """设计 §8：size 校验不过 → 不 rename、不记账、清 .part。"""
    class Bad(T):
        def download(self, url, out, size):
            self.dl.append((url, str(out))); out.write_bytes(b"x" * (size - 1)); return True

    t = Bad()
    s = {"version": 1, "files": {}, "stages": {}, "runs": []}
    rep = sync.sync_category(s, "daily", entries=[_entry("a.zip", 10)],
                             transport=t, dest_root=tmp_path, dry_run=False)
    assert rep.downloaded == []
    assert rep.failed == [{"name": "a.zip", "reason": "size mismatch: got 9, want 10"}]
    assert t.dl and t.dl[0][1].endswith("a.zip.part")  # 先写 .part
    assert not (tmp_path / "a.zip").exists()
    assert not list(tmp_path.rglob("*.part"))
    assert s["files"] == {}


def test_download_exception_marks_failed_and_continues(tmp_path):
    """单个下载异常不 fail 整链：记账 failed，后续文件继续。"""
    class Flaky(T):
        def download(self, url, out, size):
            if url.endswith("/2"):
                raise OSError("boom")
            return super().download(url, out, size)

    s = {"version": 1, "files": {}, "stages": {}, "runs": []}
    rep = sync.sync_category(s, "daily",
                             entries=[_entry("a.zip", 10, "1"), _entry("b.zip", 20, "2")],
                             transport=Flaky(), dest_root=tmp_path, dry_run=False)
    assert rep.downloaded == ["a.zip"]
    assert rep.failed == [{"name": "b.zip", "reason": "download error: boom"}]
    assert (tmp_path / "a.zip").exists() and not (tmp_path / "b.zip").exists()


def test_state_path_records_partial_progress(tmp_path):
    """断点：每个成功文件后落 state 原子文件；后续失败不丢已下记录。"""
    class Flaky(T):
        def download(self, url, out, size):
            if url.endswith("/2"):
                raise OSError("boom")
            return super().download(url, out, size)

    s = {"version": 1, "files": {}, "stages": {}, "runs": []}
    state_path = tmp_path / "pan_state.json"
    rep = sync.sync_category(s, "daily",
                             entries=[_entry("a.zip", 10, "1"), _entry("b.zip", 20, "2")],
                             transport=Flaky(), dest_root=tmp_path / "raw",
                             state_path=state_path, dry_run=False)
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert set(data["files"]) == {"daily/a.zip"}
    assert data["files"]["daily/a.zip"]["size"] == 10
    assert rep.downloaded == ["a.zip"]


def test_size_limit_exception_marks_manual_and_retries_rest(tmp_path):
    """brief 协议备选：list_urls 抛 SizeLimitExceeded(name) → manual；其余项重取链。"""
    calls = []

    class ExcT(T):
        def list_urls(self, items):
            calls.append([i["name"] for i in items])
            if any(i["name"] == "big.parquet" for i in items):
                raise sync.SizeLimitExceeded("big.parquet")
            return super().list_urls(items)

    s = {"version": 1, "files": {}, "stages": {}, "runs": []}
    rep = sync.sync_category(s, "financials", entries=_entries(), transport=ExcT(),
                             dest_root=tmp_path, dry_run=False)
    assert rep.downloaded == ["a.zip"]
    assert rep.manual == [{"name": "big.parquet", "reason": "size limit"}]
    assert calls == [["a.zip", "big.parquet"], ["a.zip"]]


def test_fetch_error_detail_surfaces_in_failed(tmp_path):
    """非 size-limit 的缺链原因（item["_fetch_error"]）透传到 failed。"""
    class ErrT:
        def list_urls(self, items):
            for i in items:
                i["_fetch_error"] = "HTTP 403 权限不足"
            return {}
        def download(self, url, out, size):
            raise AssertionError("不应被调用")

    s = {"version": 1, "files": {}, "stages": {}, "runs": []}
    rep = sync.sync_category(s, "daily", entries=[_entry("a.zip", 10)],
                             transport=ErrT(), dest_root=tmp_path, dry_run=False)
    assert rep.failed == [{"name": "a.zip", "reason": "HTTP 403 权限不足"}]
    assert rep.downloaded == [] and rep.manual == []


def test_missing_url_without_reason_goes_failed(tmp_path):
    class Blank:
        def list_urls(self, items):
            return {}
        def download(self, url, out, size):
            raise AssertionError("不应被调用")

    s = {"version": 1, "files": {}, "stages": {}, "runs": []}
    rep = sync.sync_category(s, "daily", entries=_entries(), transport=Blank(),
                             dest_root=tmp_path, dry_run=False)
    assert rep.failed == [{"name": "a.zip", "reason": "no url"},
                          {"name": "big.parquet", "reason": "no url"}]


def test_share_entries_converted_at_consumer_boundary(tmp_path):
    """T2 裁决：iter_category 产 Entry dataclass，T3 消费边界 asdict 转 dict。"""
    entries = [share.Entry(name="a.zip", size=10, fid="1", fid_token="t",
                           rel_path="a.zip", is_dir=False)]
    s = {"version": 1, "files": {}, "stages": {}, "runs": []}
    rep = sync.sync_category(s, "daily", entries=entries, transport=T(),
                             dest_root=tmp_path, dry_run=False)
    assert rep.downloaded == ["a.zip"]
    assert (tmp_path / "a.zip").read_bytes() == b"x" * 10
    assert s["files"]["daily/a.zip"]["size"] == 10


def test_nested_rel_path_creates_parent_dirs(tmp_path):
    entries = [_entry("2026/09/20260916.zip", 5)]
    s = {"version": 1, "files": {}, "stages": {}, "runs": []}
    rep = sync.sync_category(s, "minutes", entries=entries, transport=T(),
                             dest_root=tmp_path, dry_run=False)
    assert rep.downloaded == ["2026/09/20260916.zip"]
    assert (tmp_path / "2026/09/20260916.zip").read_bytes() == b"x" * 5


def test_rel_path_traversal_rejected(tmp_path):
    """外部清单不可信：rel_path 越出 dest_root → failed，不落盘。"""
    entries = [{"name": "evil", "size": 1, "rel_path": "../evil", "fid": "1", "fid_token": "t"}]
    s = {"version": 1, "files": {}, "stages": {}, "runs": []}
    rep = sync.sync_category(s, "daily", entries=entries, transport=T(),
                             dest_root=tmp_path / "raw", dry_run=False)
    assert rep.failed == [{"name": "evil", "reason": "rel_path 越界：'../evil'"}]
    assert rep.downloaded == [] and s["files"] == {}
    assert not (tmp_path / "evil").exists()


def test_script_direct_run_bootstraps_import_path(tmp_path):
    """直跑 sync.py（sys.path[0]=pan_update/、无 conftest）必须自举成功。"""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    proc = subprocess.run(
        [sys.executable, str(Path(sync.__file__).resolve())],
        env=env, capture_output=True, text=True, timeout=120, cwd=str(tmp_path),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ModuleNotFoundError" not in (proc.stdout + proc.stderr)


# —— 生产 transport（QuarkTransport）：批量取链 + 缺链单项探测 ——

def test_quark_transport_probes_missing_and_classifies_size_limit(monkeypatch):
    """批次整体 400（大文件在批内）→ 单项探测：小件补链、大件标 size limit。"""
    calls = []

    def fake_http(url, body=None, retry=3, timeout=60):
        fids = list(body["fids"])
        calls.append((url, fids))
        if len(fids) > 1:
            return 400, {"status": 400, "message": "download file size limit"}
        if fids == ["small1"]:
            return 200, {"status": 200,
                         "data": [{"fid": "small1", "download_url": "http://d/small1"}]}
        return 400, {"status": 400, "message": "download file size limit"}

    monkeypatch.setattr(sync.quark_client, "http", fake_http)
    monkeypatch.setattr(sync.quark_client, "get_stoken", lambda *a, **k: "ST")
    items = [_entry("s.zip", 10, "small1"), _entry("b.parquet", 367_000_000, "big1")]

    urls = sync.QuarkTransport().list_urls(items)

    assert urls == {"small1": "http://d/small1"}  # 单项探测把批内小件救回来
    assert items[0].get("_blocked_reason") is None
    assert items[1]["_blocked_reason"] == "size limit"
    probes = [(u, f) for u, f in calls if "/file/download" in u and len(f) == 1]
    assert {f[0] for _, f in probes} == {"small1", "big1"}  # 真调了单项探测


def test_quark_transport_probe_marks_dl_guest_and_http_error(monkeypatch):
    def fake_http(url, body=None, retry=3, timeout=60):
        fids = list(body["fids"])
        if len(fids) > 1:
            return 200, {"status": 200, "data": []}
        if fids == ["guest1"]:
            return 200, {"status": 200,
                         "data": [{"fid": "guest1",
                                   "download_url": "https://dl-guest.quark.cn/x"}]}
        return 403, {"status": 403, "message": "权限不足"}

    monkeypatch.setattr(sync.quark_client, "http", fake_http)
    monkeypatch.setattr(sync.quark_client, "get_stoken", lambda *a, **k: "ST")
    items = [_entry("g.zip", 1, "guest1"), _entry("e.zip", 2, "err1")]

    urls = sync.QuarkTransport().list_urls(items)

    assert urls == {}
    assert items[0].get("_blocked_reason") is None
    assert "guest" in items[0]["_fetch_error"]
    assert items[1]["_fetch_error"] == "HTTP 403 权限不足"


def test_quark_transport_download_delegates(monkeypatch, tmp_path):
    seen = {}

    def fake_download_file(url, out, expect_size):
        seen["args"] = (url, str(out), expect_size)
        out.write_bytes(b"x" * expect_size)
        return True, expect_size

    monkeypatch.setattr(sync.quark_client, "download_file", fake_download_file)
    out = tmp_path / "x.part"
    ok = sync.QuarkTransport().download("http://d/x", out, 5)
    assert ok is True
    assert seen["args"] == ("http://d/x", str(out), 5)
    assert out.read_bytes() == b"x" * 5
