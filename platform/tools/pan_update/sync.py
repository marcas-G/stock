"""下载执行：差集消费 → 取链（size-limit 分类）→ ``.part`` 原子落盘 → state 记账。

设计：knowledge/design/workspace/2026-09-16-pan-data-update-design.md §2.1/§4/§7/§8
- 差集（``state.diff_files``）后只下 to_fetch；同名 size 同 → unchanged 不下（幂等）。
- 分享直链大小上限（HTTP 400 ``download file size limit``）→ manual_required：
  写清单，不 fail 整链；人工放入后下次差集自动接续（§2.1）。
- 半成品防护（§8）：先写 ``<rel_path>.part``，size 校验通过才 ``os.replace``；
  state 只在成功文件上写入，给定 ``state_path`` 时逐成功文件原子落盘（断点续跑）。
- dry_run 只返回清单（``to_fetch`` 恒有值），不取链、不落盘（§7）。
- 测试经 fake transport 离线运行；生产 ``QuarkTransport`` 包装 quark_client。
"""
from __future__ import annotations

import dataclasses
import datetime
import os
import sys
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

try:
    from pan_update import state as st
    from quark_download import quark_client
except ModuleNotFoundError:  # 脚本直启（无 conftest 铺路）→ 补 platform/tools 再导入
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from pan_update import state as st
    from quark_download import quark_client


class SizeLimitExceeded(Exception):
    """取链被分享直链大小上限拒绝（HTTP 400 ``download file size limit``）。

    transport 协议备选：``list_urls`` 可整体抛本异常；``name`` 是超限项名。
    """

    def __init__(self, name: str, reason: str = "size limit"):
        super().__init__(reason)
        self.name = name
        self.reason = reason


@dataclass
class SyncReport:
    """downloaded/unchanged/to_fetch 为 rel_path；manual/failed 为 {name, reason}。"""

    downloaded: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    manual: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    to_fetch: list[str] = field(default_factory=list)


def _as_dict(entry) -> dict:
    """消费边界：T2 的 ``share.Entry`` dataclass → dict（Plan P T2 接口裁决）。"""
    return entry if isinstance(entry, dict) else dataclasses.asdict(entry)


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _dest_for(dest_root: Path, rel_path: str) -> Path:
    dest = (dest_root / rel_path).resolve()
    if not dest.is_relative_to(dest_root.resolve()):
        raise ValueError(f"rel_path 越界：{rel_path!r}")
    return dest


def _record(state: dict, category: str, item: dict) -> None:
    state["files"][st._state_key(category, item["rel_path"])] = {
        "name": item["name"],
        "size": item["size"],
        "fid": item.get("fid"),
        "synced_at": _now(),
    }


def _take_urls(transport, items: list[dict], blocked: dict[str, str]) -> dict:
    """取链；``SizeLimitExceeded(name)`` → 摘除该项后对余项重取（有界，不吞未知异常）。"""
    remaining = list(items)
    urls: dict[str, str] = {}
    while remaining:
        try:
            urls.update(transport.list_urls(remaining) or {})
            break
        except SizeLimitExceeded as ex:
            hit = next((e for e in remaining if e["name"] == ex.name), None)
            if hit is None:
                raise
            remaining = [e for e in remaining if e is not hit]
            blocked[ex.name] = ex.reason
    return urls


def _download_one(transport, url: str, item: dict, dest_root: Path) -> str | None:
    """成功返回 None；失败返回 failed 原因（本函数不做 state/manual 记账）。"""
    try:
        dest = _dest_for(dest_root, item["rel_path"])
    except ValueError as ex:
        return str(ex)
    part = dest.with_name(dest.name + ".part")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        ok = bool(transport.download(url, part, item["size"]))
    except Exception as ex:  # 单文件失败不 fail 整链（设计 §8）
        part.unlink(missing_ok=True)
        return f"download error: {ex}"
    actual = part.stat().st_size if part.exists() else -1
    if not ok or actual != item["size"]:
        part.unlink(missing_ok=True)  # 半成品不留盘（设计 §8）
        if not ok:
            return "download failed"
        return f"size mismatch: got {actual}, want {item['size']}"
    os.replace(part, dest)
    return None


