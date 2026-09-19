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
