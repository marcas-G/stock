#!/usr/bin/env python3
"""Prefect runner：把 xscore 流水线的若干配置注册为 deployment，供 UI/CLI 触发。

作为用户级服务运行（`governance/ops/install_prefect_runner.sh`）：
- UI（http://127.0.0.1:4200）→ Deployments → 选 deployment → Run（可覆盖参数）
- CLI: `research/.venv/bin/prefect deployment run 'xscore-m0-split/xscore-m0-split'`
"""
from __future__ import annotations

import os
from pathlib import Path

from prefect import serve

from flows import xscore_pipeline

HERE = Path(__file__).resolve().parent
QR = Path("/data/students/gaolei/quantresearch")

DEPLOYMENTS = [
    ("xscore-m0-split", HERE / "configs/m0-split.yaml",
     "日线/分钟/全量 × M0a，双执行口径 × 双域"),
    ("xscore-quick", HERE / "configs/quick.yaml",
     "最小示例：全 42 × M0a/M0b，open 口径"),
]


def main() -> int:
    os.environ.setdefault("PREFECT_API_URL", "http://127.0.0.1:4200/api")
    deps = []
    for name, cfg, desc in DEPLOYMENTS:
        deps.append(xscore_pipeline.to_deployment(
            name=name,
            description=desc,
            parameters={"config_path": str(cfg)},
            tags=["research", "xscore"],
        ))
    serve(*deps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
