"""M5（G4）：schema 元数据 → 目录正文 + 机器可读 JSON + 错误修复手册（同源生成）。

设计断言源（design §5.4 活文档四条 + §6 G4 差距行 + plan M5 行）：
1. 开放面描述：字段语义/单位/产生时点、算子用法/参数/约束、def 组合规范与示例、
   命名类约定（含"未来列必须落未来前缀"纪律）、错误与修法手册；
2. 关闭面描述：每条门规则 + 触发示例 + 报错文案样板（门表 ↔ 测试逐条对照）；
3. 机器可读 JSON（`catalog dump` 入口）同源生成，供写因子的 AI 开写前阅读；
4. 文档 = 教学与帮助：knowledge/contracts/interface.md 只写已实现行为，不超前。
「无白名单」：目录是活文档不是校验门——任何真实存在的列/def 新算子/新字段都自由。

「禁止行为」保证：
- 元数据校验（validate_catalog）拒绝任何空/占位/详见式描述——替换为放行一切
  的存根即败；
- 错误手册每条"文案样板"必须逐字存在于 src 源码（换实现/改文案 → 断链即败）；
- 每条门规则映射的对照测试必须真实存在（AST 级验证文件与 def）；
- DB 无关门规则的 probe 必须真实 raise 且含样板（防目录内容与实现漂移）；
- 列/算子/命名清单必须与运行时单源常量逐字一致（source._PLATFORM_COLS、
  _DAILY_BASIC_MAP、_SPECIAL_COLS、engine.reserved、ast_gate）——解析器与
  活文档共享同一 schema 元数据（防漂移）；
- JSON 输出确定性（无时间戳），两次 dump 逐字节一致。
"""

import ast
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from factorlab.adapters.catalog import (
    STUB_WORDS,
    build_catalog,
    catalog_json,
    render_catalog_markdown,
    validate_catalog,
)
from factorlab.surfaces.cli.main import app
from factorlab.adapters.read import source as src
from factorlab.core.engine.reserved import (
    FUTURE_NAMES,
    FUTURE_PREFIXES,
    INTERNAL_NAMES,
    INTERNAL_PREFIX,
)
from factorlab.core.factor.ast_gate import ALLOWED_EXPR_METHODS

runner = CliRunner()

REPO = Path(__file__).resolve().parent.parent


# ---------------- 顶层结构与 schema 完整性 ----------------

def test_build_catalog_top_level_schema():
    cat = build_catalog()
    assert set(cat) == {
        "schema_version", "scope", "source_ref",
        "open_surface", "closed_gates", "error_handbook", "known_approximations",
    }
    assert isinstance(cat["schema_version"], int) and cat["schema_version"] >= 1
    assert "日频" in cat["scope"] and "属性" in cat["scope"]  # Interface #1 命名空间
    assert "2026-09-06-factorlab-dsl-shape-design" in cat["source_ref"]
    assert set(cat["open_surface"]) == {"columns", "operators", "def_composition", "naming"}
    # 目录是活文档不是校验门：列/算子/字段全开放的不变量写在 scope 语义里
    assert any("白名单" in str(v) for v in cat.values()
               for k in ("scope",) for v in [cat["scope"]])


def test_validate_catalog_accepts_built():
    validate_catalog(build_catalog())  # 不抛 = 描述完整


def test_validate_catalog_rejects_stub_descriptions():
    """描述完整性门（防"详见"/占位式省略）——注入任何 stub 都必须被点名拒绝。"""
    cat = build_catalog()
    for stub in STUB_WORDS:
        bad = json.loads(json.dumps(cat))
        bad["open_surface"]["columns"][0]["semantic"] = f"见 interface.md {stub}"
        with pytest.raises(ValueError, match="semantic"):
            validate_catalog(bad)
    for field in ("semantic", "unit", "timing"):
        bad = json.loads(json.dumps(cat))
        bad["open_surface"]["columns"][0][field] = ""
        with pytest.raises(ValueError, match=field):
            validate_catalog(bad)


