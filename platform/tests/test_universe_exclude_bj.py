"""R37 范围收窄（2026-09-20 用户裁定）：universe 默认排除 .BJ（addendum §4）。

规格：knowledge/design/platform/specs/2026-09-20-dq-scope-cut-addendum.md §4：
- universe 解析默认排除 `.BJ`（`exclude_bj` 默认 true；显式 false 才纳入），
  日频/分钟/复合成员解析同口径；
- `.BJ` 代码不出现在 universe 结果帧；`exclude_bj` 不影响其它规则语义。

断言来源 = 规格（不是实现）：rules/codes/候选/PIT 帧/ref 文件与 canonical
handoff 全部同口径；显式 exchanges 与 exclude_st/min_list_days 语义不变。
"""
from __future__ import annotations

import copy

import polars as pl
import pytest

from factorlab.adapters.read.universe import (resolve_candidate_codes,
                                              resolve_canonical_code_map,
                                              resolve_codes,
                                              resolve_universe_frame)
from factorlab.config import Settings
from factorlab.core.spec import FactorSpec

_A, _B, _BJ = "000001.SZ", "600519.SH", "830001.BJ"

_SB_COLS = [("ts_code", "str"), ("symbol", "str"), ("exchange", "str"),
            ("list_date", "date"), ("industry", "str"), ("market", "str")]
_ST_COLS = [("ts_code", "str"), ("name", "str"), ("trade_date", "date"),
            ("type", "str"), ("type_name", "str")]
_DAILY_COLS = [("ts_code", "str"), ("trade_date", "date"), ("close", "f64")]
_CAL_COLS = [("exchange", "str"), ("cal_date", "date"), ("is_open", "i64")]

# stock_basic 含 .BJ；stock_st coverage = [20240102, 20260814]：
# 2024-01-02 当日 000001 ST；最新快照（20260814）= {600519}（enables 双规则组合）。
_BASE_TABLES = {
    "stock_basic": (_SB_COLS,
                    [(_A, "000001", "SZSE", "19910403", "银行", "主板"),
                     (_B, "600519", "SSE", "20010827", "白酒", "主板"),
                     (_BJ, "830001", "BSE", "20200101", "其他", "北交所")]),
    "stock_st": (_ST_COLS,
                 [("000001.SZ", "ST平安", "20240102", "ST", "实施风险警示"),
                  ("600519.SH", "ST茅台", "20260814", "ST", "实施风险警示")]),
    "daily": (_DAILY_COLS, [("000001.SZ", "20260812", 11.0)]),
    "trade_cal": (_CAL_COLS, [("SSE", "20260812", 1)]),
}


def _seed(env):
    env.seed(copy.deepcopy(_BASE_TABLES))


def spec_with(**universe_kwargs) -> FactorSpec:
    return FactorSpec.model_validate({
        "name": "demo", "category": "custom", "direction": 1,
        "universe": universe_kwargs, "formula": "signal = close",
    })


# ---------------------------------------------------------------- rules 默认/显式

def test_rules_default_excludes_bj(env):
    """默认（含显式 exclude_bj: true）→ .BJ 不进 codes/候选。"""
    _seed(env)
    assert resolve_codes(spec_with(rules={"exclude_bj": True}), env.rd) \
        == ["000001", "600519"]
    for rules in ({}, {"exclude_bj": True}):
        assert resolve_candidate_codes(spec_with(rules=rules), env.rd) \
            == ["000001", "600519"]


def test_rules_explicit_false_includes_bj(env):
    """仅显式 exclude_bj: false 纳入 .BJ（codes + 候选 + PIT 帧）。"""
    _seed(env)
    spec = spec_with(rules={"exclude_bj": False})
    assert resolve_codes(spec, env.rd) == ["000001", "600519", "830001"]
    assert resolve_candidate_codes(spec, env.rd) == ["000001", "600519", "830001"]

    uf = resolve_universe_frame(spec, env.rd, ["2024-01-02"])
    bj = uf.filter(pl.col("code") == "830001")
    assert bj.height == 1, ".BJ 在显式 false 下必须保留"
    row = bj.to_dicts()[0]
    assert row["exchange"] == "BSE"
    assert row["in_universe"] is True   # 2020-01-01 上市、非 ST
    assert "830001" in uf.filter(pl.col("in_universe"))["code"].to_list()


def test_universe_frame_default_has_no_bj_rows(env):
    """默认 rules：universe 结果帧不含任何 .BJ 行（不是 in_universe=false 留行）。"""
    _seed(env)
    uf = resolve_universe_frame(spec_with(rules={}), env.rd, ["2024-01-02"])
    assert set(uf["code"].unique().to_list()) == {"000001", "600519"}
    assert "830001" not in uf["code"].to_list()


def test_universe_frame_default_drops_explicit_candidate_bj(env):
    """显式传入 candidate_codes 含 BJ 时，帧仍不得出现 .BJ（结果帧门在函数内）。"""
    _seed(env)
    uf = resolve_universe_frame(spec_with(rules={}), env.rd, ["2024-01-02"],
                                candidate_codes=["000001", "830001"])
    assert "830001" not in uf["code"].to_list()
    assert set(uf["code"].unique().to_list()) == {"000001"}


