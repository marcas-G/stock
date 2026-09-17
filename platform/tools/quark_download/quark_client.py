"""quark 网盘客户端（R16）：三个入口共用的传输 / 鉴权 / 下载层。

**为什么**：同一套逻辑在 `download_level2` 与 `download_share_dir` 里各写了一份，且已开始
漂移——`http` 只差 cookie 来源与文档串、`get_stoken` 只差 `force`、`get_download_urls`/`download_file`
只差诊断打印；cookie 读取有**两种**口径（v2 走 `QUARK_COOKIE_FILE` 且缺失即抛；server 在 import
期读文件、缺失时**静默空串**）。这里是唯一实现，诊断输出用 `log` 回调表达（缺省静默）。

行为口径（合并时的取舍，逐条写明）：
- cookie：`QUARK_COOKIE_FILE`（缺省 `/tmp/quark_cookies.txt`）→ 回退工具目录旁
  `../quark_cookies.txt`（server 的历史回退）→ **都没有则 FileNotFoundError**（不静默空 Cookie：
  空 Cookie 会被上游当 401/权限问题，掩盖真实原因；R8c 对 v2 已按此口径修过一次）。
- `get_download_urls`：跳过 `dl-guest` 降级链接（下载必 412），保留其余；诊断经 `log`。
- `download_file`：流式 256KB 分块、返回 `(是否等于 expect_size, 实际大小)`。
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# —— 传输层常量（逐字取自原 v2；三处入口共用这一份）——
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")
HOST_PC = "https://drive-pc.quark.cn/1/clouddrive"
PWD_ID = "1ae1c55c0a03"          # 分享页 id（v2 与 server 同值）
PASSCODE = "QNhy"                # 该分享的口令（取 token 时传）
STOKEN_TTL = 25 * 60             # 25 分钟刷新一次（v2 口径）
DEST = "quark_downloaded"        # 下载落点目录名（两入口同值）

COOKIE_PATH = os.environ.get("QUARK_COOKIE_FILE", "/tmp/quark_cookies.txt")
_FALLBACK_COOKIE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "quark_cookies.txt")
# 仓根回退（T9 修复轮 1）：设计/手册的 cookie 单点在 stock/quark_cookies.txt，
# 放在链末（前序顺序不变）：/tmp（或显式 QUARK_COOKIE_FILE）→ tool 目录 → 仓根。
_REPO_FALLBACK_COOKIE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "..", "..", "..", "quark_cookies.txt")

_lock = threading.Lock()
_state: dict = {"stoken": None, "ts": 0.0}


def cookies() -> str:
    """读 cookie 串（首尾空白剥掉）；三处都缺 → FileNotFoundError（显式，不静默）。"""
    for p in (COOKIE_PATH, _FALLBACK_COOKIE, _REPO_FALLBACK_COOKIE):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return f.read().strip()
    raise FileNotFoundError(
        f"quark cookie 文件不存在：{COOKIE_PATH}（或回退路径 {_FALLBACK_COOKIE} / "
        f"{_REPO_FALLBACK_COOKIE}）。请更新仓根 quark_cookies.txt 或设置 "
        f"QUARK_COOKIE_FILE")


def http(url, body=None, retry=3, timeout=60, ua=None):
    """→ (status, json_body)。**HTTP 错误也返回（不抛）**：401/403 立即返回给调用方
    触发 stoken 刷新；其它异常按 2s/4s/6s 退避重试。逐字保留 v2/server 原实现语义
    （R16 合并时曾误写成"重试后抛异常"——那会夺走调用方的 401→刷新链路，已回退）。

    `ua`：可选 User-Agent 覆盖（默认模块 `UA`）。R30 实测：`/file/download` 对超限
    文件按 UA 判定（Chrome UA → 400 code 23018；官方客户端 UA → 200 直链），
    转存回退路径经此传客户端 UA。
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://pan.quark.cn/",
        "User-Agent": ua or UA,
        "Origin": "https://pan.quark.cn",
        "Cookie": cookies(),
    }
    data = json.dumps(body).encode() if body is not None else None
    last = None
    for attempt in range(retry):
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                last = (e.code, json.loads(e.read().decode()))
            except Exception:
                last = (e.code, None)
            if e.code in (401, 403):
                break
        except Exception as ex:
            last = (0, str(ex))
            if attempt == retry - 1:
                break
            time.sleep(2 * (attempt + 1))
    return last if last else (0, None)


def get_stoken(force: bool = False, *, cache: bool = True, ttl: int = STOKEN_TTL):
    """取分享 stoken。`cache=True`（缺省，v2 口径）进程内缓存 ttl 秒；`cache=False`
    （server 口径：每次现取）不走缓存也不上锁。失败即 assert（不吞）。
    """
    now = time.time()
    if not cache:
        return _fetch_stoken()
    with _lock:
        if not force and _state["stoken"] and now - _state["ts"] < ttl:
            return _state["stoken"]
        st = _fetch_stoken()
        _state["stoken"] = st
        _state["ts"] = now
        return st


def _fetch_stoken() -> str:
    s, r = http(f"{HOST_PC}/share/sharepage/token",
                {"pwd_id": PWD_ID, "passcode": PASSCODE})
    assert s == 200 and r and r.get("status") == 200, f"token failed: {r}"
    print(f"  [stoken refreshed {time.strftime('%H:%M:%S')}]", flush=True)
    return r["data"]["stoken"]


