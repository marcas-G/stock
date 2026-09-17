"""小样实测：分享超限件（367MB parquet）转存自有盘 → 自取直链下载。

用法：platform/.venv/bin/python sample_transfer.py
- 真账号真网盘；cookie 只读仓根 quark_cookies.txt，绝不打印内容。
- 落盘 data/raw/financial/<name>；下载 size 校验通过后删除网盘临时副本。
- 若自有直链仍 400 size limit → TransferError 上抛（停止并报告）。
"""
from __future__ import annotations

import dataclasses
import hashlib
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]  # .../stock
sys.path.insert(0, str(ROOT / "platform" / "tools"))

from pan_update import config, share  # noqa: E402
from pan_update import transfer as xfer  # noqa: E402

NAME = "2026-09-04_financial.parquet"
DEST = config.RAW_ROOT / "financial" / NAME


def main() -> int:
    print(f"target: {NAME}")
    if DEST.exists():
        print(f"pre-existing dest size={DEST.stat().st_size}")
    root_fid = share.find_dir(share._default_listdir, "0", config.SHARE_ROOT_DIR)
    cat_fid = share.find_dir(
        share._default_listdir, root_fid,
        config.CATEGORIES["financials"].share_dir)
    entries = share.iter_category(share._default_listdir, cat_fid)
    item = next(e for e in entries if e.name == NAME)
    print(f"share item: name={item.name} size={item.size} fid={item.fid} "
          f"token_len={len(item.fid_token)}")

    tr = xfer.QuarkPcTransport()
    seen_urls: list[str] = []

    class LoggingTransport:
        def http(self, url, body=None):
            return tr.http(url, body)

        def get_stoken(self):
            return tr.get_stoken()

        def download(self, url, out, size):
            seen_urls.append(url)
            host = url.split("/")[2] if "//" in url else url
            print(f"  own direct link host={host} expect={size}", flush=True)
            t0 = time.monotonic()
            ok = tr.download(url, out, size)
            print(f"  downloaded ok={ok} in {time.monotonic() - t0:.1f}s", flush=True)
            return ok

    transfer = xfer.DriveTransfer(
        LoggingTransport(), log=lambda m: print(f"  {m}", flush=True))
    started = time.monotonic()
    transfer.fetch(dataclasses.asdict(item), DEST)
    elapsed = time.monotonic() - started

    size = DEST.stat().st_size
    sha = hashlib.sha256()
    with open(DEST, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            sha.update(chunk)
    print(f"OK dest={DEST} size={size} elapsed={elapsed:.1f}s")
    print(f"sha256={sha.hexdigest()}")
    print(f"own_links={len(seen_urls)}")
    if size != item.size:
        print("FAIL: size mismatch")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