# ---------------------------------------------------------------- 与其它规则组合

def test_exclude_bj_false_respects_other_rules(env):
    """exclude_bj: false 只放行 BJ；exclude_st/min_list_days 语义原样生效。"""
    _seed(env)
    rules = {"exclude_bj": False, "exclude_st": True, "min_list_days": 2000}
    uf = resolve_universe_frame(spec_with(rules=rules), env.rd, ["2024-01-02"])
    active = set(uf.filter(pl.col("in_universe"))["code"].to_list())
    # 000001 当日 ST 排除；830001（2020 上市，年龄 1462d < 2000）排除；600519 保留
    assert active == {"600519"}
    # BJ 行保留在帧内但 in_universe=false（排除理由不是 scope，而是 min_list_days）
    bj = uf.filter(pl.col("code") == "830001")
    assert bj.height == 1 and bj["in_universe"][0] is False

    spec = spec_with(rules=rules)
    spec.date.start = "2022-01-01"
    # resolve_codes 同规则（最新 ST 快照 = {600519}）：600519 ST 排除；
    # 830001 年龄 730d < 2000 排除；000001 保留 → exclude_bj:false 不放宽其它规则
    assert resolve_codes(spec, env.rd) == ["000001"]


def test_exclude_st_default_unchanged_without_bj_flag(env):
    """默认 exclude_bj 不改变既有组合：exclude_st 结果与规格前一致。"""
    _seed(env)
    spec = spec_with(rules={"exclude_st": True})
    # legacy/static：最新快照（20260814）ST 集合 = {600519}；BJ 默认出局
    assert resolve_codes(spec, env.rd) == ["000001"]
    uf = resolve_universe_frame(spec, env.rd, ["2024-01-02"])
    assert set(uf["code"].unique().to_list()) == {"000001", "600519"}
    active = set(uf.filter(pl.col("in_universe"))["code"].to_list())
    assert active == {"600519"}                             # 000001 当日 ST


# ---------------------------------------------------------------- codes / ref 分支

def test_codes_branch_default_excludes_bj(env):
    """显式 codes 列表默认剔除 .BJ（codes + 候选 + PIT 帧同口径）。"""
    _seed(env)
    spec = spec_with(codes=[_A, _BJ])
    assert resolve_codes(spec, env.rd) == ["000001"]
    assert resolve_candidate_codes(spec, env.rd) == ["000001"]
    uf = resolve_universe_frame(spec, env.rd, ["2024-01-02"])
    assert "830001" not in uf["code"].to_list()


def test_ref_file_exclude_bj_false_includes_bj(env, tmp_path):
    """ref 文件顶层 `exclude_bj: false` 是 codes 分支唯一放行口。"""
    _seed(env)
    uni_dir = tmp_path / "universes"
    uni_dir.mkdir()
    (uni_dir / "bj_pool.yaml").write_text(
        f"codes: ['{_A}', '{_BJ}']\nexclude_bj: false\n", encoding="utf-8")
    settings = Settings(universes_dir=uni_dir)
    spec = spec_with(ref="bj_pool")
    assert resolve_codes(spec, env.rd, settings=settings) \
        == ["000001", "830001"]
    assert resolve_candidate_codes(spec, env.rd, settings=settings) \
        == ["000001", "830001"]

    # 同文件缺 exclude_bj（默认）→ 只留非 BJ
    (uni_dir / "default_pool.yaml").write_text(
        f"codes: ['{_A}', '{_BJ}']\n", encoding="utf-8")
    assert resolve_codes(spec_with(ref="default_pool"), env.rd,
                         settings=settings) == ["000001"]


# ---------------------------------------------------------------- canonical handoff

def test_candidate_to_canonical_map_consistent(env):
    """M7-05 handoff：默认候选已无 BJ → canonical map 无 .BJ；
    显式放行时 reference mapper 照常映射 .BJ（map 本体不是过滤点）。"""
    _seed(env)
    codes = resolve_candidate_codes(spec_with(rules={}), env.rd)
    out = resolve_canonical_code_map(env.rd, codes)
    assert out["code"].to_list() == ["000001.SZ", "600519.SH"]
    assert not any(c.endswith(".BJ") for c in out["code"].to_list())

    codes2 = resolve_candidate_codes(spec_with(rules={"exclude_bj": False}), env.rd)
    out2 = resolve_canonical_code_map(env.rd, codes2)
    assert "830001.BJ" in out2["code"].to_list()


def test_exclude_bj_unknown_rule_whitelisted(env):
    """exclude_bj ∈ _ALLOWED_RULES：不再被'未知规则'拒绝。"""
    _seed(env)
    with pytest.raises(ValueError, match="未知 universe 规则"):
        resolve_codes(spec_with(rules={"exclude_bj_typo": True}), env.rd)
    assert resolve_codes(spec_with(rules={"exclude_bj": False}), env.rd) \
        == ["000001", "600519", "830001"]
