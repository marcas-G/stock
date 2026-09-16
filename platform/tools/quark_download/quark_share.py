#!/usr/bin/env python3
"""Quark share: walk directory tree as guest -> local manifest -> filter -> download.
Usage:
  python3 quark_share.py walk <pwd_id> [out_json]
  python3 quark_share.py filter <manifest.json> <regex> [out_json]
  python3 quark_share.py download <filtered.json> <pwd_id> <cookies.txt> [dest_dir]
"""
import concurrent.futures
import datetime
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
import urllib.error

SEARCH_HOST = "https://api-album.quark.cn/api/share/file/search?biz_scene=share-search"
_token_lock = threading.Lock()  # serializes token refresh across threads

HOST = "https://drive-h.quark.cn/1/clouddrive/share/sharepage"
from quark_client import UA  # noqa: E402  （R16：UA 单点；本文件的 http_json 面不同）


def http_json(url, body=None, timeout=30, retries=5):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "Referer": "https://pan.quark.cn/",
        "User-Agent": UA,
    })
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode())
            except Exception:
                raise RuntimeError(f"HTTP {e.code}")
        except Exception as e:  # transient: timeout / ssl / conn reset
            if attempt == retries:
                raise RuntimeError(f"network error after {retries} tries: {e}")
            time.sleep(3 * attempt)
    raise RuntimeError("unreachable")


def get_token(pwd_id, passcode=None):
    body = {"pwd_id": pwd_id}
    if passcode:
        body["passcode"] = passcode
    r = http_json(f"{HOST}/token", body)
    if r.get("status") != 200:
        raise RuntimeError(f"token failed: {r.get('message')} "
                           f"(share needs passcode?)")
    return r["data"]["stoken"]


class Stoken:
    """Holds a share stoken; refreshes on expiry (非法token)."""

    def __init__(self, pwd_id, passcode):
        self.pwd_id = pwd_id
        self.passcode = passcode
        self.value = get_token(pwd_id, passcode)

    def refresh(self):
        self.value = get_token(self.pwd_id, self.passcode)
        return self.value


def list_dir(stoken, pdir_fid="0", page=1, size=50):
    """List one dir page. On 非法token, refresh and retry once."""
    def fetch(s):
        return http_json(
            f"{HOST}/detail?ver=2&pwd_id={stoken.pwd_id}"
            f"&stoken={urllib.parse.quote(s, safe='')}"
            f"&pdir_fid={pdir_fid}&force=0&_page={page}&_size={size}"
            f"&_fetch_total=1&_sort=")
    r = fetch(stoken.value)
    if r.get("status") == 400 and "非法token" in (r.get("message") or ""):
        print("token expired -> refreshing", flush=True)
        stoken.refresh()
        r = fetch(stoken.value)
    if r.get("status") != 200:
        raise RuntimeError(f"detail failed: {r}")
    return r  # full response: data.list + metadata._total


def walk(pwd_id, stoken, out_json, root_name=None, max_pages=2000):
    part_json = out_json + ".part"
    manifest, done_dirs, seen_fids = [], set(), set()
    if os.path.exists(part_json):  # resume from checkpoint
        with open(part_json, encoding="utf-8") as f:
            cp = json.load(f)
        manifest = cp.get("manifest", [])
        done_dirs = set(cp.get("done_dirs", []))
        seen_fids = set(e["fid"] for e in manifest)
        print(f"resume: {len(manifest)} entries, {len(done_dirs)} dirs done",
              flush=True)

    def save_checkpoint():
        tmp = part_json + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"manifest": manifest, "done_dirs": sorted(done_dirs)},
                      f, ensure_ascii=False)
        os.replace(tmp, part_json)

    def visit(pdir_fid, prefix, depth):
        if pdir_fid in done_dirs:
            return
        page = 1
        while True:
            if page > max_pages:
                print(f"WARN: page cap {max_pages} hit at {prefix}", flush=True)
                break
            r = list_dir(stoken, pdir_fid, page, 50)
            data = r.get("data") or {}
            meta = r.get("metadata") or {}
            items = data.get("list") or []
            if not items:
                break
            for it in items:
                fid = it["fid"]
                name = it["file_name"]
                path = f"{prefix}{name}"
                if it.get("dir"):
                    visit(fid, path + "/", depth + 1)
                elif fid not in seen_fids:  # skip files already collected
                    seen_fids.add(fid)
                    manifest.append({"path": path, "fid": fid,
                                     "size": it.get("size", 0),
                                     "dir": False})
            total = meta.get("_total")
            if total is None or page * 50 >= total:
                break
            page += 1
        done_dirs.add(pdir_fid)
        if depth <= 1 or len(manifest) % 500 < 50:
            n_files = sum(1 for e in manifest if not e["dir"])
            print(f"[{len(manifest):6d}] entries, {n_files} files at {prefix!r}",
                  flush=True)
        if depth <= 1:
            save_checkpoint()

    if root_name:
        fid, prefix = "0", ""
        for part in root_name.split("/"):
            r = list_dir(stoken, fid, 1, 50)
            top = r.get("data") or {}
            target = next((it for it in top.get("list") or []
                           if it.get("file_name") == part), None)
            if not target:
                raise RuntimeError(f"dir not found: {part!r} in {prefix!r}")
            fid, prefix = target["fid"], prefix + part + "/"
        visit(fid, prefix, 1)
    else:
        visit("0", "", 0)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    total = sum(e["size"] for e in manifest if not e["dir"])
    print(f"manifest: {len(manifest)} entries -> {out_json}")
    print(f"total file size: {total / 1e9:.2f} GB")


