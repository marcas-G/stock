"""quark 网盘客户端（R16）：三个入口共用的传输 / 鉴权 / 下载层。

**为什么**：同一套逻辑在 `quark_download_v2` 与 `quark_download_server` 里各写了一份，且已开始
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
import urllib.parse
import urllib.request

# —— 传输层常量（逐字取自原 v2；三处入口共用这一份）——
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")
HOST_PC = "https://drive-pc.quark.cn/1/clouddrive"
PWD_ID = "1ae1c55c0a03"          # 分享页 id（v2 与 server 同值）
PASSCODE = "QNhy"                # 该分享的口令（取 token 时传）
STOKEN_TTL = 25 * 60             # 25 分钟刷新一次（v2 口径）

COOKIE_PATH = os.environ.get("QUARK_COOKIE_FILE", "/tmp/quark_cookies.txt")
_FALLBACK_COOKIE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "quark_cookies.txt")


def cookies() -> str:
    """读 cookie 串（首尾空白剥掉）；两处都缺 → FileNotFoundError（显式，不静默）。"""
    for p in (COOKIE_PATH, _FALLBACK_COOKIE):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return f.read().strip()
    raise FileNotFoundError(
        f"quark cookie 文件不存在：{COOKIE_PATH}（或回退路径 {_FALLBACK_COOKIE}）")


def http(url, body=None, retry=3, timeout=60):
    """→ (status, json_body)。**HTTP 错误也返回（不抛）**：401/403 立即返回给调用方
    触发 stoken 刷新；其它异常按 2s/4s/6s 退避重试。逐字保留 v2/server 原实现语义
    （R16 合并时曾误写成"重试后抛异常"——那会夺走调用方的 401→刷新链路，已回退）。
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://pan.quark.cn/",
        "User-Agent": UA,
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


def download_file(url, out, expect_size):
    """带 Cookie 流式下载；返回 (是否等于期望大小, 实际大小)。"""
    headers = {"User-Agent": UA, "Referer": "https://pan.quark.cn/", "Cookie": cookies()}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=300) as r:
        with open(out, "wb") as fh:
            while True:
                chunk = r.read(1024 * 256)
                if not chunk:
                    break
                fh.write(chunk)
    return os.path.getsize(out) == expect_size, os.path.getsize(out)
