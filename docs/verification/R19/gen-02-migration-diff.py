#!/usr/bin/env python3
"""R21 补存：R19 `02-migration-diff.txt` 的**最小可复现**生成脚本（新侧生产者摘要）。

务必先读（诚实边界）：
- 原生成脚本（stdin 片段）未随仓存档；**旧源 `projects/ashare_alpha3/scripts/0X_*.py`
  迁移后已删除**（仅剩 `__pycache__/*.pyc`）→ 该文件里的 `旧=` 数值与 `-x/+y` diff 统计
  **不可再生成**；本脚本不伪造这些部分。
- 可复现部分：新侧 `import_daily._parse_one` 摘要 + `OUT_SCHEMA` 列序自检。
- 原文件的摘要序列化未记录，无法逐字节重放；本脚本**固定一种显式序列化**并把当前值
  打印出来（存 `02-migration-diff-r21-repro.txt`），供以后迁移对照。
  与 `02-migration-diff.txt` 里 R19 记录的 `新=` 值比较时注意两点：① 序列化方法可能不同；
  ② R21 TOOLS-C2 已改退市分支（in-file `code` 为真、sidecar）语义，600591 值可能变化。
- 只读 `data/raw/daily/`（`data/` 零改动），不写任何路径。

用法：
    platform/.venv/bin/python docs/verification/R19/gen-02-migration-diff.py
"""
from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]                      # stock/
TOOL = ROOT / "research" / "tools" / "ashare_ingest"
PLATFORM_SRC = ROOT / "platform" / "src"
FULL_ZIP = ROOT / "data" / "raw" / "daily" / "19910101至上月底07月31日A股日k线.zip"
FULL_MEMBER = "19910101至上月底A股日k线/600519.xlsx"
DELISTED_XLSX = ROOT / "data" / "raw" / "daily" / "退市股" / "600591_上海航空.xlsx"

sys.path.insert(0, str(PLATFORM_SRC))
sys.path.insert(0, str(TOOL))
import import_daily as ID  # noqa: E402
import pyarrow as pa  # noqa: E402

SERIAL = "sha256(json.dumps(Table.to_pydict(), sort_keys=True, default=str, ensure_ascii=False))[:16]"


def digest(df) -> str:
    """固定序列化（见模块 docstring；原生成脚本的序列化未知，此处显式定义）。"""
    table = pa.Table.from_pandas(df).replace_schema_metadata(None)
    payload = json.dumps(table.to_pydict(), sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def main() -> int:
    print("== R19 02-migration-diff 最小可复现生成（R21 补存）==")
    print(f"摘要序列化：{SERIAL}")
    print()
    zf = zipfile.ZipFile(FULL_ZIP)
    df = ID._parse_one("600519", zf.read(FULL_MEMBER), delisted=False)
    print(f"600519（全量 zip） rows={len(df)} digest={digest(df)}")
    df2 = ID._parse_one("600591", DELISTED_XLSX.read_bytes(), delisted=True)
    codes = sorted(set(df2["code"].tolist()))
    print(f"600591（退市 xlsx） rows={len(df2)} codes={codes} digest={digest(df2)}")
    print()
    cols = [f.name for f in ID.OUT_SCHEMA]
    ok = cols == ID.FINAL_COLS
    print(f"OUT_SCHEMA 列序 == FINAL_COLS：{'✓' if ok else '✗ ' + str(cols)}")
    print()
    print("不可复现（如实声明）：旧侧 `旧=` 数值、-x/+y 结构化 diff —— 旧源已删（仅 .pyc）。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
