"""transfer.py 行为测试：超限大文件「转存自有盘 → 自取直链」回退（离线 fake transport）。

需求源：2026-09-17 任务「分享大文件自动转存到自有网盘 → 自取直链下载」
设计：knowledge/design/workspace/2026-09-16-pan-data-update-design.md §2.1
断言纪律：fake transport 记录每一步 URL/body 与调用顺序——硬编码返回值的存根必败。
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from pan_update import config, transfer


def _item(name="big.parquet", size=4096, fid="sf1", fid_token="st1"):
    return {"name": name, "size": size, "fid": fid, "fid_token": fid_token,
            "rel_path": name}


class FakeDrive:
    """脚本化网盘：按端点路由，记录调用 sequence；save 后把文件放入临时目录。"""

    def __init__(self, *, root=None, tmp=None, saved_file=None, own_url=None,
                 download=None, delete_resp=None, save_resp=None,
                 create_resp=None):
        self.root = (root if root is not None else
                     [{"file_name": transfer.TMP_DIR_NAME, "fid": "TMP", "dir": True}])
        self.tmp = list(tmp or [])
        self.saved_file = (saved_file if saved_file is not None else
                           {"file_name": "big.parquet", "fid": "OF1",
                            "size": 4096, "dir": False})
        self.own_url = own_url or "http://own.dl/big.parquet"
        self.calls = []
        self.downloads = []
        self._download_hook = download
        self._delete_resp = delete_resp
        self._save_resp = save_resp
        self._create_resp = create_resp
        self._task_queue = []
        self._last_task = (2, 100)

    def queue_tasks(self, *states):
        self._task_queue.extend(states)

    def get_stoken(self):
        return "ST"

    def http(self, url, body=None):
        self.calls.append((url, body))
        if "/file/sort" in url:
            pdir = parse_qs(urlparse(url).query)["pdir_fid"][0]
            lst = self.root if pdir == "0" else self.tmp
            return 200, {"status": 200, "data": {"list": lst}}
        if "/clouddrive/file?" in url:
            if self._create_resp is not None:
                return self._create_resp
            self.root = self.root + [{"file_name": body["file_name"],
                                      "fid": "TMP", "dir": True}]
            return 200, {"status": 200, "data": {"task_id": "TC"}}
        if "/share/sharepage/save" in url:
            if self._save_resp is not None:
                return self._save_resp
            self.tmp = self.tmp + [dict(self.saved_file)]
            return 200, {"status": 200, "data": {"task_id": "TS"}}
        if "/task" in url:
            if self._task_queue:
                self._last_task = self._task_queue.pop(0)
            state, progress = self._last_task
            if state == 2:
                return 200, {"status": 200,
                             "data": {"task_id": "T", "status": 2, "progress": 100}}
            return 200, {"status": 200,
                         "data": {"task_id": "T", "status": state,
                                  "progress": progress, "message": "风控拦截"}}
        if "/file/download" in url:
            return 200, {"status": 200,
                         "data": [{"fid": body["fids"][0], "download_url": self.own_url}]}
        if "/file/delete" in url:
            if self._delete_resp is not None:
                return self._delete_resp
            fid = body["filelist"][0]
            self.tmp = [f for f in self.tmp if f.get("fid") != fid]
            self.root = [f for f in self.root if f.get("fid") != fid]
            return 200, {"status": 200, "data": {"task_id": "TD"}}
        raise AssertionError(f"未脚本化 URL：{url}")

    def download(self, url, out, size):
        self.downloads.append((url, str(out), size))
        if self._download_hook is not None:
            return self._download_hook(url, out, size)
        Path(out).write_bytes(b"x" * size)
        return True


def _kinds(calls):
    def kind(url):
        if "/file/sort" in url:
            return "list"
        if "/clouddrive/file?" in url:
            return "create_dir"
        if "sharepage/save" in url:
            return "save"
        if "/task" in url:
            return "task"
        if "/file/download" in url:
            return "own_url"
        if "/file/delete" in url:
            return "delete"
        return "?"
    return [kind(u) for u, _ in calls]


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def _transfer(fake, **kw):
    return transfer.DriveTransfer(fake, **kw)


# ---------------------------------------------------------------
# happy path：建/复用目录 → save → 轮询 task → 找自有 fid → 自取直链
#              → 下载 .part → size 校验 → 删副本
# ---------------------------------------------------------------

def test_fetch_transfers_polls_downloads_then_deletes_after_verify(tmp_path):
    fake = FakeDrive(tmp=[])
    fake.queue_tasks((2, 100), (2, 100))
    dest = tmp_path / "financial" / "big.parquet"

    _transfer(fake).fetch(_item(), dest)

    assert dest.read_bytes() == b"x" * 4096
    assert not list(tmp_path.rglob("*.part")), "半成品不得留盘"
    assert fake.downloads == [("http://own.dl/big.parquet",
                               str(dest.with_name("big.parquet.part")),
                               4096)]
    assert _kinds(fake.calls) == ["list", "list", "save", "task", "list",
                                  "own_url", "delete", "task"]
    save_url, save_body = fake.calls[2]
    assert "/share/sharepage/save" in save_url
    assert save_body == {
        "fid_list": ["sf1"], "fid_token_list": ["st1"], "to_pdir_fid": "TMP",
        "pdir_fid": "0", "scenario": "link", "stoken": "ST",
        "pwd_id": config.PWD_ID,
    }
    task_urls = [u for u, _ in fake.calls if "/task" in u]
    assert "task_id=TS" in task_urls[0] and "retry_index=0" in task_urls[0]
    assert "task_id=TD" in task_urls[1], "删除也要轮询到完成"
    _, own_body = fake.calls[5]
    assert own_body == {"fids": ["OF1"]}
    _, del_body = fake.calls[6]
    assert del_body == {"filelist": ["OF1"], "action_type": 2, "exclude_fids": []}


def test_fetch_reuses_existing_drive_copy_without_save(tmp_path):
    fake = FakeDrive(tmp=[{"file_name": "big.parquet", "fid": "OF1",
                           "size": 4096, "dir": False}])
    fake.queue_tasks((2, 100))
    dest = tmp_path / "big.parquet"

    _transfer(fake).fetch(_item(), dest)

    kinds = _kinds(fake.calls)
    assert "save" not in kinds and "create_dir" not in kinds, "已有同 size 副本不得重复转存"
    assert "own_url" in kinds and "delete" in kinds
    assert dest.stat().st_size == 4096


def test_fetch_keep_copy_skips_delete(tmp_path):
    fake = FakeDrive(tmp=[{"file_name": "big.parquet", "fid": "OF1",
                           "size": 4096, "dir": False}])
    dest = tmp_path / "big.parquet"

    _transfer(fake, keep_copy=True).fetch(_item(), dest)

    assert dest.exists()
    assert all("/file/delete" not in u for u, _ in fake.calls), "keep_copy 不得删副本"


def test_fetch_creates_tmp_dir_when_missing(tmp_path):
    fake = FakeDrive(root=[])
    fake.queue_tasks((2, 100), (2, 100), (2, 100))
    dest = tmp_path / "big.parquet"

    _transfer(fake).fetch(_item(), dest)

    kinds = _kinds(fake.calls)
    assert kinds[:3] == ["list", "create_dir", "task"], "先查根目录再建 factorlab_tmp"
    _, create_body = next(c for c in fake.calls if "/clouddrive/file?" in c[0])
    assert create_body == {"pdir_fid": "0", "file_name": transfer.TMP_DIR_NAME,
                           "dir_path": "", "file_type": 0}
    save_body = next(b for u, b in fake.calls if "sharepage/save" in u)
    assert save_body["to_pdir_fid"] == "TMP"


def test_available_false_without_cookie(tmp_path):
    fake = FakeDrive()
    assert _transfer(fake, cookie_check=lambda: "  ").available() is False
    assert _transfer(fake, cookie_check=lambda: "k=v").available() is True

    def missing():
        raise FileNotFoundError("quark_cookies.txt")

    assert _transfer(fake, cookie_check=missing).available() is False


# ---------------------------------------------------------------
# 失败模式：loud fail + 不误删（delete 绝不能在失败时发出）
# ---------------------------------------------------------------

def test_task_risk_control_raises_and_never_deletes(tmp_path):
    fake = FakeDrive(root=[])
    fake.queue_tasks((3, 0))  # 风控/失败状态
    dest = tmp_path / "big.parquet"

    with pytest.raises(transfer.TransferError, match="风控"):
        _transfer(fake).fetch(_item(), dest)

    assert fake.downloads == []
    assert all("/file/delete" not in u for u, _ in fake.calls), "失败不得误删"
    assert not dest.exists()


def test_task_timeout_bounded_and_loud(tmp_path):
    clock = FakeClock()
    fake = FakeDrive()
    fake.queue_tasks((1, 10))
    dest = tmp_path / "big.parquet"

    with pytest.raises(transfer.TransferTimeout, match="超时"):
        _transfer(fake, poll_interval=2.0, poll_timeout=5.0,
                  sleep=clock.sleep, clock=clock).fetch(_item(), dest)

    n_task = sum(1 for u, _ in fake.calls if "/task" in u)
    assert 2 <= n_task <= 4, f"轮询应有界：got {n_task}"
    assert fake.downloads == []
    assert all("/file/delete" not in u for u, _ in fake.calls)


def test_size_mismatch_raises_keeps_copy_and_no_final_file(tmp_path):
    def short_download(url, out, size):
        Path(out).write_bytes(b"x" * (size - 1))
        return True

    fake = FakeDrive(tmp=[{"file_name": "big.parquet", "fid": "OF1",
                           "size": 4096, "dir": False}],
                     download=short_download)
    dest = tmp_path / "big.parquet"

    with pytest.raises(transfer.TransferError, match="size"):
        _transfer(fake).fetch(_item(), dest)

    assert not dest.exists()
    assert not list(tmp_path.rglob("*.part"))
    assert all("/file/delete" not in u for u, _ in fake.calls), "校验不过保留副本待重试"


def test_save_http_error_raises_and_never_deletes(tmp_path):
    fake = FakeDrive(root=[], save_resp=(500, {"status": 500, "message": "risk control"}))
    dest = tmp_path / "big.parquet"

    with pytest.raises(transfer.TransferError, match="risk control"):
        _transfer(fake).fetch(_item(), dest)

    assert all("/file/delete" not in u for u, _ in fake.calls)
    assert not dest.exists()


def test_stale_same_name_diff_size_deleted_before_save(tmp_path):
    fake = FakeDrive(tmp=[{"file_name": "big.parquet", "fid": "OLD",
                           "size": 100, "dir": False}])
    fake.queue_tasks((2, 100), (2, 100), (2, 100))
    dest = tmp_path / "big.parquet"

    _transfer(fake).fetch(_item(), dest)

    del_bodies = [b for u, b in fake.calls if "/file/delete" in u]
    assert del_bodies[0] == {"filelist": ["OLD"], "action_type": 2, "exclude_fids": []}
    assert any("sharepage/save" in u for u, _ in fake.calls), "旧副本删除后要重新转存"
    assert dest.stat().st_size == 4096


def test_cleanup_delete_failure_does_not_fail_downloaded_file(tmp_path, capsys):
    fake = FakeDrive(tmp=[{"file_name": "big.parquet", "fid": "OF1",
                           "size": 4096, "dir": False}],
                     delete_resp=(500, {"status": 500, "message": "delete boom"}))
    dest = tmp_path / "big.parquet"
    logs = []

    _transfer(fake, log=logs.append).fetch(_item(), dest)

    assert dest.stat().st_size == 4096, "下载已成功，清理失败不得反悔"
    assert any("delete" in m.lower() or "删除" in m for m in logs)


def test_list_http_error_raises(tmp_path):
    class Dying(FakeDrive):
        def http(self, url, body=None):
            if "/file/sort" in url:
                self.calls.append((url, body))
                return 403, {"status": 403, "message": "permission denied"}
            return super().http(url, body)

    with pytest.raises(transfer.TransferError, match="permission denied"):
        _transfer(Dying()).fetch(_item(), tmp_path / "big.parquet")


# ---------------------------------------------------------------
# 真网盘实测修正（2026-09-17）：创建响应可直带 fid；新目录可见性滞后；
# 同名冲突（code 23008）视为"已存在"回退列表。
# ---------------------------------------------------------------

def test_create_dir_uses_fid_from_response_without_relist(tmp_path):
    """真网盘 create 响应带 data.fid → 直接采用（列表可见性滞后不阻塞）。"""
    fake = FakeDrive(root=[], create_resp=(200, {"status": 200, "code": 0,
                                                 "data": {"task_id": "TC", "fid": "TMP"}}))
    fake.queue_tasks((2, 100), (2, 100))
    dest = tmp_path / "big.parquet"

    _transfer(fake).fetch(_item(), dest)

    save_body = next(b for u, b in fake.calls if "sharepage/save" in u)
    assert save_body["to_pdir_fid"] == "TMP"
    assert dest.stat().st_size == 4096


def test_create_dir_name_conflict_falls_back_to_list(tmp_path):
    """create 返回 23008 同名冲突（并发/半创建）→ 视为已存在，重查列表继续。"""
    class ConflictFake(FakeDrive):
        def http(self, url, body=None):
            if "/clouddrive/file?" in url:
                self.calls.append((url, body))
                self.root = self.root + [{"file_name": transfer.TMP_DIR_NAME,
                                          "fid": "TMP", "dir": True}]
                return 400, {"status": 400, "code": 23008,
                             "message": "file is doloading[同名冲突]"}
            return super().http(url, body)

    clock = FakeClock()
    fake = ConflictFake(root=[])
    fake.queue_tasks((2, 100), (2, 100))
    dest = tmp_path / "big.parquet"

    _transfer(fake, poll_interval=0.1, sleep=clock.sleep, clock=clock).fetch(_item(), dest)

    assert dest.stat().st_size == 4096
    assert all("/file/delete" in u or "sharepage/save" in u or "/file/sort" in u
               or "/clouddrive/file?" in u or "/task" in u or "/file/download" in u
               for u, _ in fake.calls)


def test_create_dir_then_lagging_visibility_retries_list(tmp_path):
    """无 fid 且列表短时不可见 → 轮询重查（有界）而不是立刻 loud fail。"""
    class LagFake(FakeDrive):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.created = False
            self.sort0 = 0

        def http(self, url, body=None):
            if "/file/sort" in url and "pdir_fid=0" in url:
                self.sort0 += 1
                if self.created and self.sort0 >= 3:
                    return 200, {"status": 200, "data": {"list": [
                        {"file_name": transfer.TMP_DIR_NAME, "fid": "TMP", "dir": True}]}}
            if "/clouddrive/file?" in url:
                self.calls.append((url, body))
                self.created = True
                return 200, {"status": 200, "code": 0, "data": {"task_id": "TC"}}
            return super().http(url, body)

    clock = FakeClock()
    fake = LagFake(root=[])
    fake.queue_tasks((2, 100), (2, 100))
    dest = tmp_path / "big.parquet"

    _transfer(fake, poll_interval=0.1, sleep=clock.sleep, clock=clock).fetch(_item(), dest)

    assert dest.stat().st_size == 4096


def test_quark_pc_transport_sends_client_ua(monkeypatch):
    """实测（2026-09-17）：/file/download 按 UA 判定大小限制（Chrome UA 400 23018；
    官方客户端 UA 200）。生产 transport 必须带客户端 UA。"""
    seen = []

    def fake_http(url, body=None, **kw):
        seen.append((url, body, kw.get("ua")))
        return 200, {"status": 200, "data": []}

    monkeypatch.setattr(transfer.quark_client, "http", fake_http)
    tr = transfer.QuarkPcTransport()
    tr.http("http://x/file/download", {"fids": ["1"]})
    assert seen == [("http://x/file/download", {"fids": ["1"]}, transfer.DRIVE_CLIENT_UA)]
    assert "quark-cloud-drive" in transfer.DRIVE_CLIENT_UA


def test_quark_pc_transport_download_uses_ranged_chunks(monkeypatch, tmp_path):
    """实测（2026-09-17）：整文件 GET 被 CDN 限速 ~100KB/s，Range 分块 ~10MB/s；
    且 Chrome/151 常量 UA 下载被限速（客户端 UA ~8MB/s）。生产 transport 下载
    必须同时带分块 + 客户端 UA。"""
    seen = {}

    def fake_download_file(url, out, size, **kw):
        seen.update(kw)
        Path(out).write_bytes(b"x" * size)
        return True, size

    monkeypatch.setattr(transfer.quark_client, "download_file", fake_download_file)
    ok = transfer.QuarkPcTransport().download("http://u", tmp_path / "o.part", 10)
    assert ok is True
    assert seen == {"chunk_size": transfer.DRIVE_CHUNK_SIZE,
                    "ua": transfer.DRIVE_CLIENT_UA,
                    "connections": transfer.DRIVE_CONNECTIONS}
    assert transfer.DRIVE_CHUNK_SIZE >= 8 << 20
    assert transfer.DRIVE_CONNECTIONS >= 2
