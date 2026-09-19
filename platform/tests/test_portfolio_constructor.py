"""M7-02：construct_target_portfolio——SignalArtifact → Top-K Equal-Weight TargetPortfolio。"""

import datetime

import polars as pl
import pytest

from factorlab.core.domain import TargetPortfolio, TargetPortfolioMeta
from factorlab.core.domain.frames import LabelArtifact, SignalArtifact, SignalMeta
from factorlab.core.domain.timing import (DEFAULT_EOD_SIGNAL_TIMING, ExecutionTiming,
                                     InformationCutoff, SignalAvailability,
                                     SignalTiming)
from factorlab.core.strategy import SelectionSpec, StrategySpec, WeightingSpec
from factorlab.core.strategy.constructor import construct_target_portfolio

D1, D2, D3 = (datetime.date(2024, 1, 2), datetime.date(2024, 1, 3),
              datetime.date(2024, 1, 4))

CUSTOM_TIMING = SignalTiming(
    information_cutoff=InformationCutoff.OPEN,
    available_at=SignalAvailability.AT_OPEN,
    default_earliest_execution=ExecutionTiming.NEXT_CLOSE,
)


def _signal(frame=None, name="alpha_x", timing=DEFAULT_EOD_SIGNAL_TIMING,
            frequency="1d"):
    meta = SignalMeta(name=name, frequency=frequency, timing=timing,
                      adjustment="qfq")
    return SignalArtifact(frame=_frame() if frame is None else frame, meta=meta)


def _frame(rows=None, **over):
    base = {"date": pl.Series([D1, D1, D1, D1], dtype=pl.Date),
            "code": pl.Series(["000001.SZ", "000002.SZ", "600000.SH", "600001.SH"],
                              dtype=pl.String),
            "signal": pl.Series([10.0, 30.0, 20.0, None], dtype=pl.Float64)}
    base.update(over)
    if rows is not None:
        base = {k: v[:rows] if isinstance(v, pl.Series) else v for k, v in base.items()}
    return pl.DataFrame(base)


def _spec(**over):
    base = {"name": "strategy_x", "signal_name": "alpha_x", "direction": 1,
            "selection": {"method": "top_k", "k": 2},
            "weighting": {"method": "equal_weight"}}
    if "k" in over:   # k 快捷方式 → 合并进 selection
        base["selection"] = {**base["selection"], "k": over.pop("k")}
    if "method" in over:   # weighting.method 快捷方式
        base["weighting"] = {**base["weighting"], "method": over.pop("method")}
    if "gross" in over:   # gross 快捷方式 → gross_exposure
        over["gross_exposure"] = over.pop("gross")
    base.update(over)
    return StrategySpec.model_validate(base)


# ---------------- golden fixtures ----------------

def test_golden_direction_1():
    tp = construct_target_portfolio(_signal(), _spec())
    out = tp.frame.sort(["decision_date", "code"])
    assert out["code"].to_list() == ["000002.SZ", "600000.SH"]
    assert out["target_weight"].to_list() == [0.5, 0.5]


def test_golden_direction_minus_1():
    tp = construct_target_portfolio(_signal(), _spec(direction=-1))
    out = tp.frame.sort(["decision_date", "code"])
    assert out["code"].to_list() == ["000001.SZ", "600000.SH"]
    assert out["target_weight"].to_list() == [0.5, 0.5]


def test_golden_exact_tie_cutoff():
    f = pl.DataFrame({"date": pl.Series([D1, D1, D1, D1], dtype=pl.Date),
                      "code": pl.Series(["000001.SZ", "000002.SZ", "000003.SZ",
                                         "600000.SH"], dtype=pl.String),
                      "signal": pl.Series([10.0, 10.0, 10.0, 20.0], dtype=pl.Float64)})
    tp = construct_target_portfolio(_signal(frame=f), _spec(k=2))
    codes = tp.frame["code"].to_list()
    assert codes == ["000001.SZ", "600000.SH"]   # tie cutoff 处 code_asc 决定


def test_golden_insufficient_use_available():
    f = pl.DataFrame({"date": pl.Series([D1, D1], dtype=pl.Date),
                      "code": pl.Series(["000001.SZ", "000002.SZ"], dtype=pl.String),
                      "signal": pl.Series([10.0, 30.0], dtype=pl.Float64)})
    tp = construct_target_portfolio(_signal(frame=f), _spec(k=5, gross=0.8))
    out = tp.frame.sort(["decision_date", "code"])
    assert out["code"].to_list() == ["000001.SZ", "000002.SZ"]
    assert out["target_weight"].to_list() == [0.4, 0.4]


def test_golden_insufficient_all_cash():
    f = pl.DataFrame({"date": pl.Series([D1, D1], dtype=pl.Date),
                      "code": pl.Series(["000001.SZ", "000002.SZ"], dtype=pl.String),
                      "signal": pl.Series([10.0, 30.0], dtype=pl.Float64)})
    tp = construct_target_portfolio(_signal(frame=f),
                                    _spec(k=5, selection={"method": "top_k", "k": 5,
                                                          "on_insufficient": "all_cash"}))
    assert tp.frame.height == 0
    assert D1 in tp.decision_dates


def test_golden_multi_date_all_null():
    f = pl.DataFrame({"date": pl.Series([D1, D1, D2, D2, D3, D3], dtype=pl.Date),
                      "code": pl.Series(["000001.SZ", "600000.SH"] * 3, dtype=pl.String),
                      "signal": pl.Series([10.0, 20.0, None, None, 5.0, 8.0],
                                          dtype=pl.Float64)})
    tp = construct_target_portfolio(_signal(frame=f), _spec())
    assert tp.decision_dates == (D1, D2, D3)
    dates = tp.frame["decision_date"].unique().to_list()
    assert D2 not in dates          # all-null → all cash（日期仍在 decision_dates）
    assert D1 in dates and D3 in dates


