"""4 件超限文件：分享 size vs 本地 size/sha256 + 网盘 factorlab_tmp 清空确认。

用法：platform/.venv/bin/python 10-size-verify.py
- 只读 cookie（经 quark_client），输出不含任何凭据。
- 判定：每件 local_size == share_size（size 校验的独立复核），副本目录应为空。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "platform" / "tools"))

from pan_update import config, share, transfer  # noqa: E402

TARGETS = {
    "daily": ["19910101至20260831A股日k线.zip"],
    "financials": ["个股财务数据_2026-09-04_更新.zip",
                   "财务季报年报_2026-09-04_更新.zip",
                   "2026-09-04_financial.parquet"],
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    root_fid = share.find_dir(share._default_listdir, "0", config.SHARE_ROOT_DIR)
    bad = 0
    for key, names in TARGETS.items():
        cat = config.CATEGORIES[key]
        cat_fid = share.find_dir(share._default_listdir, root_fid, cat.share_dir)
        entries = {e.name: e for e in share.iter_category(share._default_listdir, cat_fid)}
        for name in names:
            e = entries.get(name)
            local = cat.local_root / name
            if e is None:
                print(f"MISSING-IN-SHARE {key}/{name}")
                bad += 1
                continue
            size = local.stat().st_size if local.is_file() else -1
            ok = size == e.size
            bad += 0 if ok else 1
            print(f"{'OK ' if ok else 'FAIL'} {key}/{name} share={e.size} "
                  f"local={size} rel={e.rel_path}")
            if local.is_file():
                print(f"     sha256={sha256(local)}")
    tr = transfer.QuarkPcTransport()
    dt = transfer.DriveTransfer(tr)
    tmp_fid = dt._find_tmp_dir()
    entries = dt._list_dir(tmp_fid) if tmp_fid else []
    print(f"factorlab_tmp fid={tmp_fid} entries="
          f"{[(i.get('file_name'), i.get('size')) for i in entries]}")
    if entries:
        bad += 1
    print("SIZE_VERIFY_OK" if bad == 0 else f"SIZE_VERIFY_FAIL ({bad})")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