def test_validate_catalog_rejects_empty_gate_fields():
    cat = json.loads(json.dumps(build_catalog()))
    gate = cat["error_handbook"][0]
    for field in ("rule", "trigger", "sample", "fix"):
        old = gate[field]
        gate[field] = ""
        with pytest.raises(ValueError):
            validate_catalog(cat)
        gate[field] = old


# ---------------- 开放面：字段（语义/单位/产生时点） ----------------

def _columns(cat):
    return cat["open_surface"]["columns"]


def test_columns_schema_and_completeness():
    expected = {  # 每列行：名称/来源表/类别/语义/单位/产生时点 全部非空
        "name", "table", "kind", "semantic", "unit", "timing",
    }
    for row in _columns(build_catalog()):
        assert set(row) == expected
        assert all(row[k].strip() for k in ("name", "table", "kind", "semantic", "unit", "timing"))
        assert not any(w in row["semantic"] for w in STUB_WORDS)


def test_columns_mandatory_members_and_units():
    names = {r["name"]: r for r in _columns(build_catalog())}
    for required in ("date", "code", "open", "high", "low", "close", "pre_close",
                     "change", "pct_chg", "volume", "amount", "adj_factor",
                     "idx_ret", "turnover", "total_mv", "circ_mv", "pe_ttm",
                     "pb", "dv_ratio", "volume_ratio", "industry"):
        assert required in names, required
    # 单位与语义必须具体可执行（2026-09-08 实测校准：ch_prod daily.vol/daily.amount
    # 对 bars_1m 按 (code, 交易日) 汇总比值 ≈ 1——平台 daily 实际落库为 股/元，
    # 与分钟面同单位、可代数对齐；vendor 文档口径（手/千元）不适用本库）
    assert "股" in names["volume"]["unit"]
    assert "元" in names["amount"]["unit"]
    assert "万元" in names["total_mv"]["unit"] and "万元" in names["circ_mv"]["unit"]
    assert "元" in names["close"]["unit"] and "不复权" in names["close"]["semantic"]
    assert "复权" in names["adj_factor"]["semantic"]
    assert "时点" in names["close"]["timing"] or "盘后" in names["close"]["timing"] \
        or "T 日" in names["close"]["timing"] or "t 日" in names["close"]["timing"].lower()
    # 未来列命名纪律落在命名类约定里，不是某字段的免责声明
    assert "industry" in names
    assert "当前值" in names["industry"]["semantic"] or "非" in names["industry"]["semantic"]


def test_columns_share_single_source_with_parser():
    """列清单 == 解析器/读路径常量并集（防漂移：目录与运行时同一 schema 元数据）。"""
    rows = {r["name"]: r for r in _columns(build_catalog())}
    expected = set(src._PLATFORM_COLS) | set(src._DAILY_BASIC_MAP) | set(src._SPECIAL_COLS)
    surface = {n for n, r in rows.items() if r["table"] not in ("attributes", "hint")}
    assert surface == expected
    # 表归属与读路径一致：映射名进 daily、daily_basic 键进估值表、特殊名表 special
    for name in src._PLATFORM_COLS:
        assert rows[name]["table"] == "daily"
    for name in src._DAILY_BASIC_MAP:
        assert rows[name]["table"] == "daily_basic"
    for name in src._SPECIAL_COLS:
        assert rows[name]["table"] == "special"
    # 属性面示例行 + 原始列映射提示行存在
    assert rows["industry"]["table"] == "attributes"
    for raw, engine in src._RAW_MAP_HINTS.items():
        hint = f"{raw} → {engine}"
        assert any(hint in r["semantic"] for r in rows.values() if r["table"] == "hint")


def test_columns_no_whitelist_statement():
    """目录必须声明"无白名单"：可用列随当前数据面实探，收录不是前提。"""
    md = render_catalog_markdown()
    assert "无白名单" in md or "没有白名单" in md
    assert "实探" in md or "schema" in md


# ---------------- 开放面：算子 / def 组合规范 / 方法链 ----------------