# ---------------- input guards ----------------

def test_signal_name_mismatch_fails():
    with pytest.raises(ValueError, match="signal_name"):
        construct_target_portfolio(_signal(name="alpha_a"), _spec())


def test_wrong_signal_runtime_type_fails():
    for bad in (pl.DataFrame(), {"a": 1}, None, [1, 2]):
        with pytest.raises((TypeError, ValueError)):
            construct_target_portfolio(bad, _spec())


def test_label_artifact_rejected():
    la = LabelArtifact(frame=pl.DataFrame({
        "date": pl.Series([D1], dtype=pl.Date),
        "code": pl.Series(["000001.SZ"], dtype=pl.String),
        "forward_return_5d": pl.Series([0.01], dtype=pl.Float64)}))
    with pytest.raises((TypeError, ValueError)):
        construct_target_portfolio(la, _spec())


def test_raw_dataframe_rejected():
    with pytest.raises((TypeError, ValueError)):
        construct_target_portfolio(_frame(), _spec())


def test_wrong_spec_type_fails():
    with pytest.raises((TypeError, ValueError)):
        construct_target_portfolio(_signal(), {"name": "x"})


def test_frequency_incompatible_fails():
    with pytest.raises(ValueError, match="frequency|rebalance"):
        construct_target_portfolio(_signal(frequency="5d"), _spec())


# ---------------- canonical / non-finite ----------------

def test_noncanonical_code_fails():
    f = _frame().with_columns(pl.lit("T600018.SH").alias("code"))
    with pytest.raises(ValueError):
        construct_target_portfolio(_signal(frame=f), _spec())


def test_nonselected_noncanonical_still_fails():
    """非法 alias（signal=-999 永不入选）仍必须 fail whole construction。"""
    f = pl.DataFrame({"date": pl.Series([D1, D1], dtype=pl.Date),
                      "code": pl.Series(["600000.SH", "T600018.SH"], dtype=pl.String),
                      "signal": pl.Series([100.0, -999.0], dtype=pl.Float64)})
    with pytest.raises(ValueError):
        construct_target_portfolio(_signal(frame=f), _spec(k=1, direction=1))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_fails(bad):
    f = _frame().with_columns(pl.lit(bad).alias("signal"))
    with pytest.raises(ValueError):
        construct_target_portfolio(_signal(frame=f), _spec())


def test_nonfinite_nonselected_still_fails():
    f = pl.DataFrame({"date": pl.Series([D1, D1], dtype=pl.Date),
                      "code": pl.Series(["600000.SH", "000001.SZ"], dtype=pl.String),
                      "signal": pl.Series([100.0, float("nan")], dtype=pl.Float64)})
    with pytest.raises(ValueError):
        construct_target_portfolio(_signal(frame=f), _spec(k=1, direction=1))


# ---------------- ranking / selection ----------------

def test_null_dropped():
    tp = construct_target_portfolio(_signal(), _spec(k=3))
    assert "600001.SH" not in tp.frame["code"].to_list()


def test_n_equals_k():
    f = _frame().filter(pl.col("signal").is_not_null())
    tp = construct_target_portfolio(_signal(frame=f), _spec(k=3))
    assert tp.frame.height == 3


def test_n_greater_k():
    tp = construct_target_portfolio(_signal(), _spec(k=1))
    assert tp.frame.height == 1
    assert tp.frame["code"][0] == "000002.SZ"


def test_n_zero_use_available():
    f = pl.DataFrame({"date": pl.Series([D1, D1], dtype=pl.Date),
                      "code": pl.Series(["000001.SZ", "000002.SZ"], dtype=pl.String),
                      "signal": pl.Series([None, None], dtype=pl.Float64)})
    tp = construct_target_portfolio(_signal(frame=f), _spec())
    assert tp.frame.height == 0 and D1 in tp.decision_dates


def test_n_zero_all_cash():
    f = pl.DataFrame({"date": pl.Series([D1], dtype=pl.Date),
                      "code": pl.Series(["000001.SZ"], dtype=pl.String),
                      "signal": pl.Series([None], dtype=pl.Float64)})
    tp = construct_target_portfolio(_signal(frame=f), _spec(
        selection={"method": "top_k", "k": 5, "on_insufficient": "all_cash"}))
    assert tp.frame.height == 0 and D1 in tp.decision_dates


def test_shuffled_input_invariant():
    f1 = _frame()
    rng = [3, 0, 2, 1]
    f2 = f1[pl.Series(rng)]                 # 行 shuffle（polars 行索引）
    a = construct_target_portfolio(_signal(frame=f1), _spec())
    b = construct_target_portfolio(_signal(frame=f2), _spec())
    assert a.frame.equals(b.frame)
    assert a.decision_dates == b.decision_dates
    assert a.meta == b.meta


def test_multi_date_independence():
    f = pl.DataFrame({"date": pl.Series([D1, D1, D1, D2, D2, D2], dtype=pl.Date),
                      "code": pl.Series(["000001.SZ", "000002.SZ", "600000.SH"] * 2,
                                        dtype=pl.String),
                      "signal": pl.Series([10.0, 20.0, 30.0, 99.0, 5.0, 1.0],
                                          dtype=pl.Float64)})
    tp = construct_target_portfolio(_signal(frame=f), _spec(k=2))
    d1 = tp.frame.filter(pl.col("decision_date") == D1)["code"].to_list()
    d2 = tp.frame.filter(pl.col("decision_date") == D2)["code"].to_list()
    assert d1 == ["000002.SZ", "600000.SH"]
    assert d2 == ["000001.SZ", "000002.SZ"]   # 逐日独立


# ---------------- weighting / gross ----------------

