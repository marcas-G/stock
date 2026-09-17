"""超限大文件回退：分享转存到自有网盘 → 自取直链下载 → 校验后清副本。

设计/需求：`knowledge/design/workspace/2026-09-16-pan-data-update-design.md` §2.1
（分享直链对超限文件取链 HTTP 400 `download file size limit`；用账号 cookie 走
自有文件路径绕开分享直链上限）。CLI 侧默认启用，`--no-transfer` 关闭。

流程（每步 HTTP 经注入 transport，便于单测断言 URL/body/顺序）：
1. 确保自有根目录下临时目录 `factorlab_tmp` 存在（无则建，轮询 task 到完成）；
2. 临时目录已有同名同 size 副本 → 直接复用（断点重试不重复转存）；
   同名不同 size → 删旧副本后重新转存；
3. `share/sharepage/save` 转存 → 轮询 `/task` 到 status=2；失败/风控/超时 → loud fail；
4. 列临时目录定位自有 fid → `file/download` 取自取直链；
5. 下载到 `<dest>.part`，size 校验通过才 `os.replace`（与 sync 同纪律）；
6. 校验通过后删除自有副本（`keep_copy=True` / `--keep-drive-copy` 时保留）。
   失败路径绝不发删除（不误删），副本留给下次复用。

安全性：只删除本次确认过的 `factorlab_tmp` 内自有 fid；清理失败只告警不回滚已下载文件。
"""
from __future__ import annotations

import os
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Callable

try:
    from quark_download import quark_client
except ModuleNotFoundError:  # 脚本直启（无 conftest 铺路）→ 补 platform/tools 再导入
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from quark_download import quark_client

TMP_DIR_NAME = "factorlab_tmp"
_QUERY = "pr=ucpro&fr=pc&uc_param_str="
# Range 分块大小（R30 实测：同一链接整文件 GET 被 CDN 限速 ~100KB/s，
# Range 分块 ~10MB/s；64MiB ≈ 6s/块 @10MB/s）。
DRIVE_CHUNK_SIZE = 64 << 20
# 并行连接数（慢速节点下 4 连接并行抬高聚合带宽；内存/句柄开销可忽略）。
DRIVE_CONNECTIONS = 4
# 官方客户端 UA（R30 实测）：/file/download 对超限文件按 UA 判定——
# Chrome UA → 400 code 23018 `download file size limit`；客户端 UA → 200 直链。
DRIVE_CLIENT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) quark-cloud-drive/2.5.20 Chrome/100.0.4896.160 "
    "Electron/18.3.5.4-b478491100 Safari/537.36 Channel/pckk_other_ch")


class TransferError(Exception):
    """转存回退失败（转存/轮询/取链/校验）：loud fail，调用方记 failed。"""


class TransferTimeout(TransferError):
    """task 轮询超时（转存/删除/建目录未在 poll_timeout 内完成）。"""


class QuarkPcTransport:
    """生产 transport：`quark_client` 的 http/download/stoken 三件套。

    http 一律带官方客户端 UA（`DRIVE_CLIENT_UA`）——超限文件取链是否被
    size limit（code 23018）按 UA 判定（2026-09-17 实测）。
    """

    def http(self, url, body=None):
        return quark_client.http(url, body, ua=DRIVE_CLIENT_UA)

    def download(self, url, out, size) -> bool:
        ok, _actual = quark_client.download_file(
            url, out, size, chunk_size=DRIVE_CHUNK_SIZE, ua=DRIVE_CLIENT_UA,
            connections=DRIVE_CONNECTIONS)
        return bool(ok)

    def get_stoken(self) -> str:
        return quark_client.get_stoken()


def _task_state(data: dict) -> int:
    try:
        return int(data.get("status"))
    except (TypeError, ValueError):
        return -1


