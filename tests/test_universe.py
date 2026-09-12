"""resolve_codes/candidate 双腿参数化（env：duckdb|ch，见 tests/conftest.py）。

平台库风格假库：stock_basic（无 exchange 列依赖——交易所由 ts_code 后缀推断）/
stock_st（无 is_st 列——最新 trade_date 快照的 ts_code 集合即 ST 集合）/daily/trade_cal。
动态"变异"测试以终态数据描述表达（seed 一次到位）。
"""

import copy

import pytest

from factorlab.adapters.read.universe import (normalize_code, resolve_candidate_codes,
                                     resolve_codes)
from factorlab.core.spec import FactorSpec

_SB_COLS = [("ts_code", "str"), ("symbol", "str"), ("exchange", "str"),
            ("list_date", "str"), ("industry", "str"), ("market", "str")]
_ST_COLS = [("ts_code", "str"), ("name", "str"), ("trade_date", "date"),
            ("type", "str"), ("type_name", "str")]
_DAILY_COLS = [("ts_code", "str"), ("trade_date", "date"), ("close", "f64")]
_CAL_COLS = [("exchange", "str"), ("cal_date", "date"), ("is_open", "i64")]

_BASE_TABLES = {
    "stock_basic": (_SB_COLS,
                    [("000001.SZ", "000001", "SZSE", "19910403", "银行", "主板"),
                     ("600519.SH", "600519", "SSE", "20010827", "白酒", "主板"),
                     ("830001.BJ", "830001", "BSE", "20200101", "其他", "北交所")]),
    "stock_st": (_ST_COLS,
                 # 仅 000001 在最新快照（600519 无 ST 记录）——exclude_st 集合 = {000001}
                 [("000001.SZ", "ST平安", "20260814", "ST", "实施风险警示")]),
    "daily": (_DAILY_COLS,
              [("000001.SZ", "20260812", 11.0), ("600519.SH", "20260812", 1410.0)]),
    "trade_cal": (_CAL_COLS,
                  [("SSE", "20260812", 1), ("SSE", "20260813", 1)]),
}


def _seed(env, tables=None, append=None, drop=None):
    """_BASE_TABLES 副本 → 覆盖表 / 追加行 / 删表 → seed。"""
    t = copy.deepcopy(_BASE_TABLES)
    for name, extra in (append or {}).items():
        t[name] = (t[name][0], list(t[name][1]) + extra)
    if tables:
        t.update(tables)
    for name in (drop or []):
        t.pop(name, None)
    env.seed(t)


def spec_with(**universe_kwargs):
    return FactorSpec.model_validate({
        "name": "demo", "category": "custom", "direction": 1,
        "universe": universe_kwargs, "formula": "signal = close",
    })


def test_normalize_code():
    assert normalize_code("000001.SZ") == "000001"
    assert normalize_code("600519") == "600519"
    with pytest.raises(ValueError):
        normalize_code("abc")
    with pytest.raises(ValueError):
        normalize_code("000001.SZ.X")


def test_normalize_code_rejects_whitespace():
    with pytest.raises(ValueError):
        normalize_code(" 00000 ")


def test_resolve_codes_inline(env):
    _seed(env)
    spec = spec_with(codes=["000001.SZ", "600519"])
    assert resolve_codes(spec, env.rd) == ["000001", "600519"]


def test_resolve_codes_inline_dedup(env):
    _seed(env)
    spec = spec_with(codes=["600519.SH", "600519", "000001.SZ"])
    assert resolve_codes(spec, env.rd) == ["000001", "600519"]


def test_resolve_codes_exclude_st_platform_db(env):
    # 平台库 stock_st 无 is_st：最新 trade_date 快照的 ts_code 集合 = ST 集合
    _seed(env)
    spec = spec_with(rules={"exclude_st": True})
    assert resolve_codes(spec, env.rd) == ["600519"]  # 000001 在 stock_st 最新快照


def test_resolve_codes_exclude_st_latest_snapshot_only(env):
    # 600519 曾有 ST 记录（旧快照）但最新 trade_date 快照无 → 不算 ST，不被排除
    _seed(env, append={"stock_st": [("600519.SH", "贵州茅台", "20260101", "ST",
                                     "实施风险警示")]})
    spec = spec_with(rules={"exclude_st": True})
    assert resolve_codes(spec, env.rd) == ["600519"]


def test_resolve_codes_exclude_st_missing_stock_st_table(env):
    # 缺 stock_st 表（如未重建的平台库）：明确报错而非底层 Catalog/查询错误
    _seed(env, drop=["stock_st"])
    with pytest.raises(ValueError, match="stock_st"):
        resolve_codes(spec_with(rules={"exclude_st": True}), env.rd)


def test_resolve_codes_exchanges_by_suffix(env):
    # 平台库 stock_basic 无 exchange 列：SSE 由 ts_code 后缀 .SH 推断
    _seed(env)
    spec = spec_with(rules={"exchanges": ["SSE"]})
    assert resolve_codes(spec, env.rd) == ["600519"]


def test_resolve_codes_rejects_bse(env):
    # 含 830001.BJ；BSE 显式拒绝（v1 仅 SSE/SZSE）
    _seed(env)
    with pytest.raises(ValueError, match="BSE"):
        resolve_codes(spec_with(rules={"exchanges": ["BSE"]}), env.rd)


def test_resolve_codes_exchanges_empty_list(env):
    # 空列表等价于未指定：默认 SSE+SZSE（不含 BSE）
    _seed(env)
    spec = spec_with(rules={"exchanges": []})
    assert resolve_codes(spec, env.rd) == ["000001", "600519"]