def test_gross_1_and_equal_weights():
    tp = construct_target_portfolio(_signal(), _spec())
    assert tp.frame["target_weight"].to_list() == [0.5, 0.5]


def test_gross_0_8():
    tp = construct_target_portfolio(_signal(), _spec(gross=0.8))
    assert tp.frame["target_weight"].to_list() == [0.4, 0.4]


def test_equal_weights_exact_within_day():
    tp = construct_target_portfolio(_signal(), _spec(k=2, gross=0.6))
    ws = tp.frame["target_weight"].to_list()
    assert ws == [0.3, 0.3]


def test_no_cash_pseudo_row():
    tp = construct_target_portfolio(_signal(), _spec(gross=0.8))
    assert "CASH" not in tp.frame["code"].to_list()


def test_no_zero_weight_rows():
    tp = construct_target_portfolio(_signal(), _spec(k=2))
    assert (tp.frame["target_weight"] > 0).all()


# ---------------- dtypes ----------------

def test_integer_signal():
    f = _frame().with_columns(pl.col("signal").cast(pl.Int64))
    tp = construct_target_portfolio(_signal(frame=f), _spec())
    assert tp.frame["code"].to_list() == ["000002.SZ", "600000.SH"]
    assert tp.frame["target_weight"].dtype == pl.Float64


def test_float32_signal():
    f = _frame().with_columns(pl.col("signal").cast(pl.Float32))
    tp = construct_target_portfolio(_signal(frame=f), _spec())
    assert tp.frame["code"].to_list() == ["000002.SZ", "600000.SH"]


def test_float64_signal():
    tp = construct_target_portfolio(_signal(), _spec())
    assert tp.frame["code"].to_list() == ["000002.SZ", "600000.SH"]


# ---------------- output contract ----------------

def test_output_schema_exact():
    tp = construct_target_portfolio(_signal(), _spec())
    assert tp.frame.columns == ["decision_date", "code", "target_weight"]
    assert tp.frame.schema["decision_date"] == pl.Date
    assert tp.frame.schema["code"] == pl.String
    assert tp.frame.schema["target_weight"] == pl.Float64


def test_output_sorted():
    tp = construct_target_portfolio(_signal(), _spec())
    assert tp.frame.equals(tp.frame.sort(["decision_date", "code"]))


def test_domain_validator_accepts_output():
    tp = construct_target_portfolio(_signal(), _spec())
    TargetPortfolio(frame=tp.frame, decision_dates=tp.decision_dates, meta=tp.meta)


def test_meta_propagation():
    tp = construct_target_portfolio(_signal(), _spec(gross=0.8))
    m = tp.meta
    assert m.strategy_name == "strategy_x"
    assert m.source_signal_name == "alpha_x"
    assert m.gross_exposure == 0.8
    assert m.frequency == "1d"


def test_custom_timing_propagation():
    tp = construct_target_portfolio(_signal(timing=CUSTOM_TIMING), _spec())
    assert tp.meta.source_timing is CUSTOM_TIMING


def test_input_frame_unchanged():
    s = _signal()
    orig = s.frame.clone()
    construct_target_portfolio(s, _spec())
    assert s.frame.equals(orig)


def test_safe_extra_signal_columns_ignored():
    f1 = _frame()
    f2 = f1.with_columns(pl.lit(99.0).alias("close"),
                         pl.lit("x").alias("foo"))
    a = construct_target_portfolio(_signal(frame=f1), _spec())
    b = construct_target_portfolio(_signal(frame=f2), _spec())
    assert a.frame.equals(b.frame)


def test_empty_signal_artifact():
    f = pl.DataFrame({"date": pl.Series([], dtype=pl.Date),
                      "code": pl.Series([], dtype=pl.String),
                      "signal": pl.Series([], dtype=pl.Float64)})
    tp = construct_target_portfolio(_signal(frame=f), _spec())
    assert tp.frame.height == 0
    assert tp.decision_dates == ()
    assert tp.frame.schema["target_weight"] == pl.Float64
    TargetPortfolio(frame=tp.frame, decision_dates=(), meta=tp.meta)


# ================================================================
# M7-03：constructor 接入 Rebalance Scheduler
# ================================================================

def test_weekly_constructor_only_scheduled_dates():
    """weekly：只有 scheduled dates 形成 TargetPortfolio。"""
    dates = [datetime.date(2024, 1, 2), datetime.date(2024, 1, 3),
             datetime.date(2024, 1, 4), datetime.date(2024, 1, 5),
             datetime.date(2024, 1, 8), datetime.date(2024, 1, 9),
             datetime.date(2024, 1, 10), datetime.date(2024, 1, 11)]
    f = pl.DataFrame({"date": pl.Series([d for d in dates for _ in range(2)],
                                        dtype=pl.Date),
                      "code": pl.Series(["000001.SZ", "600000.SH"] * 8,
                                        dtype=pl.String),
                      "signal": pl.Series([10.0, 20.0] * 8, dtype=pl.Float64)})
    sa = _signal(frame=f)
    tp = construct_target_portfolio(sa, _spec(rebalance_frequency="weekly"))
    assert tp.decision_dates == (datetime.date(2024, 1, 5), datetime.date(2024, 1, 11))
    assert set(tp.frame["decision_date"].unique().to_list()) == \
        {datetime.date(2024, 1, 5), datetime.date(2024, 1, 11)}
    assert tp.meta.rebalance_frequency == "weekly"
    assert tp.meta.frequency == "1d"


