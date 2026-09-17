"""sync.py 行为测试：fake transport 离线验证差集消费、manual 分类、原子落盘、幂等。

需求源：.superpowers/sdd/2026-09-16-pan-data-update-plan/task-3-brief.md
设计：knowledge/design/workspace/2026-09-16-pan-data-update-design.md §2.1/§4/§7/§8
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
from pathlib import Path

import pytest

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


# —— 修复轮 1：I1 并行下载 / I2 403·412 重取链 / I3 download()→False ——

class _PeakT(T):
    """记录并发峰值的 fake transport：fid=1 故意慢于 fid=2（完成序与条目序相反）。"""

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def download(self, url, out, size):
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(0.2 if url.endswith("/1") else 0.01)
        try:
            return super().download(url, out, size)
        finally:
            with self._lock:
                self.active -= 1


def test_parallel_download_peak_over_one_and_reports_in_entry_order(tmp_path):
    """I1：默认 workers=8 → 真并发（峰值≥2）；归集仍按条目顺序（b 先完成不影响）。"""
    t = _PeakT()
    s = {"version": 1, "files": {}, "stages": {}, "runs": []}
    rep = sync.sync_category(s, "daily",
                             entries=[_entry("a.zip", 10, "1"), _entry("b.zip", 20, "2")],
                             transport=t, dest_root=tmp_path, dry_run=False)
    assert t.peak >= 2
    assert t.dl and t.dl[0][0].endswith("/2")  # b（快）确实先完成，报告仍按条目序
    assert rep.downloaded == ["a.zip", "b.zip"]
    assert s["files"]["daily/a.zip"]["size"] == 10


def test_workers_one_stays_serial(tmp_path):
    """I1 边界：workers=1 → 峰值恒 1（并发上限被尊重）。"""
    t = _PeakT()
    s = {"version": 1, "files": {}, "stages": {}, "runs": []}
    rep = sync.sync_category(s, "daily",
                             entries=[_entry("a.zip", 10, "1"), _entry("b.zip", 20, "2")],
                             transport=t, dest_root=tmp_path, dry_run=False, workers=1)
    assert t.peak == 1
    assert rep.downloaded == ["a.zip", "b.zip"]


def test_download_false_with_full_size_marks_failed(tmp_path):
    """I3：transport 写满 size 但返回 False → failed("download failed")，不记账无 .part。"""
    class FalseT(T):
        def download(self, url, out, size):
            self.dl.append((url, str(out)))
            out.write_bytes(b"x" * size)
            return False

    s = {"version": 1, "files": {}, "stages": {}, "runs": []}
    rep = sync.sync_category(s, "daily", entries=[_entry("a.zip", 10)],
                             transport=FalseT(), dest_root=tmp_path, dry_run=False)
    assert rep.downloaded == []
    assert rep.failed == [{"name": "a.zip", "reason": "download failed"}]
    assert s["files"] == {}
    assert not (tmp_path / "a.zip").exists()
    assert not list(tmp_path.rglob("*.part"))


def _quark_transport_fakes(monkeypatch, first_error):
    state = {"fetches": [], "downloads": []}

    def fake_http(url, body=None, retry=3, timeout=60):
        state["fetches"].append(list(body["fids"]))
        fid = body["fids"][0]
        return 200, {"status": 200,
                     "data": [{"fid": fid,
                               "download_url": f"http://d/{fid}?v={len(state['fetches'])}"}]}

    def fake_download_file(url, out, expect_size):
        state["downloads"].append(url)
        if len(state["downloads"]) == 1:
            raise first_error(url)
        out.write_bytes(b"x" * expect_size)
        return True, expect_size

    monkeypatch.setattr(sync.quark_client, "http", fake_http)
    monkeypatch.setattr(sync.quark_client, "get_stoken", lambda *a, **k: "ST")
    monkeypatch.setattr(sync.quark_client, "download_file", fake_download_file)
    return state


def test_quark_transport_refetches_link_once_on_412(monkeypatch, tmp_path):
    """I2：首次下载 412 → 重取链一次并重试成功；取链恰 2 次、换新链。"""
    state = _quark_transport_fakes(
        monkeypatch, lambda url: urllib.error.HTTPError(url, 412, "Precondition Failed", None, None))
    tr = sync.QuarkTransport()
    items = [_entry("a.zip", 10, "1")]
    urls = tr.list_urls(items)
    assert len(state["fetches"]) == 1

    ok = tr.download(urls["1"], tmp_path / "a.part", 10)

    assert ok is True
    assert len(state["fetches"]) == 2  # 重新取链恰一次
    assert len(state["downloads"]) == 2
    assert state["downloads"][0] != state["downloads"][1]
    assert (tmp_path / "a.part").read_bytes() == b"x" * 10


def test_quark_transport_refetches_on_expired_link_message(monkeypatch, tmp_path):
    """I2 备选口径：异常文本含「链接过期」也触发重取。"""
    state = _quark_transport_fakes(monkeypatch, lambda url: RuntimeError("下载链接已过期"))
    tr = sync.QuarkTransport()
    items = [_entry("a.zip", 10, "1")]
    urls = tr.list_urls(items)

    ok = tr.download(urls["1"], tmp_path / "a.part", 10)

    assert ok is True
    assert len(state["fetches"]) == 2 and len(state["downloads"]) == 2


def test_quark_transport_no_refetch_for_other_errors(monkeypatch, tmp_path):
    """I2 边界：非 403/412/过期 → 不重取（取链恰 1 次），异常原样上抛。"""
    state = _quark_transport_fakes(monkeypatch, lambda url: OSError("boom"))
    tr = sync.QuarkTransport()
    items = [_entry("a.zip", 10, "1")]
    urls = tr.list_urls(items)

    with pytest.raises(OSError, match="boom"):
        tr.download(urls["1"], tmp_path / "a.part", 10)

    assert len(state["fetches"]) == 1 and len(state["downloads"]) == 1


# —— 修复轮 1（P2a）：manual 本地登记（adopted）——

def _empty_state():
    return {"version": 1, "files": {}, "stages": {}, "runs": []}


def test_adopt_local_file_with_matching_size(tmp_path):
    """分享清单存在 + 本地已有 + size 匹配 + state 未登记 → adopted（不取链/下载）。"""
    t = T()
    s = _empty_state()
    dest = tmp_path / "raw"
    dest.mkdir()
    (dest / "a.zip").write_bytes(b"y" * 10)
    rep = sync.sync_category(s, "daily", entries=[_entry("a.zip", 10, "1")],
                             transport=t, dest_root=dest)
    assert rep.adopted == ["a.zip"]
    assert rep.downloaded == [] and t.dl == [], "adopted 不得再取链/下载"
    e = s["files"]["daily/a.zip"]
    assert e["adopted"] is True and e["synced_at"]


def test_adopt_size_mismatch_not_adopted_and_downloaded(tmp_path):
    """size 不符不登记：照常下载覆盖，state 不带 adopted 标记。"""
    t = T()
    s = _empty_state()
    dest = tmp_path / "raw"
    dest.mkdir()
    (dest / "a.zip").write_bytes(b"y" * 3)
    rep = sync.sync_category(s, "daily", entries=[_entry("a.zip", 10, "1")],
                             transport=t, dest_root=dest)
    assert rep.adopted == []
    assert rep.downloaded == ["a.zip"]
    assert (dest / "a.zip").read_bytes() == b"x" * 10
    assert "adopted" not in s["files"]["daily/a.zip"]


def test_adopt_skips_already_recorded_entries(tmp_path):
    """state 已登记 → 不重复 adopted（仍走 unchanged 跳过）。"""
    t = T()
    s = _empty_state()
    s["files"]["daily/a.zip"] = {"name": "a.zip", "size": 10, "fid": "1",
                                  "synced_at": "2026-09-16T00:00:00"}
    dest = tmp_path / "raw"
    dest.mkdir()
    (dest / "a.zip").write_bytes(b"y" * 10)
    rep = sync.sync_category(s, "daily", entries=[_entry("a.zip", 10, "1")],
                             transport=t, dest_root=dest)
    assert rep.adopted == [] and rep.unchanged == ["a.zip"] and t.dl == []


def test_adopt_dry_run_reports_without_state_write(tmp_path):
    """dry-run：报 adopted 且从 to_fetch 剔除；不写 state（含 state_path 不落盘）。"""
    t = T()
    s = _empty_state()
    dest = tmp_path / "raw"
    dest.mkdir()
    (dest / "a.zip").write_bytes(b"y" * 10)
    state_path = tmp_path / "pan_state.json"
    rep = sync.sync_category(s, "daily", entries=[_entry("a.zip", 10, "1")],
                             transport=t, dest_root=dest, dry_run=True,
                             state_path=state_path)
    assert rep.adopted == ["a.zip"]
    assert rep.to_fetch == [] and rep.downloaded == []
    assert s["files"] == {} and not state_path.exists() and t.dl == []


def test_adopt_traversal_path_skipped(tmp_path):
    """越界 rel_path 不 adopt（与下载同口径：外部清单不可信）。"""
    t = T()
    s = _empty_state()
    dest = tmp_path / "raw"
    dest.mkdir()
    (tmp_path / "evil").write_bytes(b"y" * 10)
    entries = [{"name": "evil", "size": 10, "rel_path": "../evil",
                "fid": "1", "fid_token": "t"}]
    rep = sync.sync_category(s, "daily", entries=entries, transport=t, dest_root=dest)
    assert rep.adopted == [] and s["files"] == {}


# —— 转存回退（transfer）：超限项经自有网盘下载；失败 loud 不误删 ——

class FakeTransfer:
    """注入 sync 的转存客户端：available/fetch 行为可脚本化，记录调用。"""

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
        Path(dest).write_bytes(b"T")


def test_blocked_item_transferred_when_available(tmp_path):
    """size limit 项 → transfer.fetch 落盘 + 记账 downloaded；不再进 manual。"""
    s = _empty_state()
    x = FakeTransfer()
    rep = sync.sync_category(s, "financials", entries=_entries(), transport=T(),
                             transfer=x, dest_root=tmp_path)
    assert rep.downloaded == ["a.zip", "big.parquet"]
    assert rep.manual == []
    assert x.calls == [("big.parquet", str(tmp_path / "big.parquet"))]
    assert (tmp_path / "big.parquet").read_bytes() == b"T"
    assert s["files"]["financials/big.parquet"]["size"] == 367_000_000


def test_transfer_failure_marks_failed_loud(tmp_path):
    """task 超时/风控 → failed（reason 带 transfer:），不记账、不误报 manual。"""
    from pan_update import transfer as xfer

    s = _empty_state()
    x = FakeTransfer(fail=xfer.TransferTimeout("转存 task 超时（600s）"))
    rep = sync.sync_category(s, "financials", entries=_entries(), transport=T(),
                             transfer=x, dest_root=tmp_path)
    assert rep.manual == []
    assert rep.downloaded == ["a.zip"]
    assert rep.failed == [{"name": "big.parquet",
                           "reason": "transfer: 转存 task 超时（600s）"}]
    assert set(s["files"]) == {"financials/a.zip"}


def test_transfer_unavailable_keeps_manual_required(tmp_path):
    """cookie 不可用（available=False）→ 维持 manual_required，不调 fetch。"""
    s = _empty_state()
    x = FakeTransfer(available=False)
    rep = sync.sync_category(s, "financials", entries=_entries(), transport=T(),
                             transfer=x, dest_root=tmp_path)
    assert rep.manual == [{"name": "big.parquet", "reason": "size limit"}]
    assert x.calls == [] and rep.failed == []


def test_no_transfer_client_keeps_manual_required(tmp_path):
    s = _empty_state()
    rep = sync.sync_category(s, "financials", entries=_entries(), transport=T(),
                             dest_root=tmp_path)
    assert rep.manual == [{"name": "big.parquet", "reason": "size limit"}]


def test_transfer_not_called_for_unblocked_items(tmp_path):
    s = _empty_state()
    x = FakeTransfer()
    rep = sync.sync_category(s, "daily",
                             entries=[_entry("a.zip", 10, "1"), _entry("b.zip", 20, "2")],
                             transport=T(), transfer=x, dest_root=tmp_path)
    assert rep.downloaded == ["a.zip", "b.zip"]
    assert x.calls == []


def test_blocked_traversal_item_never_reaches_transfer(tmp_path):
    s = _empty_state()
    x = FakeTransfer()
    entries = [{"name": "evil", "size": 367_000_000, "rel_path": "../evil",
                "fid": "1", "fid_token": "t"}]
    rep = sync.sync_category(s, "daily", entries=entries, transport=T(),
                             transfer=x, dest_root=tmp_path / "raw")
    assert x.calls == [], "越界路径不得传给转存（外部清单不可信）"
    assert rep.failed == [{"name": "evil", "reason": "rel_path 越界：'../evil'"}]
    assert rep.manual == []
