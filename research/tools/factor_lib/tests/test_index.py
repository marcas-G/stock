"""因子库结构门（R5）：索引一致 · yaml↔md 镜像 · 族规则 · 变体组报告。

历史：152 个 yaml 平铺 + 153 个档案，无索引、无族规则、无从查证"哪些是同公式变体"。
本测试把这些从"约定"升级为**门**。
"""
from __future__ import annotations

import pathlib
import sys

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import build_index as BI  # noqa: E402
import plan_rename  # noqa: E402

ROOT = BI.ROOT
FACTOR = BI.FACTOR
DOCS = BI.DOCS


def test_index_matches_generator(tmp_path):
    """门：docs/index/factors.md 必须与生成器输出逐字节一致（防手改/陈旧）。"""
    assert BI.OUT.is_file(), "索引不存在——先跑 build_index.py"
    assert BI.OUT.read_text(encoding="utf-8") == BI.render()


def test_every_yaml_has_mirror_doc_and_name_matches():
    """yaml ↔ md 一一镜像；档案 front-matter 的 xname 必须等于 spec.name。"""
    specs = BI.load_specs()
    # 原集 ≥152（只增不删——挖矿循环持续新增因子并逐轮归档；低于 152 说明有删除，
    # 必须显式确认。R04 后 `_` 前缀目录/文件为元数据，不计入）
    assert len(specs) >= 152, f"因子数少于原集 152（{len(specs)}）——有删除？"
    for s in specs:
        md = ROOT / s["md"]
        assert md.is_file(), f"缺档案 {s['md']}"
        head = md.read_text(encoding="utf-8").splitlines()[:8]
        xname = next((l.split(":", 1)[1].strip() for l in head if l.startswith("xname:")), None)
        assert xname == s["name"], f"{s['md']} 的 xname={xname!r} ≠ name={s['name']!r}"


def test_family_rules():
    """每个 yaml 落在 _families.yaml 声明的族目录里，且名字以该族前缀开头（misc 除外）。"""
    fams = dict(plan_rename.load_families())
    # `_` 前缀目录是元数据（`_pools/` 等），不是族——与 load_specs 跳过口径一致
    assert set(fams) == {d.name for d in FACTOR.iterdir()
                         if d.is_dir() and not d.name.startswith("_")}, "族目录与族表不一致"
    for s in BI.load_specs():
        prefixes = fams.get(s["family"])
        assert prefixes is not None, f"未声明的族目录: {s['family']}"
        if s["family"] != "misc":
            assert any(s["name"] == p or s["name"].startswith(p + "_") for p in prefixes), \
                f"{s['name']} 不匹配族 {s['family']} 的前缀 {prefixes}"


def test_stems_unique_within_family():
    import collections
    seen = collections.Counter((s["family"], s["stem"]) for s in BI.load_specs())
    dup = [k for k, v in seen.items() if v > 1]
    assert not dup, f"同族 stem 冲突: {dup}"


def test_variant_groups_are_reported_not_silently_dropped():
    """同公式多假设必须**在索引里成组出现**（R5 实测 5 组 15 个，全部是 direction/params/
    process 差异的合法变体，0 个真重复）——不得静默归档或删除。"""
    text = BI.render()
    groups = [g for g in BI.load_specs() if g["formula_norm"]]
    by_f = {}
    for g in groups:
        by_f.setdefault(g["formula_norm"], []).append(g)
    variants = {k: v for k, v in by_f.items() if len(v) > 1}
    assert len(variants) >= 5, "变体组数量异常减少——确认不是误删"
    for members in variants.values():
        for m in members:
            assert f"`{m['name']}`" in text, f"变体组成员 {m['name']} 未出现在索引里"