def test_scheduled_all_null_explicit_all_cash():
    """scheduled Friday 全 null → all cash；不回退到周四。"""
    dates = [datetime.date(2024, 1, 2), datetime.date(2024, 1, 3),
             datetime.date(2024, 1, 4), datetime.date(2024, 1, 5)]
    f = pl.DataFrame({"date": pl.Series([d for d in dates for _ in range(2)],
                                        dtype=pl.Date),
                      "code": pl.Series(["000001.SZ", "600000.SH"] * 4,
                                        dtype=pl.String),
                      "signal": pl.Series([10.0, 20.0, 30.0, 40.0, 50.0, 60.0,
                                            None, None],
                                          dtype=pl.Float64)})
    sa = _signal(frame=f)
    tp = construct_target_portfolio(sa, _spec(rebalance_frequency="weekly"))
    # 该周最后 available date = 2024-01-05（Fri）——全 null → 0 rows（explicit all cash）
    assert datetime.date(2024, 1, 5) in tp.decision_dates
    assert tp.frame.height == 0


# ================================================================
# C4 T2（§19.2）：score_weighted——Top-K 后 s'=max(signal×direction,0)，
# w=gross×s'/Σs'；Σs'==0 → 当日 all-cash（不 fallback 等权）；long-only。
# ================================================================

def _score_signal(signals, codes=("000001.SZ", "000002.SZ", "600000.SH")):
    f = pl.DataFrame({
        "date": pl.Series([D1] * len(codes), dtype=pl.Date),
        "code": pl.Series(list(codes), dtype=pl.String),
        "signal": pl.Series(list(signals), dtype=pl.Float64)})
    return _signal(frame=f)


def test_score_weighted_hand_computed():
    """s=[2,1,-1]、top2 → w=[gross×2/3, gross×1/3]；负分第三只不建行。"""
    tp = construct_target_portfolio(_score_signal([2.0, 1.0, -1.0]),
                                    _spec(k=2, gross=0.9, method="score_weighted"))
    out = tp.frame.sort(["decision_date", "code"])
    assert out["code"].to_list() == ["000001.SZ", "000002.SZ"]
    assert out["target_weight"].to_list() == [pytest.approx(0.6),
                                              pytest.approx(0.3)]
    assert "600000.SH" not in out["code"].to_list()
    assert tp.frame.height == 2
    assert tp.meta.gross_exposure == 0.9


def test_score_weighted_selected_nonpositive_no_zero_row():
    """k=3 全选 s=[3,2,-1]：Σs' 只算正部（5）→ w=[3/5,2/5]，0 权重不建行。"""
    tp = construct_target_portfolio(_score_signal([3.0, 2.0, -1.0]),
                                    _spec(k=3, method="score_weighted"))
    out = tp.frame.sort(["decision_date", "code"])
    assert out["code"].to_list() == ["000001.SZ", "000002.SZ"]
    assert out["target_weight"].to_list() == [pytest.approx(0.6),
                                              pytest.approx(0.4)]
    assert (out["target_weight"] > 0).all()


def test_score_weighted_all_negative_all_cash():
    """全负分：Σs'==0 → 当日 all-cash（decision_date 在、0 rows，不 fallback 等权）。"""
    tp = construct_target_portfolio(_score_signal([-1.0, -2.0, -3.0]),
                                    _spec(k=2, method="score_weighted"))
    assert tp.frame.height == 0
    assert D1 in tp.decision_dates
    assert tp.frame.columns == ["decision_date", "code", "target_weight"]


def test_score_weighted_all_zero_all_cash():
    """全零分：Σs'==0 → all-cash（0 不是 null，选择照常，加权显式空仓）。"""
    tp = construct_target_portfolio(_score_signal([0.0, 0.0, 0.0]),
                                    _spec(k=2, method="score_weighted"))
    assert tp.frame.height == 0
    assert D1 in tp.decision_dates


def test_score_weighted_direction_flip_symmetry():
    """direction=-1 时 s=signal×(-1)：镜像选股 + 镜像权重。"""
    f = pl.DataFrame({
        "date": pl.Series([D1] * 4, dtype=pl.Date),
        "code": pl.Series(["000001.SZ", "000002.SZ", "600000.SH", "600001.SH"],
                          dtype=pl.String),
        "signal": pl.Series([2.0, 1.0, -2.0, -1.0], dtype=pl.Float64)})
    up = construct_target_portfolio(_signal(frame=f),
                                    _spec(k=2, method="score_weighted"))
    dn = construct_target_portfolio(_signal(frame=f),
                                    _spec(k=2, direction=-1, method="score_weighted"))
    up_out, dn_out = (x.frame.sort(["decision_date", "code"]) for x in (up, dn))
    assert up_out["code"].to_list() == ["000001.SZ", "000002.SZ"]
    assert dn_out["code"].to_list() == ["600000.SH", "600001.SH"]
    assert up_out["target_weight"].to_list() == [pytest.approx(2 / 3),
                                                 pytest.approx(1 / 3)]
    assert dn_out["target_weight"].to_list() == [pytest.approx(2 / 3),
                                                 pytest.approx(1 / 3)]


def test_score_weighted_differs_from_equal_weight_same_selection():
    """同一输入：选择集一致（tie/null 语义不变），仅权重公式不同。"""
    sa = _score_signal([2.0, 1.0, -1.0])
    eq = construct_target_portfolio(sa, _spec(k=2, method="equal_weight"))
    sw = construct_target_portfolio(sa, _spec(k=2, method="score_weighted"))
    eq_out = eq.frame.sort(["decision_date", "code"])
    sw_out = sw.frame.sort(["decision_date", "code"])
    assert eq_out["code"].to_list() == sw_out["code"].to_list()
    assert eq_out["target_weight"].to_list() == [0.5, 0.5]
    assert sw_out["target_weight"].to_list() == [pytest.approx(2 / 3),
                                                 pytest.approx(1 / 3)]


