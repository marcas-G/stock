"""下载执行：差集消费 → 取链（size-limit 分类）→ ``.part`` 原子落盘 → state 记账。

设计：knowledge/design/workspace/2026-09-16-pan-data-update-design.md §2.1/§4/§7/§8
- 差集（``state.diff_files``）后只下 to_fetch；同名 size 同 → unchanged 不下（幂等）。
- 分享直链大小上限（HTTP 400 ``download file size limit``）→ manual_required：
  写清单，不 fail 整链；人工放入后下次差集自动接续（§2.1）——就位件按
  「清单存在 + 本地已有 + size 匹配 + state 未登记」登记为 ``adopted``（不计下载，
  CLI 视作新数据清阶段标记）。
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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

try:
    from pan_update import state as st
    from pan_update import transfer as transfer_mod
    from quark_download import quark_client
except ModuleNotFoundError:  # 脚本直启（无 conftest 铺路）→ 补 platform/tools 再导入
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from pan_update import state as st
    from pan_update import transfer as transfer_mod
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
    """downloaded/unchanged/to_fetch/adopted 为 rel_path；manual/failed 为 {name, reason}。

    adopted：人工就位的 manual 大件（分享清单存在 + 本地已有 + size 匹配 + state 未登记）
    ——已视为"有新数据"，但未经下载（T9 修复轮 1）。
    """

    downloaded: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    manual: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    to_fetch: list[str] = field(default_factory=list)
    adopted: list[str] = field(default_factory=list)


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


def _adopt_local(state: dict, category: str, items: list[dict], dest_root: Path,
                 state_path: Path | None, dry_run: bool) -> list[str]:
    """人工就位件登记（设计 §2.1：manual_required 放入 raw 后下次差集自动接续）。

    条件（全满足才 adopted）：分享清单存在、state 未登记、本地已存在且 size 匹配。
    dry_run 只识别不改 state；非 dry_run 逐件 ``_record`` + ``adopted=True`` 原子落盘。
    size 不符不登记（仍走下载；半成品同名件不会被误认）。
    """
    adopted: list[str] = []
    for item in items:
        key = st._state_key(category, item["rel_path"])
        if key in state["files"]:
            continue
        try:
            p = _dest_for(dest_root, item["rel_path"])
        except ValueError:
            continue
        if not p.is_file() or p.stat().st_size != item["size"]:
            continue
        adopted.append(item["rel_path"])
        if not dry_run:
            _record(state, category, item)
            state["files"][key]["adopted"] = True
            if state_path is not None:
                st.save_state_atomic(Path(state_path), state)
    return adopted


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


def _cleanup(part: Path) -> None:
    try:
        part.unlink(missing_ok=True)
    except OSError:
        pass


def _download_one(transport, url: str, item: dict, dest_root: Path) -> str | None:
    """成功返回 None；失败返回 failed 原因（本函数不做 state/manual 记账）。

    线程安全：只触碰本项自己的 ``.part`` 与目标文件，可与其它项并发（I1）。
    """
    try:
        dest = _dest_for(dest_root, item["rel_path"])
    except ValueError as ex:
        return str(ex)
    part = dest.with_name(dest.name + ".part")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        ok = bool(transport.download(url, part, item["size"]))
    except Exception as ex:  # 单文件失败不 fail 整链（设计 §8）
        _cleanup(part)
        return f"download error: {ex}"
    try:
        actual = part.stat().st_size if part.exists() else -1
        if not ok or actual != item["size"]:
            _cleanup(part)  # 半成品不留盘（设计 §8）
            if not ok:
                return "download failed"
            return f"size mismatch: got {actual}, want {item['size']}"
        os.replace(part, dest)
    except OSError as ex:
        _cleanup(part)
        return f"download error: {ex}"
    return None


def _run_downloads(transport, dest_root: Path, jobs: list[tuple[int, dict, str]], workers: int):
    """执行下载任务 → ``[(idx, detail-or-None)]`` 按提交顺序。

    workers<=1 或单任务 → 串行；否则线程池（``map`` 保序）。worker 只做下载+落盘；
    state 记账/report 归集由主线程统一处理（完成序不影响报告条目序）。
    """
    def run(job):
        idx, item, url = job
        return idx, _download_one(transport, url, item, dest_root)

    if workers <= 1 or len(jobs) <= 1:
        return [run(job) for job in jobs]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(run, jobs))


def sync_category(state: dict, category: str, *, entries, transport, dest_root,
                  dry_run: bool = False, workers: int = 8,
                  state_path: Path | None = None, transfer=None) -> SyncReport:
    """下载一个类别的差集 → ``SyncReport``。

    - entries：``share.Entry`` 或已 ``dataclasses.asdict`` 的 dict。
    - transport：``list_urls(items) -> {fid: url}``（超限项在 item 上标
      ``_blocked_reason``，或抛 ``SizeLimitExceeded``）；``download(url, out, size) -> bool``。
    - dry_run：只填 ``to_fetch``，不触网/不落盘；``to_fetch`` 恒有值。
    - workers：下载并发上限（默认 8；1 → 串行）。worker 只做下载落盘，state 记账/
      报告归集由主线程按条目序统一处理（线程安全；完成序不影响报告顺序）。
    - state_path：给定则每个成功文件后 ``state.save_state_atomic``。
    - adopted：人工就位件（清单存在 + 本地已有 + size 匹配 + state 未登记）登记为
      adopted，不计 downloaded；dry-run 只识别不写 state 并从 to_fetch 剔除。
    - transfer：超限（size limit）回退客户端（``available()``/``fetch(item, dest)``，
      即 ``transfer.DriveTransfer``）。可用时超限项走「转存自有盘 → 自取直链」并记
      downloaded；不可用/未提供 → 维持 manual_required。``fetch`` 失败抛
      ``transfer.TransferError`` → 记 failed（loud，副本不删）。
    """
    dest_root = Path(dest_root)
    items = [_as_dict(e) for e in entries]
    adopted = _adopt_local(state, category, items, dest_root, state_path, dry_run)
    diff = st.diff_files(state, category, items)
    if adopted and dry_run:
        aset = set(adopted)
        diff.to_fetch = [e for e in diff.to_fetch if e["rel_path"] not in aset]
    report = SyncReport(
        unchanged=[e["rel_path"] for e in diff.skipped],
        to_fetch=[e["rel_path"] for e in diff.to_fetch],
        adopted=adopted,
    )
    if dry_run or not diff.to_fetch:
        return report

    blocked: dict[str, str] = {}
    urls = _take_urls(transport, diff.to_fetch, blocked)
    jobs: list[tuple[int, dict, str]] = []
    blocked_items: list[tuple[dict, str]] = []
    for idx, item in enumerate(diff.to_fetch):
        name = item["name"]
        if name in blocked:
            blocked_items.append((item, blocked[name]))
            continue
        reason = item.get("_blocked_reason")
        if reason:
            blocked_items.append((item, reason))
            continue
        url = urls.get(item.get("fid"))
        if not url:
            report.failed.append({"name": name, "reason": item.get("_fetch_error") or "no url"})
            continue
        jobs.append((idx, item, url))

    for idx, detail in _run_downloads(transport, dest_root, jobs, workers):
        item = diff.to_fetch[idx]
        if detail is not None:
            report.failed.append({"name": item["name"], "reason": detail})
            continue
        report.downloaded.append(item["rel_path"])
        _record(state, category, item)
        if state_path is not None:
            st.save_state_atomic(Path(state_path), state)

    for item, reason in blocked_items:
        if transfer is None or not transfer.available():
            report.manual.append({"name": item["name"], "reason": reason})
            continue
        try:
            dest = _dest_for(dest_root, item["rel_path"])
        except ValueError as ex:
            report.failed.append({"name": item["name"], "reason": str(ex)})
            continue
        try:
            transfer.fetch(item, dest)
        except transfer_mod.TransferError as ex:
            report.failed.append({"name": item["name"], "reason": f"transfer: {ex}"})
            continue
        report.downloaded.append(item["rel_path"])
        _record(state, category, item)
        if state_path is not None:
            st.save_state_atomic(Path(state_path), state)
    return report


def _is_link_expired(ex: BaseException) -> bool:
    """403/412（含 URLError 包裹）或「链接过期/expired」文案 → 可重取链的过期错误。"""
    if getattr(ex, "code", None) in (403, 412):
        return True
    if getattr(getattr(ex, "reason", None), "code", None) in (403, 412):
        return True
    text = f"{ex}"
    return "过期" in text or "expired" in text.lower()


class QuarkTransport:
    """生产 transport：批量取链（``get_download_urls``，50/批）+ 缺链单项探测。

    批次内有大文件时整批 400（``download file size limit``）会拖掉同批小件链；
    对缺链项逐项重取：拿到链 → 补回（小件可下），400 size limit → ``_blocked_reason``，
    其它 → ``_fetch_error``（由 ``sync_category`` 归入 failed，原因透传）。
    下载遇 403/412/链接过期（典型：链在批次期间过期）→ 对该项重新取链一次再重试；
    仍失败才上抛（reason 含原始异常），不吞错。
    """

    def __init__(self, *, log: Callable[[str], None] | None = None):
        self.log = log
        self._items_by_fid: dict[str, dict] = {}
        self._fid_by_url: dict[str, str] = {}

    def list_urls(self, items: list[dict]) -> dict:
        stoken = quark_client.get_stoken()
        pairs = [(i["fid"], i.get("fid_token") or "") for i in items]
        urls = quark_client.get_download_urls(stoken, pairs, log=self.log)
        for item in items:
            if item["fid"] not in urls:
                self._probe(stoken, item, urls)
        self._items_by_fid.update({i["fid"]: i for i in items})
        self._fid_by_url.update({u: f for f, u in urls.items()})
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
        try:
            ok, _actual = quark_client.download_file(url, out, size)
            return bool(ok)
        except Exception as ex:
            new_url = self._refreshed_url(url, ex)
            try:
                ok, _actual = quark_client.download_file(new_url, out, size)
                return bool(ok)
            except Exception as retry_ex:
                raise RuntimeError(
                    f"重取链后仍失败：{retry_ex}（原错误：{ex}）") from retry_ex

    def _refreshed_url(self, url: str, ex: BaseException) -> str:
        """过期类错误 → 单项重新取链一次，返回新链；其余情况原样上抛。"""
        fid = self._fid_by_url.get(url)
        item = self._items_by_fid.get(fid) if fid else None
        if item is None or not _is_link_expired(ex):
            raise
        fresh: dict[str, str] = {}
        self._probe(quark_client.get_stoken(), item, fresh)
        new_url = fresh.get(fid)
        if not new_url:
            raise
        self._fid_by_url[new_url] = fid
        return new_url