def search_share(stoken, q, page=1, size=50):
    """POST share file search; returns parsed response dict.
    Retries transient service errors (5000109) with backoff."""
    body = {"data": {
        "pwd_id": stoken.pwd_id, "stoken": stoken.value,
        "search_type": "", "page": page, "size": size,
        "filters": [{"value": [q], "key": "q", "channel": "submit",
                     "channel_type": "input", "context": ""}],
        "sort_by": "rel",
    }}
    last = None
    for attempt in range(1, 8):
        r = http_json(SEARCH_HOST, body)
        if r.get("code") == 2000000:
            return r
        if r.get("code") in (4010000, 401) or \
                str(r.get("code", "")).startswith("500"):
            # transient backend / stale token: refresh + backoff
            if attempt in (1, 3, 5):
                print(f"search err {r.get('code')} -> refreshing token",
                      flush=True)
                with _token_lock:
                    stoken.refresh()
                    body["data"]["stoken"] = stoken.value
            time.sleep(4 * attempt)
            last = r
            continue
        raise RuntimeError(f"search failed: {r}")
    raise RuntimeError(f"search failed after retries: {last}")


def do_search(pwd_id, passcode, codes_file, out_json, threads=5, max_pages=60):
    """Search each stock code across all dates; write manifest of zip files.
    Resumes: codes already present in out_json are skipped."""
    codes = []
    with open(codes_file, encoding="utf-8") as f:
        for line in f:
            codes += line.split()
    codes = sorted(set(codes))

    manifest = []
    done_codes = set()
    if os.path.exists(out_json):  # resume
        with open(out_json, encoding="utf-8") as f:
            manifest = json.load(f)
        done_codes = set(e["code"] for e in manifest)
        todo = [c for c in codes if c not in done_codes]
        print(f"resume: {len(manifest)} files, {len(done_codes)}/{len(codes)} "
              f"codes done, {len(todo)} remaining", flush=True)
        codes = todo
    print(f"{len(codes)} codes -> searching all dates (this takes a while)",
          flush=True)

    lock = threading.Lock()
    st = Stoken(pwd_id, passcode)  # ONE shared token; refresh is serialized

    def save_progress():
        tmp = out_json + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1)
        os.replace(tmp, out_json)

    def one(code):
        got = 0
        for page in range(1, max_pages + 1):
            r = search_share(st, code, page, 50)
            fl = ((r or {}).get("data") or {}).get("file_list") or []
            if not fl:
                break
            got += len(fl)
            for f in fl:
                fn = f.get("file_name", "")
                # keep exact <code>.SH/SZ.zip matches only
                if not re.match(rf"^{re.escape(code)}\.(SZ|SH)\.zip$", fn):
                    continue
                dt = datetime.datetime.fromtimestamp(
                    (f.get("created_at") or 0) / 1000)
                manifest.append({
                    "code": code, "file": fn, "path": f"{code}/{fn}",
                    "date": dt.strftime("%Y%m%d"), "size": f.get("size", 0),
                    "fid": f.get("fid"), "created_at": f.get("created_at"),
                    "share_fid_token": f.get("share_fid_token"),
                    "pdir_fid": f.get("pdir_fid"),
                    "pdir_fid_token": f.get("pdir_fid_token"),
                })
            if len(fl) < 50:
                break
            if page % 5 == 0:
                print(f"  {code}: page {page}, {got} hits", flush=True)
        if got == 0:
            print(f"  {code}: NO hits", flush=True)
        else:
            with lock:
                print(f"  {code}: {got} files", flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as ex:
        for _ in ex.map(one, codes):
            with lock:
                save_progress()  # incremental checkpoint

    save_progress()
    total = sum(e["size"] for e in manifest)
    per_code = {}
    for e in manifest:
        per_code[e["code"]] = per_code.get(e["code"], 0) + e["size"]
    print(f"manifest: {len(manifest)} files, {total / 1e9:.2f} GB -> {out_json}")
    print(f"codes with data: {len(per_code)} / {len(codes)}")
    big = sorted(per_code.items(), key=lambda kv: -kv[1])[:10]
    print("largest codes (GB):", [(c, round(s / 1e9, 3)) for c, s in big])


def get_download_links(s, entries, cookies, batch=50):
    """POST share/download for a batch of entries -> dict fid: signed URL.
    Requires valid login cookies (pan.quark.cn session)."""
    url = "https://drive-h.quark.cn/1/clouddrive/file/share/download"
    headers = {
        "Content-Type": "application/json",
        "Referer": "https://pan.quark.cn/",
        "User-Agent": UA,
        "Origin": "https://pan.quark.cn",
        "Cookie": cookies,
    }
    links = {}
    for i in range(0, len(entries), batch):
        part = entries[i:i + batch]
        body = {"fids": [e["fid"] for e in part],
                "pwd_id": s.pwd_id, "stoken": s.value,
                "fids_token": [e.get("share_fid_token") or e.get("pdir_fid_token")
                               for e in part]}
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                resp = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"download link failed: HTTP {e.code}: "
                               f"{e.read().decode()[:300]}")
        data = resp.get("data") or {}
        for item in data.get("task_id_list") or []:
            pass  # newer API returns task ids; check response shape
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, dict) and v.get("download_url"):
                    links[k] = v["download_url"]
        if not links and "list" in data:
            for item in data["list"]:
                if item.get("download_url"):
                    links[item.get("fid")] = item["download_url"]
    return links