def test_score_weighted_null_dropped_not_zero_filled():
    """null 沿用既有 drop（不进入 ranking，也不补偿为 0 权重行）。"""
    tp = construct_target_portfolio(_score_signal([None, 2.0, 1.0]),
                                    _spec(k=2, method="score_weighted"))
    out = tp.frame.sort(["decision_date", "code"])
    assert out["code"].to_list() == ["000002.SZ", "600000.SH"]
    assert out["target_weight"].to_list() == [pytest.approx(2 / 3),
                                              pytest.approx(1 / 3)]


def test_score_weighted_tie_uses_code_asc():
    """exact tie 沿用既有 code_asc cutoff（不引入新 tie 规则）。"""
    f = pl.DataFrame({
        "date": pl.Series([D1] * 3, dtype=pl.Date),
        "code": pl.Series(["000001.SZ", "000002.SZ", "600000.SH"], dtype=pl.String),
        "signal": pl.Series([10.0, 10.0, 20.0], dtype=pl.Float64)})
    tp = construct_target_portfolio(_signal(frame=f),
                                    _spec(k=2, method="score_weighted"))
    out = tp.frame.sort(["decision_date", "code"])
    assert out["code"].to_list() == ["000001.SZ", "600000.SH"]
    assert out["target_weight"].to_list() == [pytest.approx(1 / 3),
                                              pytest.approx(2 / 3)]


def test_score_weighted_one_day_all_cash_other_days_kept():
    """逐日独立：全负日 all-cash，其余日照常加权。"""
    f = pl.DataFrame({
        "date": pl.Series([D1, D1, D1, D2, D2, D2], dtype=pl.Date),
        "code": pl.Series(["000001.SZ", "000002.SZ", "600000.SH"] * 2,
                          dtype=pl.String),
        "signal": pl.Series([2.0, 1.0, -1.0, -1.0, -2.0, -3.0],
                            dtype=pl.Float64)})
    tp = construct_target_portfolio(_signal(frame=f),
                                    _spec(k=2, method="score_weighted"))
    assert tp.decision_dates == (D1, D2)
    assert set(tp.frame["decision_date"].unique().to_list()) == {D1}
    d1 = tp.frame.sort(["decision_date", "code"])
    assert d1["code"].to_list() == ["000001.SZ", "000002.SZ"]
    assert d1["target_weight"].to_list() == [pytest.approx(2 / 3),
                                             pytest.approx(1 / 3)]


# ================================================================
# C4b T1：top_k_buffered——顺序式选择（前一期持仓 + enter_k/retain_k 缓冲）
# ================================================================

_A, _B, _C, _D, _E = ("000001.SZ", "000002.SZ", "000003.SZ",
                      "600000.SH", "600001.SH")
_D4 = datetime.date(2024, 1, 5)
_BUFF_DATES = (D1, D2, D3, _D4)


def _multi_day(day_signals: dict, codes=None):
    """day_signals: {date: {code: signal}} → 长表 SignalArtifact。"""
    codes = codes or list(next(iter(day_signals.values())))
    rows = [(d, c, day_signals[d].get(c))
            for d in sorted(day_signals) for c in codes]
    f = pl.DataFrame({
        "date": pl.Series([r[0] for r in rows], dtype=pl.Date),
        "code": pl.Series([r[1] for r in rows], dtype=pl.String),
        "signal": pl.Series([r[2] for r in rows], dtype=pl.Float64)})
    return _signal(frame=f)


def _buffered_spec(**over):
    base = {"selection": {"method": "top_k_buffered", "enter_k": 2, "retain_k": 3}}
    base.update(over.pop("spec_over", {}))
    if "enter_k" in over:
        base["selection"] = {**base["selection"], "enter_k": over.pop("enter_k")}
    if "retain_k" in over:
        base["selection"] = {**base["selection"], "retain_k": over.pop("retain_k")}
    return _spec(**base, **over)


def _churn(tp):
    """成员变化总数：相邻决策日 symmetric difference 之和。"""
    by_day = {d: set(tp.frame.filter(pl.col("decision_date") == d)["code"].to_list())
              for d in tp.decision_dates}
    days = sorted(by_day)
    return sum(len(by_day[b] ^ by_day[a]) for a, b in zip(days, days[1:]))


def _oscillating_signals():
    """A 恒 rank1；B/C 每日互换 rank2/3（top_k 每日翻转，buffered 缓冲不动）。"""
    return {
        D1: {_A: 10.0, _B: 9.0, _C: 8.0, _D: 7.0, _E: 6.0},
        D2: {_A: 10.0, _C: 9.0, _B: 8.0, _D: 7.0, _E: 6.0},
        D3: {_A: 10.0, _B: 9.0, _C: 8.0, _D: 7.0, _E: 6.0},
        _D4: {_A: 10.0, _C: 9.0, _B: 8.0, _D: 7.0, _E: 6.0},
    }


def test_buffered_turnover_lower_than_top_k_same_params():
    """同参对照：buffered（enter_k=2, retain_k=3）换手显著低于 top_k（k=2）。

    fixture 中 B/C 每日互换 rank2/3——top_k 每日翻转（churn=3），buffered 因
    B 始终在 retain_k 内而零换手。若 buffered 退化为逐日 top_k → 断言必红。
    """
    sa = _multi_day(_oscillating_signals())
    topk = construct_target_portfolio(sa, _spec(k=2))
    buffered = construct_target_portfolio(sa, _buffered_spec())
    assert _churn(topk) == 6, "对照 fixture 必须真换手（top_k 每日翻转，每次 swap 计 2）"
    assert _churn(buffered) == 0
    assert _churn(buffered) < _churn(topk)
    # buffered 逐日成员 = {A, B}（B rank3 仍在 retain_k=3 内，不换出）
    for d in _BUFF_DATES:
        assert set(buffered.frame.filter(
            pl.col("decision_date") == d)["code"].to_list()) == {_A, _B}


