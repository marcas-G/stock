#!/usr/bin/env python3
"""夸克分享 Level2 数据全量下载 v2(250 交易日 × 300 股票 ≈ 7.5 万文件, ~118GB)

树结构: level2_detail/level2按个股压缩--历史数据增加中/{年}/{月}/{日}/{前缀}/{4位组}/{股票}.zip
策略:按天处理 —— 当天 4 个目标前缀并发 list 取 (fid, share_fid_token) ->
      批量 file/download 取直链 -> 8 路并发下载。
      stoken 每 25 分钟刷新一次(刷新只影响后续取 token,已取链接不受影响)。
      断点续传:已存在且 size 匹配的文件跳过;403/412 时刷新链接重试。

用法:python3 quark_download_v2.py [start_idx] [end_idx]   # manifest 按天排序后取切片
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

PWD_ID = "1ae1c55c0a03"
PASSCODE = "QNhy"
# cookie 文件路径可配（默认沿用历史路径）；**懒读**——import 期不碰文件系统
# （R8c：原先模块顶层 open() 让"cookie 不在"的机器连 import 都失败，也无法被测试）
COOKIE_PATH = os.environ.get("QUARK_COOKIE_FILE", "/tmp/quark_cookies.txt")


def _cookies() -> str:
    """读 cookie 串（首尾空白剥掉）。缺失 → FileNotFoundError（不静默空 Cookie）。"""
    with open(COOKIE_PATH, encoding="utf-8") as f:
        return f.read().strip()
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")
HOST_PC = "https://drive-pc.quark.cn/1/clouddrive"
MANIFEST = "manifest_tree_250d.json"
DEST = "quark_downloaded"

PREFIXES = ["00开头", "30开头", "60开头", "68开头"]
STOKEN_TTL = 25 * 60  # 25 分钟刷新一次


def http(url, body=None, retry=3, timeout=60):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://pan.quark.cn/",
        "User-Agent": UA,
        "Origin": "https://pan.quark.cn",
        "Cookie": _cookies(),
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


# ---- stoken 管理 ----
_lock = threading.Lock()
_state = {"stoken": None, "ts": 0}


def get_stoken(force=False):
    now = time.time()
    with _lock:
        if (not force and _state["stoken"]
                and now - _state["ts"] < STOKEN_TTL):
            return _state["stoken"]
        s, r = http(f"{HOST_PC}/share/sharepage/token",
                    {"pwd_id": PWD_ID, "passcode": PASSCODE})
        assert s == 200 and r and r.get("status") == 200, f"token failed: {r}"
        _state["stoken"] = r["data"]["stoken"]
        _state["ts"] = now
        print(f"  [stoken refreshed {time.strftime('%H:%M:%S')}]", flush=True)
        return _state["stoken"]


def detail(stoken, pdir, page=1, size=50):
    url = (f"{HOST_PC}/share/sharepage/detail?ver=2&pwd_id={PWD_ID}"
           f"&stoken={urllib.parse.quote(stoken, safe='')}"
           f"&pdir_fid={pdir}&force=0&_page={page}&_size={size}"
           f"&_fetch_total=1&_sort=")
    s, r = http(url)
    if s != 200 or not r or r.get("status") != 200:
        if r and "非法token" in (r.get("message") or ""):
            return None  # 信号:需要刷新 stoken
        return []
    return (r.get("data") or {}).get("list") or []


def list_all(stoken, pdir):
    items, page = [], 1
    while True:
        lst = detail(stoken, pdir, page, 50)
        if lst is None:
            return None
        if not lst:
            break
        items += lst
        if len(lst) < 50:
            break
        page += 1
        time.sleep(0.05)
    return items


def get_download_urls(stoken, fid_tok_list):
    """批量取下载直链(50/批),返回 {fid: url};dl-guest 链接丢弃"""
    urls = {}
    for i in range(0, len(fid_tok_list), 50):
        chunk = fid_tok_list[i:i + 50]
        body = {"fids": [f for f, _ in chunk], "pwd_id": PWD_ID,
                "stoken": stoken, "fids_token": [t for _, t in chunk]}
        s, r = http(f"{HOST_PC}/file/download?pr=ucpro&fr=pc&uc_param_str=", body)
        if s == 200 and r and r.get("status") == 200:
            for it in r.get("data") or []:
                u = it.get("download_url") or ""
                if u and "dl-guest" not in urllib.parse.urlparse(u).netloc:
                    urls[it["fid"]] = u
        time.sleep(0.3)
    return urls


def download_file(url, out, expect_size):
    headers = {"User-Agent": UA, "Referer": "https://pan.quark.cn/",
               "Cookie": _cookies()}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=300) as r:
        with open(out, "wb") as fh:
            while True:
                chunk = r.read(1024 * 256)
                if not chunk:
                    break
                fh.write(chunk)
    return os.path.getsize(out) == expect_size, os.path.getsize(out)


def day_tokens_and_links(day_entries):
    """取一天内所有文件的下载直链（按 `pdir_fid` 组目录 list 拿 token）。
    返回 ({fid: url}, 缺失列表)"""
    # 收集每个 (前缀,组) 需要 list 的目录:fid 已知,直接 detail 组目录拿 token
    by_group = {}  # group_fid -> [entries]
    for e in day_entries:
        by_group.setdefault(e["pdir_fid"], []).append(e)

    stoken = get_stoken()
    fid_tok = {}
    # 并发 list 各组的目录(拿 share_fid_token)
    group_fids = list(by_group.keys())

    def one_group(gfid):
        items = list_all(stoken, gfid)
        if items is None:
            return None
        return {it["fid"]: it.get("share_fid_token") for it in items}

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(one_group, gfid): gfid for gfid in group_fids}
        for f in as_completed(futs):
            tok = f.result()
            if tok:
                fid_tok.update(tok)
    # 组目录 300 个左右,并发 list 很快

    # 批量取链接
    fid_tok_list = [(e["fid"], fid_tok.get(e["fid"]))
                    for e in day_entries if e["fid"] in fid_tok]
    fid_tok_list = [(f, t) for f, t in fid_tok_list if t]
    urls = get_download_urls(stoken, fid_tok_list)
    missing = [e for e in day_entries
               if e["fid"] not in urls or not urls[e["fid"]]]
    return urls, missing


def main():
    start_idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    end_idx = int(sys.argv[2]) if len(sys.argv) > 2 else 10 ** 9

    with open(MANIFEST, encoding="utf-8") as f:
        manifest = json.load(f)
    days = {}
    for e in manifest:
        days.setdefault(e["date"], []).append(e)
    day_list = sorted(days)
    day_list = day_list[start_idx:end_idx]
    total_files = sum(len(days[d]) for d in day_list)
    total_size = sum(e["size"] for d in day_list for e in days[d])
    print(f"{len(day_list)} 天, {total_files} 文件, {total_size/1e9:.2f} GB "
          f"-> {DEST}", flush=True)

    stoken = get_stoken(force=True)
    ok_total = 0
    t0 = time.time()
    for di, day in enumerate(day_list):
        entries = days[day]
        # 已下载的跳过
        todo = []
        for e in entries:
            sub = f"{DEST}/{day}/{e['code']}"
            out = os.path.join(sub, e["file"])
            if os.path.exists(out) and os.path.getsize(out) == e["size"]:
                ok_total += 1
                continue
            todo.append(e)
        if todo:
            # 组目录 fid 已知,直接取链接(含 token 刷新保护)
            for attempt in range(3):
                urls, missing = day_tokens_and_links(todo)
                if not missing:
                    break
                print(f"  {day}: {len(missing)} 无链接, 刷新 stoken 重试 "
                      f"{attempt+1}/3", flush=True)
                get_stoken(force=True)
                urls, missing = day_tokens_and_links(todo)
                if not missing:
                    break
                time.sleep(3)
            else:
                urls = {}

            # 并行下载
            results = {}
            def dl(e):
                url = urls.get(e["fid"])
                if not url:
                    return e, False, "no link"
                sub = f"{DEST}/{day}/{e['code']}"
                os.makedirs(sub, exist_ok=True)
                out = os.path.join(sub, e["file"])
                for attempt in range(3):
                    try:
                        m, sz = download_file(url, out, e["size"])
                        if m:
                            return e, True, "ok"
                        print(f"  SIZE MISMATCH {day} {e['file']} "
                              f"{sz}/{e['size']}", flush=True)
                        if os.path.exists(out):
                            os.remove(out)
                    except urllib.error.HTTPError as hx:
                        if hx.code in (403, 412) and attempt < 2:
                            # 重新取该文件链接
                            stoken = get_stoken(force=True)
                            toks = list_all(stoken, e["pdir_fid"])
                            if toks:
                                tok = {it["fid"]: it.get("share_fid_token")
                                       for it in toks}.get(e["fid"])
                                if tok:
                                    u2 = get_download_urls(
                                        stoken, [(e["fid"], tok)])
                                    if u2:
                                        url = u2[e["fid"]]
                            print(f"  CDN {hx.code} {day} {e['file']}, "
                                  f"link refreshed", flush=True)
                            time.sleep(1)
                            continue
                        if os.path.exists(out):
                            os.remove(out)
                        return e, False, f"HTTP {hx.code}"
                    except Exception as ex:
                        print(f"  FAIL {day} {e['file']} (try {attempt+1}): "
                              f"{ex}", flush=True)
                        if os.path.exists(out):
                            os.remove(out)
                    time.sleep(1)
                return e, False, "max retries"

            with ThreadPoolExecutor(max_workers=8) as ex:
                futs = {ex.submit(dl, e): e for e in todo}
                for f in as_completed(futs):
                    e, ok, msg = f.result()
                    if ok:
                        ok_total += 1
                    else:
                        print(f"  FAILED {day} {e['file']}: {msg}", flush=True)
        el = time.time() - t0
        done_files = sum(1 for d in day_list[:di + 1]
                         for e in days[d])
        speed = (sum(e["size"] for d in day_list[:di + 1]
                     for e in days[d]) / 1e6) / (el / 60)
        print(f"day {di+1}/{len(day_list)} {day}: ok={ok_total} "
              f"({speed:.0f} MB/min, {el/60:.1f} min elapsed)", flush=True)

    print(f"\nDONE: {ok_total}/{total_files} files, "
          f"{(time.time()-t0)/60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
