"""pan_state.json 读写：原子写、损坏隔离、按类别的文件差集。

状态骨架（设计 §3）：{"version": 1, "files": {...}, "stages": {...}, "runs": [...]}
- files 以 ``<category>/<rel_path>`` 为键（rel_path 相对类别根）。
- 差集语义（设计 §4）：新文件 → to_fetch；同名 size 变 → changed 且 to_fetch；
  同名 size 同 → skipped。to_fetch = 新文件 + 变更文件（先新后变更）。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path


def _empty() -> dict:
    return {"version": 1, "files": {}, "stages": {}, "runs": []}


def load_state(path: Path) -> dict:
    if not path.exists():
        return _empty()
    try:
        s = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(s, dict) or "files" not in s:
            raise ValueError("非状态骨架")
        return s
    except (json.JSONDecodeError, ValueError):
        path.replace(path.with_name(f"{path.name}.corrupt-{int(time.time())}"))
        return _empty()


def save_state_atomic(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


@dataclass
class Diff:
    to_fetch: list[dict]
    changed: list[dict]
    skipped: list[dict]


def _state_key(category: str, rel_path: str) -> str:
    prefix = f"{category}/"
    return rel_path if rel_path.startswith(prefix) else prefix + rel_path


def diff_files(state: dict, category: str, entries: list[dict]) -> Diff:
    known = {k: v for k, v in state["files"].items() if k.startswith(f"{category}/")}
    new: list[dict] = []
    changed: list[dict] = []
    skipped: list[dict] = []
    for e in entries:
        prev = known.get(_state_key(category, e["rel_path"]))
        if prev is None:
            new.append(e)
        elif prev.get("size") != e["size"]:
            changed.append(e)
        else:
            skipped.append(e)
    return Diff(to_fetch=new + changed, changed=changed, skipped=skipped)
