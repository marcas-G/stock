"""参考库（Reference Library，D10）：`research/factor/_reference.yaml` 加载器。

权威定义：`knowledge/design/platform/specs/2026-09-16-factorlab-eval-metrics-v2-design.md`
§3b——库级分析（corr/svd/resic）默认对**择优最小库**（不拿全局因子对照）；库按
**信号来源**分 `scales: daily / minute`（两类语义不同、不混用对照）；`--target` 是
评估参数而非库分组。

禁止行为（spec §3b）：`--against reference` 只读本文件的对应 `scales` 组——
不得扫描全库（`ParquetPanelStore.list_factors`）、不得跨 scales 取对照。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

REFERENCE_SCALES: tuple[str, ...] = ("daily", "minute")
ENV_OVERRIDE = "FACTORLAB_REFERENCE"


@dataclass(frozen=True)
class ReferenceEntry:
    """参考库成员：`name / style / reason / added / entry_corr_max / entry_resic_t`（spec §3b）。"""
    name: str
    scales: str
    style: str
    reason: str
    added: str
    entry_corr_max: float | None = None
    entry_resic_t: float | None = None


def default_reference_path() -> Path:
    """参考库路径：`FACTORLAB_REFERENCE` 覆盖优先，否则仓库根 `research/factor/_reference.yaml`。"""
    env = os.environ.get(ENV_OVERRIDE)
    if env:
        return Path(env)
    # app/analysis/reference.py → parents[5] = stock/（与 config._REPO_ROOT 同根）
    return (Path(__file__).resolve().parents[5]
            / "research" / "factor" / "_reference.yaml")


def load_reference(path: Path | None = None) -> dict[str, list[ReferenceEntry]]:
    """读 `_reference.yaml` → {scales: [ReferenceEntry,...]}。

    - 顶层必须有 `scales` 映射，键只能是 daily/minute（未知 scales → ValueError）；
    - 每项必须有 name/style/reason/added（空 → ValueError）；同 scales 内 name 重复
      → ValueError（近亲/重复登记会污染对照集）；
    - 缺省 scales 组返回空列表（初始 minute 组可为空）。
    """
    p = Path(path) if path is not None else default_reference_path()
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    scales_map = raw.get("scales")
    if not isinstance(scales_map, dict):
        raise ValueError(f"参考库 {p} 缺顶层 `scales` 映射")
    unknown = set(scales_map) - set(REFERENCE_SCALES)
    if unknown:
        raise ValueError(
            f"参考库 {p} 含未知 scales {sorted(unknown)}（只允许 {list(REFERENCE_SCALES)}）")
    out: dict[str, list[ReferenceEntry]] = {s: [] for s in REFERENCE_SCALES}
    for scales in REFERENCE_SCALES:
        seen: set[str] = set()
        for item in scales_map.get(scales) or []:
            if not isinstance(item, dict) or not item.get("name"):
                raise ValueError(f"参考库 {p} 的 {scales} 项缺 name: {item!r}")
            missing = [k for k in ("style", "reason", "added") if not item.get(k)]
            if missing:
                raise ValueError(
                    f"参考库 {p} 项 {item['name']} 缺字段 {missing}（spec §3b 必填）")
            name = str(item["name"])
            if name in seen:
                raise ValueError(f"参考库 {p} 的 {scales} 组 name 重复: {name}")
            seen.add(name)
            out[scales].append(ReferenceEntry(
                name=name, scales=scales, style=str(item["style"]),
                reason=str(item["reason"]), added=str(item["added"]),
                entry_corr_max=item.get("entry_corr_max"),
                entry_resic_t=item.get("entry_resic_t"),
            ))
    return out


def reference_names(scales: str = "daily", path: Path | None = None) -> list[str]:
    """对应 scales 组的成员名（保持 yaml 文件序——种子在前）。"""
    if scales not in REFERENCE_SCALES:
        raise ValueError(f"未知 scales: {scales}（支持 {list(REFERENCE_SCALES)}）")
    return [e.name for e in load_reference(path)[scales]]
