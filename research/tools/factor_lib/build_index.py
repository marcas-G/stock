#!/usr/bin/env python
"""因子索引生成器（R5）：`research/factor/**` → `docs/index/factors.md`。

产物纪律（沿用 `docs/catalog.md` 的范式）：**生成物与生成器输出逐字节一致**，
不一致即门红（`--check` 模式；测试 `tests/test_index.py` 常驻）。

索引内容：
- 按族分组的总表（名字/方向/目标/公式摘要/档案链接）；
- **变体组**：同一公式（归一空白后）出现在 ≥2 个因子时成组列出，并标注各自差异
  （direction / params / process 链）——它们是**研究变体**（同一公式的不同处理或方向假设），
  不是重复条目；归档与否属研究者判断（R5 实测：152 个里 0 个是真重复）。
"""
from __future__ import annotations

import collections
import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[3]        # stock/
FACTOR = ROOT / "research" / "factor"
DOCS = ROOT / "research" / "docs" / "factors"
OUT = ROOT / "docs" / "index" / "factors.md"


def _norm_formula(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _is_meta_path(path) -> bool:
    """下划线前缀文件/目录 = 元数据（非因子 spec）：`_pools/`（R03 分钟覆盖池）等。

    与 `factorlab lint --all` / R04 批量 lint 的 `_` 跳过口径一致——池 YAML 含
    `name` 字段但不是因子，不得进索引/族校验。
    """
    return any(part.startswith("_") for part in path.parts)


def load_specs() -> list[dict]:
    out = []
    for f in sorted(FACTOR.rglob("*.yaml")):
        # `_` 前缀 = 元数据/数据（非因子 spec）：文件与任意路径分量同规
        # （如 `intraday/_pools/bars1m_2024h1.yaml` 是分钟覆盖池，不是因子）
        if any(part.startswith("_") for part in f.relative_to(FACTOR).parts):
            continue
        spec = yaml.safe_load(f.read_text(encoding="utf-8"))
        out.append({
            "name": spec["name"],
            "family": f.parent.name,
            "stem": f.stem,
            "direction": spec.get("direction"),
            "target": spec.get("target", "forward_return_5d"),
            "adjustment": spec.get("adjustment", "qfq"),
            "interface": spec.get("interface", "daily"),
            "desc": (spec.get("description") or "").strip().replace("|", "/"),
            "params": spec.get("params") or {},
            "process": spec.get("process") or [],
            "formula_norm": _norm_formula(spec.get("formula") or ""),
            "yaml": f.relative_to(ROOT).as_posix(),
            "md": (DOCS / f.parent.name / f"{f.stem}.md").relative_to(ROOT).as_posix(),
        })
    return out


def _diff_note(members: list[dict]) -> str:
    """该组成员之间"差在哪"（direction / params / process）——一行为组内公共说明。"""
    dirs = sorted({m["direction"] for m in members})
    params = sorted({str(sorted(m["params"].items())) for m in members})
    procs = sorted({"; ".join(m["process"]) for m in members})
    bits = []
    if len(dirs) > 1:
        bits.append(f"direction {'/'.join(str(d) for d in dirs)}")
    if len(params) > 1:
        bits.append(f"params {len(params)} 种")
    if len(procs) > 1:
        bits.append(f"process {len(procs)} 种")
    return "；".join(bits) or "无字段差异"


def render() -> str:
    specs = load_specs()
    by_fam = collections.defaultdict(list)
    for s in specs:
        by_fam[s["family"]].append(s)
    groups = collections.defaultdict(list)
    for s in specs:
        groups[s["formula_norm"]].append(s)
    variant_groups = {k: v for k, v in groups.items() if len(v) > 1}

    lines: list[str] = []
    lines.append("# 因子索引（自动生成，勿手改）")
    lines.append("")
    lines.append("> 生成器：`research/tools/factor_lib/build_index.py`（`--check` 门：产物必须逐字节一致）。")
    lines.append(f"> 共 **{len(specs)} 个因子** · **{len(by_fam)} 族** · "
                 f"变体组 **{len(variant_groups)}** 组（同公式多假设，非重复）。")
    lines.append("")
    lines.append("## 族总览")
    lines.append("")
    lines.append("| 族 | 文件数 | 目录 |")
    lines.append("|---|---|---|")
    for fam in sorted(by_fam):
        lines.append(f"| {fam} | {len(by_fam[fam])} | `research/factor/{fam}/` |")
    lines.append("")
    for fam in sorted(by_fam):
        lines.append(f"## 族：{fam}")
        lines.append("")
        lines.append("| 因子名 | 方向 | 目标 | 说明 | 档案 | 规格 |")
        lines.append("|---|---|---|---|---|---|")
        for s in sorted(by_fam[fam], key=lambda x: x["name"]):
            desc = (s["desc"][:48] + "…") if len(s["desc"]) > 48 else s["desc"]
            lines.append(f"| `{s['name']}` | {s['direction']} | {s['target'].replace('forward_return_','fwd')} "
                         f"| {desc} | [{s['stem']}.md](../../{s['md']}) | [yaml](../../{s['yaml']}) |")
        lines.append("")
    lines.append("## 变体组（同公式多假设）")
    lines.append("")
    lines.append("同一公式在不同 direction / params / process 下的变体——**研究假设的对照，不是重复条目**；"
                 "归档/合并属研究者判断。")
    lines.append("")
    for formula, members in sorted(variant_groups.items(), key=lambda kv: -len(kv[1])):
        short = formula[:110] + ("…" if len(formula) > 110 else "")
        lines.append(f"### 组（{len(members)} 个）· 差异：{_diff_note(members)}")
        lines.append("")
        lines.append(f"```\n{short}\n```")
        lines.append("")
        lines.append("| 因子名 | 方向 | params | process |")
        lines.append("|---|---|---|---|")
        for m in sorted(members, key=lambda x: x["name"]):
            p = ", ".join(f"{k}={v}" for k, v in sorted(m["params"].items())) or "—"
            pr = "; ".join(m["process"]) or "—"
            lines.append(f"| `{m['name']}` | {m['direction']} | {p} | {pr} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        if not OUT.is_file():
            print(f"索引不存在: {OUT}（先运行 build_index.py 生成）")
            return 1
        cur = OUT.read_text(encoding="utf-8")
        if cur != text:
            print("索引与生成器输出不一致（doc 陈旧或手改）——重跑 build_index.py")
            return 1
        print("索引一致 ✓")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"已生成 {OUT.relative_to(ROOT)}（{len(text.splitlines())} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
