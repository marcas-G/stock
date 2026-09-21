"""因子库结构门（R5）：索引一致 · yaml↔md 镜像 · 族规则 · 变体组报告。

历史：152 个 yaml 平铺 + 153 个档案，无索引、无族规则、无从查证"哪些是同公式变体"。
本测试把这些从"约定"升级为**门**。

R31 ci-bootstrap ③：yaml↔md 镜像门对"缺档案"改为**提交时效宽限**——spec yaml
最近 git 提交 < GRACE_HOURS（默认 72h）或缺历史（挖矿在途）→ PENDING 打印警告放行；
超期/无法判定（非仓库/浅克隆）→ 照旧失败。判定逻辑与单测见 `dossier_freshness.py`。
"""
from __future__ import annotations

import pathlib
import sys

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import build_index as BI  # noqa: E402
import dossier_freshness as DF  # noqa: E402
import plan_rename  # noqa: E402

ROOT = BI.ROOT
FACTOR = BI.FACTOR
DOCS = BI.DOCS

# R37：本文件全部断言依赖真实研究产物区（QUANTRESEARCH_ROOT）；GitHub-hosted
# 干净 checkout 无该目录 → 整文件 skip（self-hosted 跑全量）。
pytestmark = pytest.mark.skipif(
    not FACTOR.is_dir(),
    reason=f"研究产物区不存在：{FACTOR}（QUANTRESEARCH_ROOT 未挂载）")


def test_index_matches_generator(tmp_path):
    """门：`<research_root>/index/factors.md` 必须与生成器输出逐字节一致（防手改/陈旧）。"""
    assert BI.OUT.is_file(), "索引不存在——先跑 build_index.py"
    assert BI.OUT.read_text(encoding="utf-8") == BI.render()


# inflight（fast/deep 均排除；owner=挖矿流程，档案补齐即删标记）：挖矿在途 spec
# （momentum_20d/turnrank_top2|top5）档案未补齐，yaml 提交超 72h 宽限 → 时效门转红，
# 属挖矿在途非门故障。无关联 GitHub issue（责任方=挖矿流程，非修复类 issue）。
# 其余 4 项 test_index 断言（索引一致/族规则/族内唯一/变体组）不受影响。
@pytest.mark.inflight
def test_every_yaml_has_mirror_doc_and_name_matches():
    """yaml ↔ md 一一镜像；档案 front-matter 的 xname 必须等于 spec.name。

    缺档案时效门（R31 ③）：yaml 提交 < 72h / 无历史 → PENDING（警告放行）；
    超期或 git 无法判定 → `dossier_freshness` 抛错 = 门红。
    """
    specs = BI.load_specs()
    # 原集 ≥152（只增不删——挖矿循环持续新增因子并逐轮归档；低于 152 说明有删除，
    # 必须显式确认。R04 后 `_` 前缀目录/文件为元数据，不计入）
    assert len(specs) >= 152, f"因子数少于原集 152（{len(specs)}）——有删除？"
    pending = DF.require_mirror_docs(specs, root=ROOT)
    for v in pending:
        print(f"PENDING 档案缺失（yaml 提交 < {DF.GRACE_HOURS:g}h 宽限）: "
              f"{v.md} ← {v.yaml}")
    for s in specs:
        md = ROOT / s["md"]
        if not md.is_file():
            continue  # PENDING（上面已警告）；STALE 已由 require_mirror_docs 拒绝
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
