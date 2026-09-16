#!/usr/bin/env python
"""策略索引生成器（Plan S Task 4）：`research/strategy/**` + `knowledge/dossiers/strategies/**`
→ `knowledge/index/strategies.md`。

产物纪律（与因子索引同款）：生成物与生成器输出**逐字节一致**，
不一致即门红（`--check`；测试 `tests/test_strategy_index.py` 常驻）。

成对门（不是静默跳过）：
- 每个 `research/strategy/<stem>.yaml` 必须有 `knowledge/dossiers/strategies/<stem>.md` 档案；
- 每个带 front matter 的档案必须声明 `spec`（= `research/strategy/<stem>.yaml`）
  与 `window`（回测窗口），且 spec 存在；
- 缺任一 → 非零退出并列出名字。

front matter 约定（`_` 前缀文件 = 元数据豁免，如 `_template.md`）：

    ---
    name: <策略名>
    spec: research/strategy/<name>.yaml
    window: "<start> ~ <end>"
    status: draft
    ---

无 front matter 的 `.md` 视为**历史档案**：不参与配对，但在索引"历史档案"区
显式列出（透明，不静默丢弃）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]        # stock/
STRATEGY = ROOT / "research" / "strategy"
DOCS = ROOT / "knowledge" / "dossiers" / "strategies"
OUT = ROOT / "knowledge" / "index" / "strategies.md"

_REQUIRED_FRONT = ("spec", "window")


class StrategyIndexError(ValueError):
    """成对门失败（缺档案 / 缺 spec / front matter 非法）——消息含全部问题行。"""


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


def load_specs(strategy_dir: Path = STRATEGY) -> tuple[list[dict], list[str]]:
    """读全部策略 spec（`_` 前缀豁免）；返回 (rows, errors)。"""
    rows: list[dict] = []
    errors: list[str] = []
    for f in sorted(Path(strategy_dir).glob("*.yaml")):
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
        portfolio = data.get("portfolio") or {}
        execution = data.get("execution") or {}
        date = data.get("date") or {}
        window = (f"{date.get('start')} ~ {date.get('end')}"
                  if date.get("start") and date.get("end") else "—")
        rows.append({
            "name": name,
            "stem": f.stem,
            "yaml": f"research/strategy/{f.name}",
            "signal": data.get("signal", "—"),
            "direction": data.get("direction", "—"),
            "rebalance": portfolio.get("rebalance_frequency", "daily"),
            "timing": execution.get("timing", "NEXT_OPEN"),
            "window": window,
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
                           "md": f"knowledge/dossiers/strategies/{f.name}"})
            continue
        missing = [k for k in _REQUIRED_FRONT if not fm.get(k)]
        if missing:
            errors.append(f"{f.name}: front matter 缺字段 {missing}"
                          f"（模板约定：{' + '.join(_REQUIRED_FRONT)}）")
            continue
        declared = str(fm["spec"])
        expected = f"research/strategy/{f.stem}.yaml"
        if declared != expected:
            errors.append(f"{f.name}: front matter spec={declared!r} ≠ 约定 {expected!r}")
            continue
        name = fm.get("name")
        if name is not None and name != f.stem:
            errors.append(f"{f.name}: front matter name={name!r} ≠ 文件名 stem={f.stem!r}")
            continue
        registered.append({
            "stem": f.stem,
            "md": f"knowledge/dossiers/strategies/{f.name}",
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
                          f"knowledge/dossiers/strategies/{s['stem']}.md{hint}")
    spec_stems = {s["stem"] for s in specs}
    for d in registered:
        if d["stem"] not in spec_stems:
            errors.append(f"{d['stem']}: 档案缺 spec {d['spec']}")
    return errors


def render(strategy_dir: Path = STRATEGY, docs_dir: Path = DOCS) -> str:
    """生成索引文本；成对门失败 → StrategyIndexError（不做部分生成）。"""
    specs, errors = load_specs(strategy_dir)
    registered, legacy, d_errors = load_dossiers(docs_dir)
    errors.extend(d_errors)
    errors.extend(_pair(specs, registered, legacy))
    if errors:
        raise StrategyIndexError("策略索引成对门失败：\n" + "\n".join(errors))

    lines: list[str] = []
    lines.append("# 策略索引（自动生成，勿手改）")
    lines.append("")
    lines.append("> 生成器：`research/tools/factor_lib/build_strategy_index.py`"
                 "（`--check` 门：产物必须逐字节一致）。")
    lines.append(f"> 共 **{len(specs)} 个策略**。")
    lines.append("")
    lines.append("## 注册策略（spec ↔ 档案成对）")
    lines.append("")
    lines.append("| 策略 | 信号（L3） | 方向 | 调仓 | 执行 | 窗口 | 状态 | 档案 | 规格 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for s in sorted(specs, key=lambda x: x["name"]):
        d = next(d for d in registered if d["stem"] == s["stem"])
        lines.append(
            f"| `{s['name']}` | `{s['signal']}` | {s['direction']} | "
            f"{s['rebalance']} | {s['timing']} | {s['window']} | {d['status']} | "
            f"[{s['stem']}.md](../../{d['md']}) | "
            f"[{s['stem']}.yaml](../../{s['yaml']}) |")
    lines.append("")
    if legacy:
        lines.append("## 历史档案（无 front matter，未注册策略 spec）")
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


def main(argv: list[str] | None = None, *, strategy_dir: Path = STRATEGY,
         docs_dir: Path = DOCS, out: Path = OUT) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        text = render(strategy_dir=strategy_dir, docs_dir=docs_dir)
    except StrategyIndexError as exc:
        print(str(exc))
        return 1
    out = Path(out)
    if "--check" in argv:
        if not out.is_file():
            print(f"索引不存在: {out}（先运行 build_strategy_index.py 生成）")
            return 1
        if out.read_text(encoding="utf-8") != text:
            print("索引与生成器输出不一致（doc 陈旧或手改）——重跑 build_strategy_index.py")
            return 1
        print("策略索引一致 ✓")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"已生成 {_display(out)}（{len(text.splitlines())} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