def test_buffered_member_stability_and_fill_from_enter_rank():
    """缓冲边界：持仓在 retain_k 内保留（即使 rank>enter_k）；rank>retain_k 掉出
    后从 enter_k 名次内补入（不是从全体候选）。"""
    sa = _multi_day({
        D1: {_A: 5.0, _B: 4.0, _C: 3.0, _D: 2.0, _E: 1.0},
        D2: {_A: 5.0, _C: 4.0, _B: 3.5, _D: 2.0, _E: 1.0},  # B rank3 缓冲内，C 不补入
        D3: {_A: 5.0, _C: 4.0, _D: 3.0, _B: 2.0, _E: 1.0},  # B rank4 掉出 → C 补入
        _D4: {_A: 5.0, _B: 4.0, _C: 3.0, _D: 2.0, _E: 1.0},  # B rank2 但无空位（不追涨）
    })
    tp = construct_target_portfolio(sa, _buffered_spec())
    got = {d: set(tp.frame.filter(pl.col("decision_date") == d)["code"].to_list())
           for d in _BUFF_DATES}
    assert got[D1] == {_A, _B}
    assert got[D2] == {_A, _B}, "rank3 在 retain_k=3 内必须保留（C rank2 不硬挤）"
    assert got[D3] == {_A, _C}, "rank4 > retain_k 掉出，空位从 enter_k=2 补入"
    assert got[_D4] == {_A, _C}, "无空位时不得追涨换入 B（缓冲不是动量）"


def test_buffered_null_holding_dropped_and_filled():
    """持仓当日 signal null（drop 语义）→ 掉出，由 enter_k 名次候选补位。"""
    sa = _multi_day({
        D1: {_A: 5.0, _B: 4.0, _C: 3.0},
        D2: {_A: 5.0, _B: None, _C: 4.0, _D: 3.0},
    }, codes=[_A, _B, _C, _D])
    tp = construct_target_portfolio(sa, _buffered_spec())
    d2 = set(tp.frame.filter(pl.col("decision_date") == D2)["code"].to_list())
    assert d2 == {_A, _C}


def test_buffered_determinism_same_input_and_shuffled_rows():
    """确定性：两次同输入逐帧相等；行序 shuffle 不改变结果（code_asc tie）。"""
    sa = _multi_day(_oscillating_signals())
    a = construct_target_portfolio(sa, _buffered_spec())
    b = construct_target_portfolio(sa, _buffered_spec())
    assert a.frame.equals(b.frame)

    f = sa.frame
    shuffled = f[pl.Series([3, 0, 7, 1, 9, 2, 5, 8, 4, 6, 11, 10, 13, 12, 14, 15, 16, 17, 18, 19][:f.height])]
    c = construct_target_portfolio(_signal(frame=shuffled), _buffered_spec())
    assert c.frame.equals(a.frame)


def test_buffered_all_cash_day_resets_holdings():
    """全 null 日（显式 all-cash）清空持仓状态：次日重新从 top enter_k 建仓，
    不得携带幽灵持仓。"""
    sa = _multi_day({
        D1: {_A: 5.0, _B: 4.0, _C: 3.0},
        D2: {_A: None, _B: None, _C: None},
        D3: {_A: 2.0, _B: 5.0, _C: 4.0},
    })
    tp = construct_target_portfolio(sa, _buffered_spec())
    assert set(tp.frame.filter(pl.col("decision_date") == D1)["code"].to_list()) == {_A, _B}
    assert tp.frame.filter(pl.col("decision_date") == D2).height == 0
    assert D2 in tp.decision_dates
    # 若状态未清：A（D3 rank3）会被保留 → {A,B}；正确结果 = 全新 top2 {B,C}
    assert set(tp.frame.filter(pl.col("decision_date") == D3)["code"].to_list()) == {_B, _C}


def test_buffered_insufficient_all_cash_resets_holdings():
    """on_insufficient=all_cash：当日候选 < enter_k → 显式空仓并清空持仓状态。"""
    sa = _multi_day({
        D1: {_A: 5.0, _B: 4.0, _C: 3.0},
        D2: {_A: 5.0, _B: None, _C: None},
        D3: {_A: 2.0, _B: 5.0, _C: 4.0},
    })
    tp = construct_target_portfolio(
        sa, _buffered_spec(spec_over={"selection": {
            "method": "top_k_buffered", "enter_k": 2, "retain_k": 3,
            "on_insufficient": "all_cash"}}))
    assert tp.frame.filter(pl.col("decision_date") == D2).height == 0
    assert set(tp.frame.filter(pl.col("decision_date") == D3)["code"].to_list()) == {_B, _C}


def test_buffered_use_available_caps_target_count():
    """use_available：当日可用数 < enter_k 时目标仓位=可用数；持仓不在当日
    候选中（null drop）即掉出。"""
    sa = _multi_day({
        D1: {_A: 5.0, _B: 4.0, _C: 3.0},
        D2: {_A: 5.0, _B: None, _C: None},
    })
    tp = construct_target_portfolio(sa, _buffered_spec())
    d2 = tp.frame.filter(pl.col("decision_date") == D2)
    assert d2["code"].to_list() == [_A]


def test_buffered_equal_weight_and_sparse_output():
    """权重与输出契约沿用既有语义：equal=gross/M；0 权重不建行；排序稳定。"""
    sa = _multi_day({
        D1: {_A: 5.0, _B: 4.0, _C: 3.0},
        D2: {_A: 5.0, _C: 4.0, _B: 3.5},
    })
    tp = construct_target_portfolio(sa, _buffered_spec(gross=0.8))
    assert tp.frame.height == 4
    assert set(tp.frame["target_weight"].to_list()) == {0.4}
    assert (tp.frame["target_weight"] > 0).all()
    assert tp.frame.equals(tp.frame.sort(["decision_date", "code"]))
    assert tp.frame.columns == ["decision_date", "code", "target_weight"]
    assert tp.meta.gross_exposure == 0.8
    assert tp.decision_dates == (D1, D2)


