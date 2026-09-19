#!/usr/bin/env python
"""合成分索引生成器（Plan CX-C3）：`<research_root>/composites/specs/*.yaml`
+ `<research_root>/dossiers/composites/*.md` → `<research_root>/index/composites.md`。

R37 Phase 2：研究产物区根 = `QUANTRESEARCH_ROOT`（解析单点见 `quantresearch_paths.py`；
合成分 spec/实现随迁，entrypoint 改为产物区内相对模块路径 `composites.implementations.*`）。

产物纪律（与因子/策略索引同款）：生成物与生成器输出**逐字节一致**，
不一致即门红（`--check`；测试 `tests/test_composite_index.py` 常驻）。

成对门（不是静默跳过）：
- 每个 `composites/specs/<stem>.yaml` 必须有 `dossiers/composites/<stem>.md` 档案；
- 每个带 front matter 的档案必须声明 `spec`（= `composites/specs/<stem>.yaml`）
  与 `window`（样本窗口），且 spec 存在；
- 缺任一 → 非零退出并列出名字。

成员顺序契约（design §3）：spec `members` 声明顺序 = 矩阵列顺序——
索引原序呈现，**不做** alphabetical/hash sort（重排会让读者无法恢复 X 列序）。

front matter 约定（`_` 前缀文件 = 元数据豁免，如 `_template.md`）：

    ---
    name: <合成分名>
    spec: composites/specs/<name>.yaml
    window: "<start> ~ <end>"
    status: draft
    ---

无 front matter 的 `.md` 视为**历史档案**：不参与配对，但在索引"历史档案"区
显式列出（透明，不静默丢弃）。

`--check` 只读已提交文件（spec + 档案），不依赖 `runs/` 产物——干净检出即可复现；
评估数字在档案正文中如实引用 `runs/platform/composites/<name>/summary.json` 快照。
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

from quantresearch_paths import COMPOSITE_SPECS, DOCS_COMPOSITES, INDEX, ROOT

COMPOSITE = COMPOSITE_SPECS
DOCS = DOCS_COMPOSITES
OUT = INDEX / "composites.md"

_REQUIRED_FRONT = ("spec", "window")


class CompositeIndexError(ValueError):
    """成对门/规格校验失败——消息含全部问题行。"""


def _front_matter(text: str) -> dict | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            data = yaml.safe_load("\n".join(lines[1:i]))
            if not isinstance(data, dict):
                raise ValueError("front matter 必须为 mapping")
            return data
    raise ValueError("front matter 未闭合（缺第二个 ---）")


def load_specs(composite_dir: Path = COMPOSITE) -> tuple[list[dict], list[str]]:
    """读全部合成分 spec（`_` 前缀豁免）；返回 (rows, errors)。"""
    rows: list[dict] = []
    errors: list[str] = []
    for f in sorted(Path(composite_dir).glob("*.yaml")):
        if f.name.startswith("_"):
            continue
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            errors.append(f"{f.name}: YAML 解析失败: {exc}")
            continue
        if not isinstance(data, dict):
            errors.append(f"{f.name}: 根结构必须为 mapping")
            continue
        name = data.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"{f.name}: 缺少 name 字段")
            continue
        if name != f.stem:
            errors.append(f"{f.name}: name={name!r} ≠ 文件名 stem={f.stem!r}"
                          f"（约定同名校验）")
            continue
        members = data.get("members")
        if (not isinstance(members, list) or not members
                or not all(isinstance(m, str) and m for m in members)):
            errors.append(f"{f.name}: members 必须为非空字符串列表"
                          f"（声明顺序 = X 列顺序，design §3）")
            continue
        impl = data.get("implementation") or {}
        entrypoint = impl.get("entrypoint") if isinstance(impl, dict) else None
        if not isinstance(entrypoint, str) or not entrypoint:
            errors.append(f"{f.name}: 缺少 implementation.entrypoint")
            continue
        rows.append({
            "name": name,
            "stem": f.stem,
            "yaml": f"composites/specs/{f.name}",
            "members": list(members),
            "entrypoint": entrypoint,
            "params": data.get("params") or {},
        })
    return rows, errors


def load_dossiers(docs_dir: Path = DOCS):
    """读全部档案；返回 (registered, legacy, errors)。"""
    registered: list[dict] = []
    legacy: list[dict] = []
    errors: list[str] = []
    for f in sorted(Path(docs_dir).glob("*.md")):
        if f.name.startswith("_"):
            continue
        try:
            fm = _front_matter(f.read_text(encoding="utf-8"))
        except ValueError as exc:
            errors.append(f"{f.name}: front matter 非法: {exc}")
            continue
        if fm is None:
            legacy.append({"stem": f.stem,
                           "md": f"dossiers/composites/{f.name}"})
            continue
        missing = [k for k in _REQUIRED_FRONT if not fm.get(k)]
        if missing:
            errors.append(f"{f.name}: front matter 缺字段 {missing}"
                          f"（模板约定：{' + '.join(_REQUIRED_FRONT)}）")
            continue
        declared = str(fm["spec"])
        expected = f"composites/specs/{f.stem}.yaml"
        if declared != expected:
            errors.append(f"{f.name}: front matter spec={declared!r} ≠ 约定 {expected!r}")
            continue
        name = fm.get("name")
        if name is not None and name != f.stem:
            errors.append(f"{f.name}: front matter name={name!r} ≠ 文件名 stem={f.stem!r}")
            continue
        registered.append({
            "stem": f.stem,
            "md": f"dossiers/composites/{f.name}",
            "spec": declared,
            "window": fm["window"],
            "status": fm.get("status", "—"),
        })
    return registered, legacy, errors


def _pair(specs: list[dict], registered: list[dict], legacy: list[dict]) -> list[str]:
    errors: list[str] = []
    reg_by_stem = {d["stem"]: d for d in registered}
    legacy_stems = {d["stem"] for d in legacy}
    for s in specs:
        if s["stem"] not in reg_by_stem:
            hint = ("（同名档案无 front matter——历史档案不参与配对）"
                    if s["stem"] in legacy_stems else "")
            errors.append(f"{s['stem']}: spec 缺档案 "
                          f"dossiers/composites/{s['stem']}.md{hint}")
    spec_stems = {s["stem"] for s in specs}
    for d in registered:
        if d["stem"] not in spec_stems:
            errors.append(f"{d['stem']}: 档案缺 spec {d['spec']}")
    return errors


def _params_text(params: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(params.items())) or "—"


def render(composite_dir: Path = COMPOSITE, docs_dir: Path = DOCS) -> str:
    """生成索引文本；成对门/规格失败 → CompositeIndexError（不做部分生成）。"""
    specs, errors = load_specs(composite_dir)
    registered, legacy, d_errors = load_dossiers(docs_dir)
    errors.extend(d_errors)
    errors.extend(_pair(specs, registered, legacy))
    if errors:
        raise CompositeIndexError("合成分索引成对门失败：\n" + "\n".join(errors))

    lines: list[str] = []
    lines.append("# 合成分索引（自动生成，勿手改）")
    lines.append("")
    lines.append("> 生成器：`research/tools/factor_lib/build_composite_index.py`"
                 "（`--check` 门：产物必须逐字节一致）。")
    lines.append(f"> 共 **{len(specs)} 个合成分**；成员列顺序 = spec 声明顺序（design §3）。")
    lines.append("")
    lines.append("## 注册合成分（spec ↔ 档案成对）")
    lines.append("")
    lines.append("| 合成分 | 成员（列顺序） | 方法 | 参数 | 窗口 | 状态 | 档案 | 规格 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for s in sorted(specs, key=lambda x: x["name"]):
        d = next(d for d in registered if d["stem"] == s["stem"])
        members = " → ".join(f"`{m}`" for m in s["members"])
        lines.append(
            f"| `{s['name']}` | {members} | `{s['entrypoint']}` | "
            f"{_params_text(s['params'])} | {d['window']} | {d['status']} | "
            f"[{s['stem']}.md](../../{d['md']}) | "
            f"[{s['stem']}.yaml](../../{s['yaml']}) |")
    lines.append("")
    if legacy:
        lines.append("## 历史档案（无 front matter，未注册合成分 spec）")
        lines.append("")
        for d in sorted(legacy, key=lambda x: x["stem"]):
            lines.append(f"- [{d['stem']}.md](../../{d['md']})——历史研究档案，"
                         f"补 front matter（spec + window）后进入上表")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _display(out: Path) -> str:
    try:
        return str(out.relative_to(ROOT))
    except ValueError:
        return str(out)


def main(argv: list[str] | None = None, *, composite_dir: Path = COMPOSITE,
         docs_dir: Path = DOCS, out: Path = OUT) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        text = render(composite_dir=composite_dir, docs_dir=docs_dir)
    except CompositeIndexError as exc:
        print(str(exc))
        return 1
    out = Path(out)
    if "--check" in argv:
        if not out.is_file():
            print(f"索引不存在: {out}（先运行 build_composite_index.py 生成）")
            return 1
        if out.read_text(encoding="utf-8") != text:
            print("索引与生成器输出不一致（doc 陈旧或手改）——重跑 build_composite_index.py")
            return 1
        print("合成分索引一致 ✓")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"已生成 {_display(out)}（{len(text.splitlines())} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