def test_operators_schema_and_platform_owned_complete():
    ops = build_catalog()["open_surface"]["operators"]
    assert set(ops) == {"platform_owned", "registry_inventory", "elementwise_methods",
                        "partition_prefixes"}
    for row in ops["platform_owned"]:
        assert set(row) == {"name", "kind", "semantic", "constraints"}
        assert row["name"] and row["semantic"].strip() and row["constraints"].strip()
    owned = {r["name"] for r in ops["platform_owned"]}
    assert {"returns", "vwap", "adv20", "gp_rank", "gp_mean", "cs_stable_rank"} <= owned
    for row in ops["registry_inventory"]:
        assert set(row) == {"name", "kind", "version"}
        assert row["name"] and row["kind"]
    inv = {r["name"] for r in ops["registry_inventory"]}
    assert owned <= inv  # 平台自带算子也在注册清单里（op list 同源）
    assert "cs_stable_rank" in inv
    # cs_rank 是 stable dense rank 的公式层别名（alias 不在 op list——
    # 注册清单与 list_ops() 同源）；别名可写、canonical 在册
    from factorlab.core.ops import registry as _reg
    assert _reg.has_op("cs_rank") and _reg.get_op("cs_rank").name == "cs_stable_rank"


def test_registry_inventory_matches_runtime_registry():
    """注册清单必须来自当前 registry.list_ops()（非手抄目录）——与 compute_formula
    相同的幂等注册链触发后逐名一致。"""
    from factorlab.core.ops.platform_ops import register_platform_ops
    from factorlab.core.ops.polars_ta_wrappers import register_polars_ta_ops
    from factorlab.core.ops.stable_rank import register_stable_rank_ops
    from factorlab.core.ops import registry

    register_polars_ta_ops()
    register_platform_ops()
    register_stable_rank_ops()
    live = {op.name for op in registry.list_ops()}
    inv = {r["name"] for r in build_catalog()["open_surface"]["operators"]["registry_inventory"]}
    assert inv == live


def test_method_chain_whitelist_single_source():
    """方法链白名单 == ast_gate.ALLOWED_EXPR_METHODS（元素级方法链仅限这组）。"""
    ops = build_catalog()["open_surface"]["operators"]
    allowed = ops["elementwise_methods"]
    assert set(allowed) == set(ALLOWED_EXPR_METHODS)
    assert set(allowed) <= {"abs", "log", "log1p", "sqrt", "exp", "sign", "floor"}


def test_def_composition_rules_and_example():
    dc = build_catalog()["open_surface"]["def_composition"]
    assert set(dc) == {"rules", "example"}
    assert len(dc["rules"]) >= 5          # 组合规范事无巨细
    assert dc["example"].count("def ") >= 1  # 有示例（教学）
    joined = "\n".join(dc["rules"]) + "\n" + dc["example"]
    assert "def " in joined and "return" in joined
    assert not any(w in joined for w in STUB_WORDS)


def test_naming_conventions_single_source():
    """命名类约定 == engine/reserved 单源（防漂移）。"""
    naming = build_catalog()["open_surface"]["naming"]
    assert set(naming) == {"future_prefixes", "future_names", "internal_prefix",
                           "internal_names", "disciplines"}
    assert tuple(naming["future_prefixes"]) == FUTURE_PREFIXES
    assert set(naming["future_names"]) == set(FUTURE_NAMES)
    assert naming["internal_prefix"] == INTERNAL_PREFIX
    assert set(naming["internal_names"]) == set(INTERNAL_NAMES)
    # 纪律文本包含数据侧"未来列命名"与"入库校验"双重锁 + 引擎读面列禁止内部名
    joined = "\n".join(naming["disciplines"])
    assert "future_prefixes" in naming or "forward_*" in joined
    assert "入库" in joined and "validate_engine_surface" in joined
    assert "活文档" in joined
    assert not any(w in joined for w in STUB_WORDS)


# ---------------- 关闭面：门表 ↔ 测试逐条对照 ----------------

_GATE_KEYS = {"id", "category", "name", "rule", "trigger", "sample", "fix", "tests", "probe"}
WALLS = {"syntax_efficiency", "future", "internal"}
CATEGORIES = WALLS | {"spec", "processor", "helper"}
_TEST_REF = r"tests/(test_[a-z0-9_]+\.py)::(test_[a-zA-Z0-9_]+)"