def test_buffered_tie_code_asc_deterministic():
    """exact tie 沿用 code_asc：D1 三人同为 10 → 选 code 最小的两人；D2 保持。"""
    sa = _multi_day({
        D1: {_A: 10.0, _B: 10.0, _C: 10.0},
        D2: {_A: 10.0, _B: 10.0, _C: 10.0},
    })
    tp = construct_target_portfolio(sa, _buffered_spec())
    for d in (D1, D2):
        assert tp.frame.filter(
            pl.col("decision_date") == d)["code"].to_list() == [_A, _B]


def test_buffered_score_weighted_combination():
    """buffered 选择 + score_weighted 加权（正交组合：选择按缓冲，权重按分数正部）。"""
    sa = _multi_day({
        D1: {_A: 2.0, _B: 1.0, _C: -1.0},
        D2: {_A: 2.0, _B: 1.0, _C: -1.0},
    })
    tp = construct_target_portfolio(sa, _buffered_spec(method="score_weighted"))
    d1 = tp.frame.sort(["decision_date", "code"]).filter(
        pl.col("decision_date") == D1)
    assert d1["code"].to_list() == [_A, _B]
    assert d1["target_weight"].to_list() == [pytest.approx(2 / 3),
                                             pytest.approx(1 / 3)]


def test_buffered_score_weighted_zero_score_row_not_held():
    """s'=0 的入选名不建行、也不占缓冲位：其后转正需经 enter_k 空位补入，
    不因"曾入选"被自动保留（持有状态 = 实际建仓行，不是选择集）。"""
    sa = _multi_day({
        D1: {_A: 5.0, _B: 4.0, _C: 3.0, _D: 2.0},
        D2: {_A: 5.0, _C: 4.5, _B: 0.0, _D: 2.0},
        D3: {_A: 5.0, _C: 4.5, _D: 4.4, _B: 1.0},
    })
    tp = construct_target_portfolio(sa, _buffered_spec(
        enter_k=2, retain_k=4, method="score_weighted"))
    d2 = set(tp.frame.filter(pl.col("decision_date") == D2)["code"].to_list())
    d3 = set(tp.frame.filter(pl.col("decision_date") == D3)["code"].to_list())
    assert d2 == {_A}, "s'=0 不建行（sparse）"
    assert d3 == {_A, _C}, "B 未建仓 → 空位按 enter_k=2 补 C；B 占缓冲位则必红"


# ================================================================
# C4b T2：market_cap_weighted——PIT total_mv join，w ∝ mv（gross 内归一）
# ================================================================

def _mv_frame(rows):
    """rows: [(date, code, total_mv|None)] → (date, code, total_mv) 面板。"""
    return pl.DataFrame({
        "date": pl.Series([r[0] for r in rows], dtype=pl.Date),
        "code": pl.Series([r[1] for r in rows], dtype=pl.String),
        "total_mv": pl.Series([r[2] for r in rows], dtype=pl.Float64)})


def test_market_cap_weighted_hand_computed():
    """Top-2 {A(20), B(30)}、gross=0.8 → w=[0.8×20/50, 0.8×30/50]=[0.32,0.48]。"""
    sa = _score_signal([2.0, 1.0, 0.5], codes=(_A, _B, _C))
    mv = _mv_frame([(D1, _A, 20.0), (D1, _B, 30.0), (D1, _C, 999.0)])
    tp = construct_target_portfolio(
        sa, _spec(k=2, gross=0.8, method="market_cap_weighted"), market_cap=mv)
    out = tp.frame.sort(["decision_date", "code"])
    assert out["code"].to_list() == [_A, _B]
    assert out["target_weight"].to_list() == [pytest.approx(0.32),
                                              pytest.approx(0.48)]
    assert tp.frame.height == 2


def test_market_cap_weighted_selection_still_signal_driven():
    """选择由 signal 决定（mv 只影响权重）：k=1 选 signal 最高者，即使其 mv 最小。"""
    sa = _score_signal([3.0, 1.0, 0.1], codes=(_A, _B, _C))
    mv = _mv_frame([(D1, _A, 1.0), (D1, _B, 100.0), (D1, _C, 50.0)])
    tp = construct_target_portfolio(
        sa, _spec(k=1, method="market_cap_weighted"), market_cap=mv)
    assert tp.frame["code"].to_list() == [_A]
    assert tp.frame["target_weight"].to_list() == [pytest.approx(1.0)]


def test_market_cap_weighted_join_by_date():
    """PIT 对齐：各决策日独立取当日 mv（D1 A=10/B=30 → [0.2,0.6]；
    D2 A=40/B=10 → [0.64,0.16]）；若错用单一日期 mv → 断言必红。"""
    f = pl.DataFrame({
        "date": pl.Series([D1, D1, D2, D2], dtype=pl.Date),
        "code": pl.Series([_A, _B, _A, _B], dtype=pl.String),
        "signal": pl.Series([2.0, 1.0, 2.0, 1.0], dtype=pl.Float64)})
    mv = _mv_frame([(D1, _A, 10.0), (D1, _B, 30.0),
                    (D2, _A, 40.0), (D2, _B, 10.0)])
    tp = construct_target_portfolio(
        _signal(frame=f), _spec(k=2, gross=0.8, method="market_cap_weighted"),
        market_cap=mv)
    out = tp.frame.sort(["decision_date", "code"])
    assert out["target_weight"].to_list() == [pytest.approx(0.2),
                                              pytest.approx(0.6),
                                              pytest.approx(0.64),
                                              pytest.approx(0.16)]


