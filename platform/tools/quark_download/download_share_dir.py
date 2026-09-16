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
from quark_client import (DEST, HOST_PC, PWD_ID, UA, STOKEN_TTL,  # noqa: E402  （R16：共享客户端）
                          cookies, download_file, get_download_urls,
                          get_stoken, http)

MANIFEST = "manifest_300.json"







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




def fresh_download_url(stoken, e):
    """下载 403/412 时:刷新 stoken + 该文件 token,重新取直链。返回 (url, stoken)"""
    stoken = get_stoken(cache=False)   # server 口径：每次现取
    toks = dir_file_tokens(stoken, e["pdir_fid"])
    tok = toks.get(e["fid"])
    if tok:
        urls = get_download_urls(stoken, [(e["fid"], tok)])
        if e["fid"] in urls:
            return urls[e["fid"]], stoken
    return None, stoken




def main():
    # R01-STRAT-C3: 原 `if not COOKIES` 引用未定义名（入口 NameError 死代码，从未可用）。
    # cookie 解析收敛到共享客户端 quark_client（缺失即 FileNotFoundError，不静默空串）。
    try:
        cookies()
    except FileNotFoundError as e:
        print(f"cookie file not found: {e}")
        sys.exit(1)
    base = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(base, MANIFEST), encoding="utf-8") as f:
        manifest = json.load(f)
    total = sum(e["size"] for e in manifest)
    print(f"{len(manifest)} files, {total/1e9:.2f} GB -> {DEST}")

    stoken = get_stoken(cache=False)   # server 口径：每次现取
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
        stoken = get_stoken(cache=False)   # server 口径：每次现取
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
