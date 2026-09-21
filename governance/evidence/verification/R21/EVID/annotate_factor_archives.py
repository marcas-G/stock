#!/usr/bin/env python3
"""R01-EVID-C1 修复（R21 补存）：给全部因子档案 front matter 加 `snapshot: 历史快照` 标注。

背景：152 份档案 §4 的验证数字来自本地 `platform/results/<name>/summary.json`，该产物
从未随仓存档；当前环境（results/ 空、无平台 duckdb、CH 缺 stock_st）不可复跑。修复
选择**如实标注**而非伪造产物——见 `knowledge/dossiers/factors/README.md` 与
`docs/verification/R21/EVID/C1-not-reproducible.txt`。

R37 Phase 2：档案已随研究产物区迁出主仓——扫描面 = `QUANTRESEARCH_ROOT` 下的
`dossiers/factors/`（env 优先，缺省本机产物区）；root 不存在 → SKIP(0)（GitHub-hosted
干净 checkout 不假绿也不红）。

R40/T10 扩展：`updated_ts >= 2026-09-21`（或 front matter 无 updated_ts）的新/更新档案
必须含合法 `sample_role`（枚举 is/mixed/lockbox/legacy/unknown，权威值域见
`governance/ops/research_tidy.py::SAMPLE_ROLES` 与锁箱台账 G-LOCKBOX）；缺失/非法 →
`--check` 失败。**脚本不代填**该字段（人工/生成器声明）；更早档案 grandfather。

约定：只在 front matter 闭合 `---` 前插入一行 `snapshot:`，不动正文；幂等（已有即跳过）。

用法：
    python3 annotate_factor_archives.py --check   # 校验：缺标注/缺 sample_role 列出并 exit 1
    python3 annotate_factor_archives.py           # 写入 snapshot（幂等；不伪造 sample_role）
"""
from __future__ import annotations

import datetime as dt
import os
import pathlib
import sys

DEFAULT_ROOT = "/data/students/gaolei/quantresearch"
EFFECTIVE_DATE = dt.date(2026, 9, 21)
SAMPLE_ROLES = ("is", "mixed", "lockbox", "legacy", "unknown")


def _research_root() -> pathlib.Path:
    env = os.environ.get("QUANTRESEARCH_ROOT")
    return pathlib.Path(env) if env else pathlib.Path(DEFAULT_ROOT)


DOCS = _research_root() / "dossiers" / "factors"
MARK = "snapshot: 历史快照（验证数字不可复跑；R21 标注，见 ../README.md）"


def annotate(text: str) -> str:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("档案无 front matter")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise ValueError("front matter 未闭合")
    if any(l.startswith("snapshot:") for l in lines[1:end]):
        return text
    return "".join(lines[:end]) + MARK + "\n" + "".join(lines[end:])


def _has_mark(text: str) -> bool:
    lines = text.splitlines()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    return end is not None and any(l.startswith("snapshot:") for l in lines[1:end])


def _front_matter(text: str) -> dict[str, str]:
    """极简 front matter 解析（无 yaml 依赖；无/未闭合 → 空 dict）。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}
    fm: dict[str, str] = {}
    for raw in lines[1:end]:
        line = raw.split("<!--", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip().strip("\"'")
    return fm


def _needs_sample_role(text: str) -> bool:
    """新/更新档案（`updated_ts >= EFFECTIVE_DATE`；无 updated_ts 视为新档）。"""
    ts = _front_matter(text).get("updated_ts", "")
    if not ts:
        return True
    try:
        return dt.date.fromisoformat(ts) >= EFFECTIVE_DATE
    except ValueError:
        return True


def _sample_role_ok(text: str) -> bool:
    return _front_matter(text).get("sample_role", "") in SAMPLE_ROLES


def main() -> int:
    if not DOCS.is_dir():
        print(f"SKIP: 因子档案根不存在：{DOCS}（QUANTRESEARCH_ROOT 未挂载）")
        return 0
    files = sorted(DOCS.glob("*/*.md"))
    check = "--check" in sys.argv
    changed = 0
    for f in files:
        text = f.read_text(encoding="utf-8")
        new = annotate(text)
        if new != text:
            if not check:
                f.write_text(new, encoding="utf-8")
                changed += 1
    texts = {f: f.read_text(encoding="utf-8") for f in files}
    missing = [f for f, t in texts.items() if not _has_mark(t)]
    missing_role = [f for f, t in texts.items()
                    if _needs_sample_role(t) and not _sample_role_ok(t)]
    if check:
        rc = 0
        if missing:
            print(f"缺 snapshot 标注：{len(missing)} 份")
            for f in missing:
                print("  -", f.relative_to(DOCS))
            rc = 1
        if missing_role:
            print(f"缺/非法 sample_role（{EFFECTIVE_DATE.isoformat()} 起生效；"
                  f"允许 {'/'.join(SAMPLE_ROLES)}）：{len(missing_role)} 份")
            for f in missing_role:
                print("  -", f.relative_to(DOCS))
            rc = 1
        if rc == 0:
            print(f"snapshot 标注齐备：{len(files)} 份 ✓（sample_role 齐备）")
        return rc
    print(f"扫描 {len(files)} 份；本次写入 {changed} 份；仍缺 snapshot {len(missing)} 份；"
          f"缺/非法 sample_role {len(missing_role)} 份（脚本不代填）")
    return 0 if not missing and not missing_role else 1


if __name__ == "__main__":
    raise SystemExit(main())