def test_market_cap_weighted_direction_minus_one():
    """direction=-1：选择最低分，权重仍由正市值决定。"""
    sa = _score_signal([3.0, 1.0, 0.5], codes=(_A, _B, _C))
    mv = _mv_frame([(D1, _A, 1.0), (D1, _B, 9.0), (D1, _C, 3.0)])
    tp = construct_target_portfolio(
        sa, _spec(k=2, direction=-1, method="market_cap_weighted"), market_cap=mv)
    out = tp.frame.sort(["decision_date", "code"])
    assert out["code"].to_list() == [_B, _C]
    assert out["target_weight"].to_list() == [pytest.approx(0.75),
                                              pytest.approx(0.25)]


def test_market_cap_weighted_missing_selected_row_fails():
    """缺 mv（选中股无行）→ 显式 ValueError（点名 date+code），不静默剔除。"""
    sa = _score_signal([2.0, 1.0, 0.5], codes=(_A, _B, _C))
    mv = _mv_frame([(D1, _A, 20.0)])   # B/C 缺
    with pytest.raises(ValueError) as ei:
        construct_target_portfolio(
            sa, _spec(k=2, method="market_cap_weighted"), market_cap=mv)
    msg = str(ei.value)
    assert _B in msg and str(D1) in msg


@pytest.mark.parametrize("bad", [None, 0.0, -5.0, float("nan"), float("inf")])
def test_market_cap_weighted_invalid_selected_mv_fails(bad):
    """选中股 total_mv 为 null/非正/非 finite → 显式 ValueError（不伪装零权重）。"""
    sa = _score_signal([2.0, 1.0, 0.5], codes=(_A, _B, _C))
    mv = _mv_frame([(D1, _A, 20.0), (D1, _B, bad), (D1, _C, 5.0)])
    with pytest.raises(ValueError) as ei:
        construct_target_portfolio(
            sa, _spec(k=2, method="market_cap_weighted"), market_cap=mv)
    assert _B in str(ei.value)


def test_market_cap_weighted_non_selected_invalid_mv_ignored():
    """未被选中的 code 缺 mv 不影响结果（join 只需覆盖选中集）。"""
    sa = _score_signal([2.0, 1.0, 0.5], codes=(_A, _B, _C))
    mv = _mv_frame([(D1, _A, 20.0), (D1, _B, 30.0)])   # C 无行且未被选
    tp = construct_target_portfolio(
        sa, _spec(k=2, method="market_cap_weighted"), market_cap=mv)
    assert set(tp.frame["code"].to_list()) == {_A, _B}


def test_market_cap_weighted_panel_required():
    """weighting=market_cap_weighted 未传面板 → fail fast（app 层接线错误的守卫）。"""
    with pytest.raises(ValueError, match="market_cap"):
        construct_target_portfolio(_score_signal([2.0, 1.0, 0.5]),
                                   _spec(k=2, method="market_cap_weighted"))


def test_market_cap_panel_rejected_for_other_methods():
    """非 mv 加权却传面板 → fail fast（防串线静默忽略）。"""
    mv = _mv_frame([(D1, _A, 20.0)])
    for method in ("equal_weight", "score_weighted"):
        with pytest.raises(ValueError, match="market_cap"):
            construct_target_portfolio(_score_signal([2.0, 1.0, 0.5]),
                                       _spec(k=2, method=method), market_cap=mv)


def test_market_cap_panel_schema_and_duplicates_validated():
    """面板列契约与 (date,code) 唯一性：错列 / 重复键 → fail fast。"""
    sa = _score_signal([2.0, 1.0, 0.5], codes=(_A, _B, _C))
    bad_cols = pl.DataFrame({"date": pl.Series([D1], dtype=pl.Date),
                             "code": pl.Series([_A], dtype=pl.String)})
    with pytest.raises(ValueError, match="total_mv"):
        construct_target_portfolio(sa, _spec(k=1, method="market_cap_weighted"),
                                   market_cap=bad_cols)
    dup = _mv_frame([(D1, _A, 20.0), (D1, _A, 21.0), (D1, _B, 30.0)])
    with pytest.raises(ValueError, match="重复"):
        construct_target_portfolio(sa, _spec(k=1, method="market_cap_weighted"),
                                   market_cap=dup)


def test_market_cap_panel_column_order_agnostic():
    """列集合正确但顺序不同 → 内部重排后同结果（防 iter_rows 解包错位静默错算）。"""
    sa = _score_signal([2.0, 1.0, 0.5], codes=(_A, _B, _C))
    mv = _mv_frame([(D1, _A, 20.0), (D1, _B, 30.0)]).select(
        ["code", "total_mv", "date"])
    tp = construct_target_portfolio(
        sa, _spec(k=2, gross=0.8, method="market_cap_weighted"), market_cap=mv)
    out = tp.frame.sort(["decision_date", "code"])
    assert out["target_weight"].to_list() == [pytest.approx(0.32),
                                              pytest.approx(0.48)]


def test_market_cap_weighted_buffered_combination():
    """buffered 选择 + market_cap 加权正交组合：D2 保留 B（rank3）并按 D2 mv 加权。"""
    sa = _multi_day({
        D1: {_A: 10.0, _B: 9.0, _C: 8.0},
        D2: {_A: 10.0, _C: 9.0, _B: 8.0},
    })
    mv = _mv_frame([(D1, _A, 10.0), (D1, _B, 30.0),
                    (D2, _A, 30.0), (D2, _B, 10.0)])
    tp = construct_target_portfolio(sa, _buffered_spec(method="market_cap_weighted"),
                                    market_cap=mv)
    d2 = tp.frame.sort(["decision_date", "code"]).filter(pl.col("decision_date") == D2)
    assert d2["code"].to_list() == [_A, _B]
    assert d2["target_weight"].to_list() == [pytest.approx(0.75),
                                             pytest.approx(0.25)]
