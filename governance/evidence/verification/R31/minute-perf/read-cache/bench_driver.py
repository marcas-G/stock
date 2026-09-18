#!/usr/bin/env python
"""R31 读缓存两连跑驱动（与 bench.sh 同口径；仅绕过在途 DQ 读取门）。

背景：`factorlab run` 现默认 dataset="ashare_daily" fail-closed 读取门；在途
DQ-M1 的 history health 尚未覆盖历史窗（2024-03-29 → UNKNOWN），CLI 直跑被门
拒绝（与本优化无关）。本驱动直接调用 CLI 同在的 `execute_run`（`dataset=None`
跳过门，其余参数逐一对齐 bench.sh：--chunk-days 10 --chunk-workers 1
--profile --max-memory 8GB），产出 summary.json（含 runtime.profile）与
profile.json 摘要，供两连跑读段对比。

用法：
    FACTORLAB_READ_CACHE=1 FACTORLAB_READ_CACHE_DIR=<dir> \
    governance/ops/heavy.sh platform/.venv/bin/python bench_driver.py \
        <spec.yaml> <out_dir>
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> None:
    spec_path, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    os.environ.setdefault("FACTORLAB_DATA_BACKEND", "ch")
    os.environ.setdefault("FACTORLAB_ST_DEGRADE", "allow")
    os.environ.setdefault("FACTORLAB_MINUTE_UNCOVERED", "drop")
    from factorlab.surfaces.cli.main import execute_run

    out = execute_run(spec_path, max_memory="8GB", output_dir=out_dir,
                      chunk_days=10, chunk_workers=1, profile=True,
                      dataset=None)
    summary = out["result"].summary
    profile = (summary.get("runtime") or {}).get("profile") or {}
    rec = {
        "spec": str(spec_path),
        "read_cache": os.environ.get("FACTORLAB_READ_CACHE", "1"),
        "read_cache_dir": os.environ.get("FACTORLAB_READ_CACHE_DIR"),
        "total_wall_ms": profile.get("total_wall_ms"),
        "segments": {k: v["wall_ms"] for k, v in profile.get("segments", {}).items()},
        "panel_rows": summary.get("panel_rows"),
        "signal_rows": summary.get("signal_rows"),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "profile.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rec, ensure_ascii=False))


if __name__ == "__main__":
    main()