def sync_category(state: dict, category: str, *, entries, transport, dest_root,
                  dry_run: bool = False, workers: int = 8,
                  state_path: Path | None = None) -> SyncReport:
    """下载一个类别的差集 → ``SyncReport``。

    - entries：``share.Entry`` 或已 ``dataclasses.asdict`` 的 dict。
    - transport：``list_urls(items) -> {fid: url}``（超限项在 item 上标
      ``_blocked_reason``，或抛 ``SizeLimitExceeded``）；``download(url, out, size) -> bool``。
    - dry_run：只填 ``to_fetch``，不触网/不落盘；``to_fetch`` 恒有值。
    - workers：并发上限（计划 §3 建议 8）；当前按文件串行接线，参数留给并发改造。
    - state_path：给定则每个成功文件后 ``state.save_state_atomic``。
    """
    dest_root = Path(dest_root)
    items = [_as_dict(e) for e in entries]
    diff = st.diff_files(state, category, items)
    report = SyncReport(
        unchanged=[e["rel_path"] for e in diff.skipped],
        to_fetch=[e["rel_path"] for e in diff.to_fetch],
    )
    if dry_run or not diff.to_fetch:
        return report

    blocked: dict[str, str] = {}
    urls = _take_urls(transport, diff.to_fetch, blocked)
    for item in diff.to_fetch:
        name = item["name"]
        if name in blocked:
            report.manual.append({"name": name, "reason": blocked[name]})
            continue
        reason = item.get("_blocked_reason")
        if reason:
            report.manual.append({"name": name, "reason": reason})
            continue
        url = urls.get(item.get("fid"))
        if not url:
            report.failed.append({"name": name, "reason": item.get("_fetch_error") or "no url"})
            continue
        outcome = _download_one(transport, url, item, dest_root)
        if outcome is not None:
            report.failed.append({"name": name, "reason": outcome})
            continue
        report.downloaded.append(item["rel_path"])
        _record(state, category, item)
        if state_path is not None:
            st.save_state_atomic(Path(state_path), state)
    return report


class QuarkTransport:
    """生产 transport：批量取链（``get_download_urls``，50/批）+ 缺链单项探测。

    批次内有大文件时整批 400（``download file size limit``）会拖掉同批小件链；
    对缺链项逐项重取：拿到链 → 补回（小件可下），400 size limit → ``_blocked_reason``，
    其它 → ``_fetch_error``（由 ``sync_category`` 归入 failed，原因透传）。
    """

    def __init__(self, *, log: Callable[[str], None] | None = None):
        self.log = log

    def list_urls(self, items: list[dict]) -> dict:
        stoken = quark_client.get_stoken()
        pairs = [(i["fid"], i.get("fid_token") or "") for i in items]
        urls = quark_client.get_download_urls(stoken, pairs, log=self.log)
        for item in items:
            if item["fid"] not in urls:
                self._probe(stoken, item, urls)
        return urls

    def _probe(self, stoken: str, item: dict, urls: dict) -> None:
        body = {
            "fids": [item["fid"]],
            "pwd_id": quark_client.PWD_ID,
            "stoken": stoken,
            "fids_token": [item.get("fid_token") or ""],
        }
        url = f"{quark_client.HOST_PC}/file/download?pr=ucpro&fr=pc&uc_param_str="
        status, resp = quark_client.http(url, body)
        if status == 200 and resp and resp.get("status") == 200:
            for it in resp.get("data") or []:
                link = it.get("download_url") or ""
                if not link:
                    continue
                if "dl-guest" in urllib.parse.urlparse(link).netloc:
                    item["_fetch_error"] = "dl-guest 降级链接（下载必 412）"
                    return
                urls[item["fid"]] = link
                return
        msg = ((resp or {}).get("message") or "").strip()
        if "size limit" in msg.lower():
            item["_blocked_reason"] = "size limit"
        else:
            item["_fetch_error"] = f"HTTP {status} {msg}".strip()

    def download(self, url: str, out, size: int) -> bool:
        ok, _actual = quark_client.download_file(url, out, size)
        return bool(ok)