def test_resolve_codes_rules_empty(env):
    _seed(env)
    spec = spec_with(rules={})
    assert resolve_codes(spec, env.rd) == ["000001", "600519"]


def test_resolve_codes_rules_unknown_key_rejected(env):
    _seed(env)
    with pytest.raises(ValueError, match="未知 universe 规则"):
        resolve_codes(spec_with(rules={"min_list_day": 100}), env.rd)


def test_resolve_codes_rules_negative_min_list_days_rejected(env):
    _seed(env)
    with pytest.raises(ValueError, match="min_list_days 不能为负"):
        resolve_codes(spec_with(rules={"min_list_days": -1}), env.rd)


def test_resolve_codes_rules_min_list_days(env):
    # 600519 上市于 2001-08-27；date.start=2026-01-01 时两票均超 100 天
    _seed(env)
    spec = spec_with(rules={"min_list_days": 100})
    spec.date.start = "2026-01-01"
    assert resolve_codes(spec, env.rd) == ["000001", "600519"]


def test_resolve_codes_rules_min_list_days_filters_young(env):
    # 600519 上市于 2001-08-27：距 2002-01-01 不足 365 天 → 被过滤；000001 上市于 1991 → 保留
    _seed(env)
    spec = spec_with(rules={"min_list_days": 365})
    spec.date.start = "2002-01-01"
    assert resolve_codes(spec, env.rd) == ["000001"]


def test_resolve_codes_rules_min_list_days_from_daily(env):
    # 不设 date.start → 回退到 daily 最早 trade_date；600519 上市晚于该日期 → 被过滤
    _seed(env, append={"daily": [("000001.SZ", "20000104", 10.0)]})
    spec = spec_with(rules={"min_list_days": 100})
    assert resolve_codes(spec, env.rd) == ["000001"]


def test_resolve_codes_min_list_days_no_date_source_raises(env):
    # date.start 未设置且 daily 为空 → 无基准日期，明确报错
    _seed(env, tables={"daily": (_DAILY_COLS, [])})
    with pytest.raises(ValueError, match="date.start"):
        resolve_codes(spec_with(rules={"min_list_days": 100}), env.rd)


def test_resolve_codes_empty_result_error(env):
    _seed(env)
    with pytest.raises(ValueError, match="universe 无有效股票"):
        resolve_codes(spec_with(codes=["999999.SZ"]), env.rd)


def test_resolve_codes_reference_file(env, tmp_path):
    _seed(env)
    uni_dir = tmp_path / "universes"
    uni_dir.mkdir()
    (uni_dir / "research_50.yaml").write_text("codes: ['000001.SZ']", encoding="utf-8")
    from factorlab.config import Settings
    settings = Settings(universes_dir=uni_dir)
    spec = spec_with(ref="research_50")
    assert resolve_codes(spec, env.rd, settings=settings) == ["000001"]


def test_resolve_codes_reference_file_missing_keys(env, tmp_path):
    _seed(env)
    uni_dir = tmp_path / "universes"
    uni_dir.mkdir()
    (uni_dir / "empty.yaml").write_text("{}", encoding="utf-8")
    from factorlab.config import Settings
    settings = Settings(universes_dir=uni_dir)
    with pytest.raises(ValueError, match="必须包含 codes 或 rules"):
        resolve_codes(spec_with(ref="empty"), env.rd, settings=settings)


def test_resolve_codes_override_beats_spec(env):
    _seed(env)
    spec = spec_with(codes=["000001.SZ"])
    assert resolve_codes(spec, env.rd, override="600519") == ["600519"]


def test_resolve_codes_override_file_path(env, tmp_path):
    _seed(env)
    pool = tmp_path / "my_pool.yaml"
    pool.write_text("codes: ['600519']", encoding="utf-8")
    spec = spec_with(codes=["000001.SZ"])
    assert resolve_codes(spec, env.rd, override=str(pool)) == ["600519"]


def test_resolve_codes_missing_reference_file(env, tmp_path):
    _seed(env)
    from factorlab.config import Settings
    settings = Settings(universes_dir=tmp_path / "nope")
    with pytest.raises(FileNotFoundError):
        resolve_codes(spec_with(ref="ghost"), env.rd, settings=settings)


# ================================================================
# M6-07B4：rules universe 排除 legacy vendor aliases（T/TS 前缀历史残留）
# ================================================================

_ALIAS_TABLES = {
    "stock_basic": (_SB_COLS,
                    [("000001.SZ", "000001", "SZSE", "19910403", "银行", "主板"),
                     ("600018.SH", "600018", "SSE", "20061026", "港口", "主板"),
                     ("T600018.SH", "T600018", "SSE", "20000719", "港口", "主板"),
                     ("TS0018.SH", "TS0018", "SSE", "20000719", "港口", "主板")]),
}


def test_resolve_codes_rules_excludes_legacy_aliases(env):
    env.seed(_ALIAS_TABLES)
    spec = spec_with(rules={})
    assert resolve_codes(spec, env.rd) == ["000001", "600018"]


def test_resolve_candidate_codes_rules_excludes_legacy_aliases(env):
    env.seed(_ALIAS_TABLES)
    spec = spec_with(rules={"exchanges": ["SSE", "SZSE"]})
    assert resolve_candidate_codes(spec, env.rd) == ["000001", "600018"]


def test_resolve_codes_exchanges_by_suffix_excludes_aliases(env):
    env.seed(_ALIAS_TABLES)
    spec = spec_with(rules={"exchanges": ["SSE"]})
    assert resolve_codes(spec, env.rd) == ["600018"]
