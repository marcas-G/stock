#!/usr/bin/env python3
"""夸克分享下载 - 服务器版(全链路打通)
关键发现:下载接口用 file/download?pr=ucpro&fr=pc&uc_param_str= (不是 file/share/download),
CDN 直链下载时需带 Cookie + Referer 头。
流程:token -> 按目录 detail 取 share_fid_token -> 批量 file/download 取 download_url
      -> 带 Cookie 下载到本地。断点续传(size 校验),失败重试。
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

PWD_ID = "1ae1c55c0a03"
PASSCODE = "QNhy"


def _load_cookies():
    for p in ("/tmp/quark_cookies.txt",
              os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "quark_cookies.txt")):
        if os.path.exists(p):
            return open(p).read().strip()
    return ""


COOKIES = _load_cookies()

MANIFEST = "manifest_300.json"
DEST = "quark_downloaded"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")
HOST_PC = "https://drive-pc.quark.cn/1/clouddrive"


def http(url, body=None, retry=3, timeout=60):
    """返回 (status, json_body);HTTP 错误也返回,不抛异常"""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://pan.quark.cn/",
        "User-Agent": UA,
        "Origin": "https://pan.quark.cn",
        "Cookie": COOKIES,
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


def get_stoken():
    s, r = http(f"{HOST_PC}/share/sharepage/token",
                {"pwd_id": PWD_ID, "passcode": PASSCODE})
    assert s == 200 and r and r.get("status") == 200, f"token failed: {r}"
    return r["data"]["stoken"]


def collect_dir_tokens(stoken, dirs):
    """按 pdir_fid 并发(4 路)刷新各目录 share_fid_token,返回 {fid: token}"""
    fid_tok = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(dir_file_tokens, stoken, p): p for p in dirs}
        done = 0
        for f in as_completed(futs):
            pdir = futs[f]
            toks = f.result()
            for e in dirs[pdir]:
                if e["fid"] in toks:
                    fid_tok[e["fid"]] = toks[e["fid"]]
            done += 1
            if done % 100 == 0:
                print(f"  dirs {done}/{len(dirs)}", flush=True)
    return fid_tok


def dir_file_tokens(stoken, pdir_fid):
    """翻页列出目录下所有文件的 fid -> share_fid_token"""
    out = {}
    for page in range(1, 100):
        url = (f"{HOST_PC}/share/sharepage/detail?ver=2&pwd_id={PWD_ID}"
               f"&stoken={urllib.parse.quote(stoken, safe='')}"
               f"&pdir_fid={pdir_fid}&force=0&_page={page}&_size=50"
               f"&_fetch_total=1&_sort=")
        s, r = http(url)
        if s != 200 or not r or r.get("status") != 200:
            return out
        lst = (r.get("data") or {}).get("list")
        if not lst:
            break
        for it in lst:
            out[it["fid"]] = it["share_fid_token"]
        if len(lst) < 50:
            break
    return out


def get_download_urls(stoken, fid_tok_list):
    """批量取下载直链,50 个一批。fid_tok_list: [(fid, token), ...]
    返回 {fid: download_url};若服务端降级返回 dl-guest 链接(下载必 412),
    丢弃这些项(调用方需刷新 stoken 后重取)。"""
    urls = {}
    total = len(fid_tok_list)
    for i in range(0, total, 50):
        chunk = fid_tok_list[i:i + 50]
        body = {"fids": [f for f, _ in chunk], "pwd_id": PWD_ID,
                "stoken": stoken, "fids_token": [t for _, t in chunk]}
        s, r = http(f"{HOST_PC}/file/download?pr=ucpro&fr=pc&uc_param_str=", body)
        if s == 200 and r and r.get("status") == 200:
            for it in r.get("data") or []:
                u = it.get("download_url") or ""
                if not u:
                    continue
                host = urllib.parse.urlparse(u).netloc
                if "dl-guest" in host:
                    print(f"  WARN guest link for {it['fid'][:8]} (will 412)", flush=True)
                    continue
                urls[it["fid"]] = u
        else:
            msg = (r or {}).get("message", "")
            print(f"  batch {i//50+1}: HTTP {s} {msg}", flush=True)
        print(f"  links {min(i+50, total)}/{total} (got {len(urls)})", flush=True)
        time.sleep(0.3)
    return urls


def fresh_download_url(stoken, e):
    """下载 403/412 时:刷新 stoken + 该文件 token,重新取直链。返回 (url, stoken)"""
    stoken = get_stoken()
    toks = dir_file_tokens(stoken, e["pdir_fid"])
    tok = toks.get(e["fid"])
    if tok:
        urls = get_download_urls(stoken, [(e["fid"], tok)])
        if e["fid"] in urls:
            return urls[e["fid"]], stoken
    return None, stoken


def download_file(url, out, expect_size):
    """带 Cookie 下载单个文件,返回 (ok, size)"""
    headers = {"User-Agent": UA, "Referer": "https://pan.quark.cn/",
               "Cookie": COOKIES}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=300) as r:
        with open(out, "wb") as fh:
            while True:
                chunk = r.read(1024 * 256)
                if not chunk:
                    break
                fh.write(chunk)
    return os.path.getsize(out) == expect_size, os.path.getsize(out)


def main():
    if not COOKIES:
        print("cookie file not found at ../quark_cookies.txt")
        sys.exit(1)
    base = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(base, MANIFEST), encoding="utf-8") as f:
        manifest = json.load(f)
    total = sum(e["size"] for e in manifest)
    print(f"{len(manifest)} files, {total/1e9:.2f} GB -> {DEST}")

    stoken = get_stoken()
    print("stoken OK, collecting tokens by directory ...")

    dirs = {}
    for e in manifest:
        dirs.setdefault(e["pdir_fid"], []).append(e)
    fid_tok = collect_dir_tokens(stoken, dirs)
    print(f"tokens ready: {len(fid_tok)}/{len(manifest)}")

    fid_tok_list = [(f, t) for f, t in fid_tok.items()]
    urls = get_download_urls(stoken, fid_tok_list)
    # guest 降级保护:缺链超过 10% 时刷新 stoken 全量重取一轮
    if len(urls) < len(fid_tok_list) * 0.9:
        print(f"guest-degraded links ({len(urls)}/{len(fid_tok_list)}), refreshing once ...")
        stoken = get_stoken()
        fid_tok = collect_dir_tokens(stoken, dirs)
        fid_tok_list = [(f, t) for f, t in fid_tok.items()]
        urls = get_download_urls(stoken, fid_tok_list)
    print(f"links ready: {len(urls)}/{len(manifest)}")

    os.makedirs(DEST, exist_ok=True)
    ok, fail = 0, 0
    start = time.time()
    for e in manifest:
        url = urls.get(e["fid"])
        if not url:
            print(f"  no link: {e['path']}")
            fail += 1
            continue
        sub = f"{DEST}/{e['date']}/{e['code']}"
        os.makedirs(sub, exist_ok=True)
        out = os.path.join(sub, e["file"])
        if os.path.exists(out) and os.path.getsize(out) == e["size"]:
            ok += 1
            continue
        got = False
        for attempt in range(3):
            try:
                m, sz = download_file(url, out, e["size"])
                if m:
                    got = True
                    break
                print(f"  SIZE MISMATCH {e['path']} got {sz} want {e['size']}, retry", flush=True)
                os.remove(out)
            except urllib.error.HTTPError as hx:
                if hx.code in (403, 412) and attempt < 2:
                    print(f"  CDN refused ({hx.code}), re-fetching link {e['file']} ...", flush=True)
                    url, stoken = fresh_download_url(stoken, e)
                    if not url:
                        print(f"    link refresh failed", flush=True)
                        break
                    time.sleep(1)
                    continue
                print(f"  FAIL {e['path']} (try {attempt+1}): {hx}", flush=True)
                if os.path.exists(out):
                    os.remove(out)
            except Exception as ex:
                print(f"  FAIL {e['path']} (try {attempt+1}): {ex}", flush=True)
                if os.path.exists(out):
                    os.remove(out)
            time.sleep(1)
        if got:
            ok += 1
        else:
            fail += 1
        if ok % 200 == 0:
            el = time.time() - start
            print(f"  downloaded {ok}, failed {fail}, elapsed {el/60:.1f} min", flush=True)
    el = time.time() - start
    print(f"done: {ok} ok / {len(manifest)}, fail {fail}, elapsed {el/60:.1f} min")


if __name__ == "__main__":
    main()
