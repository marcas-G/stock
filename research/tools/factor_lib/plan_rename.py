#!/usr/bin/env python
"""R5 改名计划器：读 factor/_families.yaml + factor/*.yaml，产出 rename-map（old→new）。

只打印/写 TSV，**不移动文件**（移动由调用方 `git mv` 执行，便于逐条审计与反向回退）。
规则：`factor/<族>/<stem>.yaml`，stem = name 去掉族前缀；无族可归 → `misc/`（stem=name）。
"""
from __future__ import annotations

import pathlib
import sys

import yaml

from quantresearch_paths import DOCS_FACTORS, FACTOR, ROOT

DOCS = DOCS_FACTORS


def load_families() -> list[tuple[str, tuple[str, ...]]]:
    fam = yaml.safe_load((FACTOR / "_families.yaml").read_text(encoding="utf-8"))["families"]
    out = []
    for name, prefixes in fam.items():
        out.append((name, tuple(prefixes or ())))
    return out


def plan() -> list[dict]:
    families = load_families()
    rows = []
    for f in sorted(FACTOR.glob("*.yaml")):
        if f.name.startswith("_"):
            continue
        spec = yaml.safe_load(f.read_text(encoding="utf-8"))
        name = spec["name"]
        fam, stem = None, name
        for fam_name, prefixes in families:
            for p in prefixes:
                if name == p or name.startswith(p + "_"):
                    fam, stem = fam_name, (name[len(p) + 1:] if name != p else name)
                    break
            if fam:
                break
        if fam is None:
            fam, stem = "misc", name
        if not stem or stem == name and fam != "misc":
            stem = name
        rows.append({
            "_fam": fam, "_stem": stem,
            "name": name,
            "family": fam,
            "stem": stem,
            "yaml_old": f.relative_to(ROOT).as_posix(),
            "yaml_new": (pathlib.Path("factor") / fam / f"{stem}.yaml").as_posix(),
            "md_old": (DOCS / f"{name}.md").relative_to(ROOT).as_posix(),
            "md_new": (DOCS / fam / f"{stem}.md").relative_to(ROOT).as_posix(),
        })
    # 同族内 stem 必须唯一：冲突组**回退为全名**（去前缀是为可读，不是为正确性）
    import collections
    seen = collections.Counter((r["_fam"], r["_stem"]) for r in rows)
    for r in rows:
        if seen[(r["_fam"], r["_stem"])] > 1:
            r["stem"] = r["name"]
            r["yaml_new"] = (pathlib.Path("factor") / r["_fam"] / f'{r["name"]}.yaml').as_posix()
            r["md_new"] = (DOCS / r["_fam"] / f'{r["name"]}.md').relative_to(ROOT).as_posix()
    for r in rows:
        r.pop("_fam", None); r.pop("_stem", None)
    return rows


def main() -> int:
    rows = plan()
    if "--tsv" in sys.argv:
        print("name\tfamily\tstem\tyaml_old\tyaml_new\tmd_old\tmd_new")
        for r in rows:
            print("\t".join(r[k] for k in ("name", "family", "stem", "yaml_old", "yaml_new", "md_old", "md_new")))
        return 0
    import collections
    cnt = collections.Counter(r["family"] for r in rows)
    print(f"因子 {len(rows)} 个 → {len(cnt)} 族")
    for fam, n in cnt.most_common():
        print(f"  {fam:22s} {n:>3}")
    dup = [r for r in rows if not (ROOT / r["md_old"]).is_file()]
    print(f"  缺档案的因子: {len(dup)}（应为 0；模板除外）")
    for r in dup[:5]:
        print("    !", r["name"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