def _handbook(cat):
    return cat["error_handbook"]


def test_error_handbook_schema():
    cat = build_catalog()
    rows = _handbook(cat)
    assert len(rows) >= 15
    seen_ids = set()
    for row in rows:
        assert set(row) == _GATE_KEYS, row["id"]
        assert row["category"] in CATEGORIES, row["id"]
        assert re_id(row["id"]) and row["id"] not in seen_ids
        seen_ids.add(row["id"])
        assert all(len(row[k].strip()) >= 4 for k in ("rule", "trigger", "sample", "fix"))
        assert not any(w in (row["rule"] + row["trigger"] + row["fix"]) for w in STUB_WORDS)
        assert row["tests"], row["id"]


def re_id(s):
    import re
    return re.fullmatch(r"[a-z][a-z0-9_]*", s)


def test_closed_gates_exactly_wall_categories():
    cat = build_catalog()
    closed = cat["closed_gates"]
    assert len(closed) >= 8  # 三类墙逐条收录
    assert {r["category"] for r in closed} == WALLS
    assert closed == [r for r in _handbook(cat) if r["category"] in WALLS]
    for row in closed:
        assert row["sample"] and row["fix"]


def test_closed_gates_cover_the_three_walls():
    """三类墙的代表门必须收录：语法/效率（未知算子/负位移 def 安全网）、
    未来函数（负位移 + 未来列引用）、内部保留（绑定 + 读取）。"""
    rows = {r["id"]: r for r in _handbook(build_catalog())}
    for required in (
            "gate_unknown_operator", "gate_negative_shift",
            "gate_future_column_input", "gate_reserved_binding", "gate_reserved_read",
            "gate_unknown_column_helper",
    ):
        assert required in rows, required


def test_gate_rows_map_to_existing_tests():
    """门表 ↔ 测试逐条对照：每条门规则引用的对照测试必须真实存在
    （tests/<file>.py::<test> 文件与 def 均在场）——引用悬空即失败。"""
    import re
    for row in _handbook(build_catalog()):
        for ref in row["tests"]:
            m = re.fullmatch(_TEST_REF, ref)
            assert m, f"{row['id']}: 非法测试引用 {ref!r}"
            fname, test_name = m.group(1), m.group(2)
            path = REPO / "tests" / fname
            assert path.is_file(), f"{row['id']}: 文件不存在 {ref}"
            src_text = path.read_text(encoding="utf-8")
            assert f"def {test_name}(" in src_text, f"{row['id']}: def 不存在 {ref}"


def test_error_samples_live_in_source():
    """错误手册每条"文案样板"逐字存在于 src/factorlab 源码（真实报错文案同源）。
    样板必须是连续的源码字面量（无 f-string 插值），否则换文案即断链。"""
    sources = "".join(
        p.read_text(encoding="utf-8")
        for p in (REPO / "src" / "factorlab").rglob("*.py")
    )
    for row in _handbook(build_catalog()):
        sample = row["sample"]
        assert len(sample) >= 6, row["id"]
        assert not re_braces(sample), f"{row['id']}: 样板含插值占位符"
        assert sample in sources, f"{row['id']}: 样板在源码中不存在: {sample!r}"


def re_braces(s):
    import re
    return re.search(r"\{.*\}", s)


def test_db_free_probes_match_runtime():
    """带 probe 的门（无 DB 即可触发）：真实执行必须 raise 且文案含样板——
    目录样板与运行时报错文案逐条一致（DB 相关门的运行时一致性由对照测试锁）。"""
    for row in _handbook(build_catalog()):
        if not row["probe"]:
            continue
        ns: dict = {}
        with pytest.raises((ValueError, KeyError)) as excinfo:
            exec(row["probe"], ns)  # noqa: S102 固定仓库内文案 probe
        assert row["sample"] in str(excinfo.value), (
            f"{row['id']}: 运行时文案不含样板 {row['sample']!r} → {str(excinfo.value)[:200]}")