class DriveTransfer:
    """自有网盘转存回退：`fetch(item, dest)` 一步到位（原子落盘 + 校验后清副本）。

    注入面：transport（http/download/get_stoken）、sleep/clock（轮询节奏，测试免等待）、
    cookie_check（`available()` 判定，默认 quark_client.cookies）。
    """

    def __init__(self, transport, *, log: Callable[[str], None] | None = None,
                 keep_copy: bool = False, poll_interval: float = 2.0,
                 poll_timeout: float = 600.0, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 cookie_check: Callable[[], str] | None = None,
                 dir_list_retries: int = 3):
        self.transport = transport
        self.log = log or (lambda _msg: None)
        self.keep_copy = keep_copy
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self._sleep = sleep
        self._clock = clock
        self._cookie_check = cookie_check or quark_client.cookies
        self._dir_list_retries = dir_list_retries

    def available(self) -> bool:
        """cookie 可用（非空）→ 可回退；缺失/空 → False（维持 manual_required）。"""
        try:
            return bool((self._cookie_check() or "").strip())
        except FileNotFoundError:
            return False

    # —— HTTP 原语（URL/body 即 API 契约，单测逐字断言） ——

    def _list_dir(self, fid: str) -> list[dict]:
        url = (f"{quark_client.HOST_PC}/file/sort?{_QUERY}&pdir_fid={fid}"
               f"&_page=1&_size=200&_fetch_total=1"
               f"&_sort=file_type:asc,updated_at:desc")
        status, resp = self.transport.http(url)
        if status != 200 or not resp or resp.get("status") != 200:
            raise TransferError(
                f"列网盘目录失败：HTTP {status} "
                f"{(resp or {}).get('message') or ''}".strip())
        return ((resp.get("data") or {}).get("list")) or []

    def _wait_task(self, task_id: str, what: str) -> None:
        """轮询 `/task` 到 status=2；status∈{3,4} → 失败；超时 → TransferTimeout。"""
        deadline = self._clock() + self.poll_timeout
        url = f"{quark_client.HOST_PC}/task?{_QUERY}&task_id={task_id}&retry_index=0"
        while True:
            status, resp = self.transport.http(url)
            if status != 200 or not resp or resp.get("status") != 200:
                raise TransferError(
                    f"{what} task 轮询失败：HTTP {status} "
                    f"{(resp or {}).get('message') or ''}".strip())
            data = resp.get("data") or {}
            state = _task_state(data)
            if state == 2:
                return
            if state in (3, 4):
                msg = data.get("message") or resp.get("message") or ""
                raise TransferError(
                    f"{what} task 失败（status={state}）：{msg}".strip())
            if self._clock() > deadline:
                raise TransferTimeout(
                    f"{what} task 超时（{self.poll_timeout:g}s，status={state}）："
                    f"task_id={task_id}")
            self._sleep(self.poll_interval)

    def _find_tmp_dir(self) -> str | None:
        for it in self._list_dir("0"):
            if it.get("dir") and it.get("file_name") == TMP_DIR_NAME:
                return it["fid"]
        return None

    @staticmethod
    def _is_name_conflict(resp) -> bool:
        """同名冲突（并发/半创建）：code 23008 或文案「同名冲突」→ 视为已存在。"""
        code = (resp or {}).get("code")
        msg = str((resp or {}).get("message") or "")
        return code == 23008 or "同名冲突" in msg

    def _ensure_tmp_dir(self) -> str:
        """确保临时目录存在：先查；建后优先用响应 fid；否则重查（可见性滞后）。"""
        fid = self._find_tmp_dir()
        if fid:
            return fid
        body = {"pdir_fid": "0", "file_name": TMP_DIR_NAME,
                "dir_path": "", "file_type": 0}
        status, resp = self.transport.http(
            f"{quark_client.HOST_PC}/file?{_QUERY}", body)
        data = (resp or {}).get("data") or {}
        if status == 200 and resp and resp.get("status") == 200:
            task_id = data.get("task_id")
            if task_id:
                self._wait_task(task_id, "创建目录")
            if data.get("fid"):
                return data["fid"]
        elif not self._is_name_conflict(resp):
            raise TransferError(
                f"创建临时目录失败：HTTP {status} "
                f"{(resp or {}).get('message') or ''}".strip())
        for _ in range(self._dir_list_retries):
            fid = self._find_tmp_dir()
            if fid:
                return fid
            self._sleep(self.poll_interval)
        raise TransferError(f"创建后未找到临时目录 {TMP_DIR_NAME}")

    def _find_own(self, dir_fid: str, name: str) -> dict | None:
        for it in self._list_dir(dir_fid):
            if not it.get("dir") and it.get("file_name") == name:
                return it
        return None

    def _save(self, item: dict, to_fid: str) -> str:
        body = {
            "fid_list": [item["fid"]],
            "fid_token_list": [item.get("fid_token") or ""],
            "to_pdir_fid": to_fid,
            "pdir_fid": "0",
            "scenario": "link",
            "stoken": self.transport.get_stoken(),
            "pwd_id": quark_client.PWD_ID,
        }
        status, resp = self.transport.http(
            f"{quark_client.HOST_PC}/share/sharepage/save?{_QUERY}", body)
        if status != 200 or not resp or resp.get("status") != 200:
            raise TransferError(
                f"转存失败：HTTP {status} "
                f"{(resp or {}).get('message') or (resp or {}).get('code') or ''}".strip())
        task_id = (resp.get("data") or {}).get("task_id")
        if not task_id:
            raise TransferError(f"转存响应缺 task_id：{resp}")
        return task_id

    def _own_url(self, fid: str) -> str:
        status, resp = self.transport.http(
            f"{quark_client.HOST_PC}/file/download?{_QUERY}", {"fids": [fid]})
        if status == 200 and resp and resp.get("status") == 200:
            for it in resp.get("data") or []:
                url = it.get("download_url") or ""
                if url:
                    return url
        raise TransferError(
            f"自取直链失败：HTTP {status} "
            f"{(resp or {}).get('message') or ''}".strip())

    def _delete(self, fid: str, what: str) -> None:
        body = {"filelist": [fid], "action_type": 2, "exclude_fids": []}
        status, resp = self.transport.http(
            f"{quark_client.HOST_PC}/file/delete?{_QUERY}", body)
        if status != 200 or not resp or resp.get("status") != 200:
            raise TransferError(
                f"删除副本失败：HTTP {status} "
                f"{(resp or {}).get('message') or ''}".strip())
        task_id = (resp.get("data") or {}).get("task_id")
        if task_id:
            self._wait_task(task_id, "删除")

    # —— 端到端 ——

    @staticmethod
    def _cleanup(part: Path) -> None:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass

    def fetch(self, item: dict, dest: Path) -> None:
        """转存并下载到 `dest`；成功返回 None，任何失败抛 TransferError（副本不删）。

        副本复用：临时目录同名同 size → 不重复转存（上次失败/中断的续跑）。
        """
        name = item["name"]
        size = int(item["size"])
        dest = Path(dest)
        tmp_fid = self._ensure_tmp_dir()
        own = self._find_own(tmp_fid, name)
        if own is not None and int(own.get("size") or 0) != size:
            self._delete(own["fid"], "删除旧副本")
            own = None
        if own is None:
            self._wait_task(self._save(item, tmp_fid), "转存")
            own = self._find_own(tmp_fid, name)
            if own is None:
                raise TransferError(f"转存完成但未在临时目录找到 {name!r}")
            if int(own.get("size") or 0) != size:
                raise TransferError(
                    f"转存后 size 不符：{name!r} got {own.get('size')}, want {size}")
        url = self._own_url(own["fid"])

        part = dest.with_name(dest.name + ".part")
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            ok = bool(self.transport.download(url, part, size))
            actual = part.stat().st_size if part.exists() else -1
            if not ok or actual != size:
                raise TransferError(
                    f"自取直链下载 size 校验失败：{name!r} got {actual}, want {size}")
            os.replace(part, dest)
        except TransferError:
            self._cleanup(part)
            raise
        except Exception as ex:
            self._cleanup(part)
            raise TransferError(f"自取直链下载失败：{name!r} {ex}") from ex
        if not self.keep_copy:
            try:
                self._delete(own["fid"], "删除副本")
            except TransferError as ex:
                self.log(f"WARN 清网盘副本失败（已下载成功，保留副本）：{ex}")
