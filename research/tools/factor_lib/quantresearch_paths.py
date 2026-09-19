#!/usr/bin/env python
"""研究产物区（quantresearch）路径单点（R37 Phase 2）。

工具侧不依赖 factorlab；与平台侧 `factorlab.config.settings.research_root` 同款解析
（目录公约 §5 路径单点纪律）：

- `QUANTRESEARCH_ROOT` env 优先；
- 缺省 `/data/students/gaolei/quantresearch`（本机事实；GitHub-hosted 无该目录）。

派生（研究产物区内的相对布局，迁移前 `stock/` 内的旧坐标在注释中保留对照）：

    factor/         ← 旧 research/factor/
    strategy/       ← 旧 research/strategy/
    composites/     ← 旧 research/composites/
    dossiers/       ← 旧 knowledge/dossiers/
    index/          ← 旧 knowledge/index/（生成物，禁手改）
"""
from __future__ import annotations

import os
from pathlib import Path

ENV = "QUANTRESEARCH_ROOT"
DEFAULT_ROOT = Path("/data/students/gaolei/quantresearch")


def resolve_root(cli_value: str | os.PathLike[str] | None = None) -> Path:
    """根解析：显式参数 > env > 缺省（与 governance/ops/research_tidy.py 同款）。"""
    if cli_value:
        return Path(cli_value)
    env = os.environ.get(ENV)
    if env:
        return Path(env)
    return DEFAULT_ROOT


ROOT = resolve_root()
FACTOR = ROOT / "factor"
STRATEGY = ROOT / "strategy"
COMPOSITES = ROOT / "composites"
COMPOSITE_SPECS = COMPOSITES / "specs"
DOSSIERS = ROOT / "dossiers"
DOCS_FACTORS = DOSSIERS / "factors"
DOCS_STRATEGIES = DOSSIERS / "strategies"
DOCS_COMPOSITES = DOSSIERS / "composites"
INDEX = ROOT / "index"