def get_download_urls(stoken, fid_tok_list, *, log=None):
    """批量取下载直链（50/批）→ {fid: url}；`dl-guest` 降级链接丢弃（下载必 412）。"""
    def _say(msg):
        if log is not None:
            log(msg)

    urls: dict[str, str] = {}
    total = len(fid_tok_list)
    for i in range(0, total, 50):
        chunk = fid_tok_list[i:i + 50]
        body = {"fids": [f for f, _ in chunk], "pwd_id": PWD_ID, "stoken": stoken,
                "fids_token": [t for _, t in chunk]}
        s, r = http(f"{HOST_PC}/file/download?pr=ucpro&fr=pc&uc_param_str=", body)
        if s == 200 and r and r.get("status") == 200:
            for it in r.get("data") or []:
                u = it.get("download_url") or ""
                if not u:
                    continue
                if "dl-guest" in urllib.parse.urlparse(u).netloc:
                    _say(f"  WARN guest link for {it['fid'][:8]} (will 412)")
                    continue
                urls[it["fid"]] = u
        else:
            _say(f"  batch {i // 50 + 1}: HTTP {s} {(r or {}).get('message', '')}")
        _say(f"  links {min(i + 50, total)}/{total} (got {len(urls)})")
        time.sleep(0.3)
    return urls


def download_file(url, out, expect_size, *, chunk_size=None, ua=None,
                  connections=1):
    """带 Cookie 流式下载；返回 (是否等于期望大小, 实际大小)。

    `chunk_size`：给定则 Range 分块（R30 实测：同一链接整文件 GET 被 CDN 限速
    ~100KB/s，Range 分块 ~10MB/s；分块失败按 2s/4s 退避重试并从断点续拉）。
    服务端忽略 Range（回 HTTP 200 全量）→ 从头重写，避免拼接错位。
    `ua`：可选 User-Agent 覆盖（默认模块 `UA`）；R30 实测 Chrome/151 常量 UA
    下载被 CDN 限速 ~1MB/s，官方客户端 UA ~8MB/s。
    `connections`：>1 且给 `chunk_size` 时多连接并行分块（先 1 字节探测 Range，
    不支持则退回串行分块）；慢速节点下并行可显著抬高聚合带宽。
    """
    headers = {"User-Agent": ua or UA, "Referer": "https://pan.quark.cn/",
               "Cookie": cookies()}
    if chunk_size:
        if connections > 1 and expect_size > chunk_size:
            _download_parallel(url, out, expect_size, chunk_size, headers,
                               connections)
        else:
            _download_ranged(url, out, expect_size, chunk_size, headers)
    else:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=300) as r:
            with open(out, "wb") as fh:
                while True:
                    chunk = r.read(1024 * 256)
                    if not chunk:
                        break
                    fh.write(chunk)
    size = os.path.getsize(out)
    return size == expect_size, size


def _download_parallel(url, out, expect_size, chunk_size, headers, connections):
    """多连接并行 Range：先探测 Range 支持；不支持 → 退回串行分块。"""
    retries = 0
    while True:
        probe = urllib.request.Request(url, headers={**headers, "Range": "bytes=0-0"})
        try:
            with urllib.request.urlopen(probe, timeout=300) as r:
                supported = r.status == 206
            break
        except Exception:
            if retries >= 2:
                raise
            retries += 1
            time.sleep(2 * retries)
    if not supported:
        _download_ranged(url, out, expect_size, chunk_size, headers)
        return
    with open(out, "wb") as fh:
        fh.truncate(expect_size)
    ranges = [(s, min(s + chunk_size, expect_size) - 1)
              for s in range(0, expect_size, chunk_size)]
    with ThreadPoolExecutor(max_workers=connections) as ex:
        for fut in [ex.submit(_fetch_range, url, out, rg, headers)
                    for rg in ranges]:
            fut.result()


def _fetch_range(url, out, rg, headers):
    """单块 Range 下载并 pwrite 到 offset；瞬时失败退避重试（同 offset 续拉）。"""
    start, end = rg
    retries = 0
    while True:
        try:
            req = urllib.request.Request(
                url, headers={**headers, "Range": f"bytes={start}-{end}"})
            with urllib.request.urlopen(req, timeout=300) as r:
                if r.status != 206:
                    raise OSError(f"Range 被忽略（HTTP {r.status}）：{start}-{end}")
                fd = os.open(out, os.O_WRONLY)
                try:
                    pos = start
                    while pos <= end:
                        chunk = r.read(min(1 << 18, end - pos + 1))
                        if not chunk:
                            raise OSError(f"Range 提前结束：{pos} > {end}")
                        os.pwrite(fd, chunk, pos)
                        pos += len(chunk)
                finally:
                    os.close(fd)
            return
        except Exception:
            if retries >= 2:
                raise
            retries += 1
            time.sleep(2 * retries)


def _download_ranged(url, out, expect_size, chunk_size, headers):
    """Range 分块下载：逐块 206；瞬时失败退避重试（同 offset 续拉）。"""
    got, retries = 0, 0
    with open(out, "wb") as fh:
        while got < expect_size:
            end = min(got + chunk_size, expect_size) - 1
            req = urllib.request.Request(
                url, headers={**headers, "Range": f"bytes={got}-{end}"})
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    if r.status == 200:  # 服务端不支持 Range：全量重写
                        fh.seek(0)
                        fh.truncate()
                        got = 0
                        while True:
                            chunk = r.read(1024 * 256)
                            if not chunk:
                                break
                            fh.write(chunk)
                            got += len(chunk)
                        break
                    while True:
                        chunk = r.read(1024 * 256)
                        if not chunk:
                            break
                        fh.write(chunk)
                        got += len(chunk)
                retries = 0
            except Exception:
                if retries >= 2:
                    raise
                retries += 1
                time.sleep(2 * retries)