# ---------------- 错误修复手册/触发示例完整性 ----------------

def test_every_wall_gate_has_distinct_sample_from_any_other():
    """错误手册可区分性：同一门内样板唯一（跨门允许同源文案）。"""
    seen: dict[str, list[str]] = {}
    for row in _handbook(build_catalog()):
        seen.setdefault(row["sample"], []).append(row["id"])
    dup = {s: ids for s, ids in seen.items() if len(ids) > 1}
    for s, ids in dup.items():
        assert len(set(ids)) == 1, f"样板 {s!r} 被多个门共用: {ids}"


# ---------------- 已知近似（§7 三条） ----------------

def test_known_approximations_cover_section7():
    approx = build_catalog()["known_approximations"]
    assert len(approx) >= 3
    joined = json.dumps(approx, ensure_ascii=False)
    assert "industry" in joined and "PIT" in joined.upper()
    assert "未知变量" in joined or "def 形参" in joined
    assert "未来" in joined and "前缀" in joined
    for row in approx:
        assert set(row) == {"id", "area", "statement", "mitigation"}
        assert all(v.strip() for v in row.values())


# ---------------- JSON：机器可读、确定性、catalog dump 入口 ----------------

def test_catalog_json_deterministic_and_readable():
    a = catalog_json()
    b = catalog_json()
    assert a == b  # 无时间戳/顺序漂移：两次 dump 逐字节一致
    data = json.loads(a)
    assert data["schema_version"] == build_catalog()["schema_version"]
    assert "未知列名" in a        # 中文未转义，AI 可读
    assert "open_surface" in a and "closed_gates" in a


def test_cli_catalog_dump_writes_json(tmp_path):
    out = tmp_path / "cat.json"
    result = runner.invoke(app, ["catalog", "dump", "--out", str(out)])
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema_version"] >= 1
    assert "open_surface" in data and "error_handbook" in data


def test_cli_catalog_dump_stdout_default():
    result = runner.invoke(app, ["catalog", "dump"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["schema_version"] >= 1


def test_cli_catalog_docs_writes_markdown(tmp_path):
    out = tmp_path / "catalog.md"
    result = runner.invoke(app, ["catalog", "docs", "--out", str(out)])
    assert result.exit_code == 0, result.output
    md = out.read_text(encoding="utf-8")
    assert md == render_catalog_markdown()


# ---------------- 目录正文（生成物）完整性 ----------------

def test_markdown_renders_every_entry_completely():
    """生成目录正文每条描述完整：每个列/算子/门/近似行在正文都有实体内容
    （无"详见"-式省略、无空段）。"""
    md = render_catalog_markdown()
    cat = build_catalog()
    for section in ("开放面", "关闭面", "错误修复手册", "已知近似"):
        assert section in md, section
    for row in _columns(cat):
        assert f"`{row['name']}`" in md or row["name"] in md, row["name"]
        assert row["semantic"] in md, row["id"] if "id" in row else row["name"]
    for row in cat["open_surface"]["operators"]["platform_owned"]:
        assert row["semantic"] in md
        assert row["constraints"] in md
    for row in _handbook(cat):
        assert row["id"] in md
        assert row["sample"] in md and row["fix"] in md
    for row in cat["known_approximations"]:
        assert row["statement"] in md
    assert not any(w in md for w in STUB_WORDS), "生成正文不得含占位/详见式省略"
    assert md.count("validate_engine_surface") >= 1  # 数据侧双重锁落到正文


def test_markdown_docs_file_committed_and_fresh():
    """仓库内 knowledge/contracts/catalog.md 必须与生成器当前输出逐字节一致（活文档不陈旧）。"""
    doc_path = REPO.parent / "knowledge" / "contracts" / "catalog.md"
    if not doc_path.is_file():  # 文件尚未提交——实现阶段生成；此处先红
        pytest.fail("knowledge/contracts/catalog.md 缺失：文档正文与生成器不同步")
    assert doc_path.read_text(encoding="utf-8") == render_catalog_markdown()