def do_download(pwd_id, passcode, manifest_json, cookies_file, dest_dir,
                threads=4):
    """Download all files in a manifest. Saves as <dest>/<date>/<code>.<file>"""
    with open(manifest_json, encoding="utf-8") as f:
        manifest = json.load(f)
    cookies = open(cookies_file).read().strip()
    s = Stoken(pwd_id, passcode)
    total = sum(e["size"] for e in manifest)
    print(f"{len(manifest)} files, {total / 1e9:.2f} GB -> {dest_dir}",
          flush=True)
    links = get_download_links(s, manifest, cookies)
    print(f"got {len(links)} download links", flush=True)
    os.makedirs(dest_dir, exist_ok=True)
    ok = fail = 0
    for e in manifest:
        url = links.get(e["fid"])
        if not url:
            print(f"NO LINK {e['path']}", flush=True)
            fail += 1
            continue
        sub = f"{dest_dir}/{e['date']}/{e['code']}"
        os.makedirs(sub, exist_ok=True)
        out = f"{sub}/{e['file']}"
        if os.path.exists(out) and os.path.getsize(out) == e["size"]:
            ok += 1
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r:
                with open(out, "wb") as fh:
                    fh.write(r.read())
            ok += 1
            if ok % 200 == 0:
                print(f"  {ok} files done", flush=True)
        except Exception as ex:
            print(f"FAIL {e['path']}: {ex}", flush=True)
            fail += 1
    print(f"done: {ok} ok, {fail} failed")


def do_filter(manifest_json, pattern, out_json):
    rx = re.compile(pattern)
    with open(manifest_json, encoding="utf-8") as f:
        manifest = json.load(f)
    hits = [e for e in manifest if rx.search(e["path"])]
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(hits, f, ensure_ascii=False, indent=1)
    total = sum(e["size"] for e in hits if not e["dir"])
    print(f"matched: {len(hits)} entries ({total / 1e9:.2f} GB) -> {out_json}")
    for e in hits[:30]:
        print(f"  {e['path']}  {e['size'] / 1e6:.1f} MB")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "walk"
    if cmd == "walk":
        pwd_id = sys.argv[2]
        passcode = None
        for i, a in enumerate(sys.argv):
            if a == "--pwd" and i + 1 < len(sys.argv):
                passcode = sys.argv[i + 1]
        root = None
        for i, a in enumerate(sys.argv):
            if a == "--root" and i + 1 < len(sys.argv):
                root = sys.argv[i + 1]
        walk(pwd_id, Stoken(pwd_id, passcode),
             sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else "manifest.json",
             root_name=root)
    elif cmd == "search":
        pwd_id = sys.argv[2]
        passcode = None
        for i, a in enumerate(sys.argv):
            if a == "--pwd" and i + 1 < len(sys.argv):
                passcode = sys.argv[i + 1]
        out = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else "manifest_search.json"
        codes_file = sys.argv[4] if len(sys.argv) > 4 and not sys.argv[4].startswith("--") else "codes.txt"
        do_search(pwd_id, passcode, codes_file, out)
    elif cmd == "filter":
        do_filter(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "filtered.json")
    elif cmd == "download":
        pwd_id = sys.argv[2]
        passcode = None
        for i, a in enumerate(sys.argv):
            if a == "--pwd" and i + 1 < len(sys.argv):
                passcode = sys.argv[i + 1]
        manifest_json = sys.argv[3]
        cookies_file = sys.argv[4]
        dest = sys.argv[5] if len(sys.argv) > 5 else "downloaded"
        do_download(pwd_id, passcode, manifest_json, cookies_file, dest)
    else:
        print(__doc__)
        sys.exit(1)
