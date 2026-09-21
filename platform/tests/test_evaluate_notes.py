"""R03-I3：离散/重并列信号的分层评估静默 NaN → evaluate_run 生成 notes + 落盘标记。

设计依据（R03 挖矿报告 R03-I3 修法）：按"组内收益全 NaN / 组无有限收益"判定并进
notes（如 degenerate_groups / notes 列表），CLI/摘要显著提示（指引：高并列信号可
降组数或改用其他评估）；不要静默。
"""
from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from factorlab.app.context import RunContext
from factorlab.app.evaluate import evaluate_run
from factorlab.core.engine.compute import FactorResult
from factorlab.core.spec import FactorSpec, UniverseSpec


def _spec(name="r03_i3", outputs=None):
    return FactorSpec(name=name, category="custom", direction=1,
                      universe=UniverseSpec(codes=["000001.SZ"]),
                      formula="signal = close", outputs=outputs)


def _outcome(panel, spec=None):
    spec = spec or _spec()
    result = FactorResult(spec=spec, signal_artifact=None, label_artifact=None, panel=panel)
    return evaluate_run(result, spec, RunContext())


def _tie_heavy_panel(weeks=4, n=100):
    """小整数计数信号（60% 并列 0）→ average-rank 对称分位跳过多个 decile。"""
    rows = []
    for w in range(weeks):
        d = dt.date(2024, 1, 5) + dt.timedelta(weeks=w)
        for s in range(n):
            sig = 0.0 if s < 60 else (1.0 if s < 80 else (2.0 if s < 95 else 3.0))
            rows.append({"date": d, "code": f"{s:06d}", "signal": sig,
                         "forward_return_5d": 0.001 * sig + 1e-6 * s,
                         "forward_return_1d": 0.001 * sig + 1e-6 * s})
    return pl.DataFrame(rows)


def _continuous_panel(weeks=4, n=100):
    rows = []
    for w in range(weeks):
        d = dt.date(2024, 1, 5) + dt.timedelta(weeks=w)
        for s in range(n):
            sig = (s + 1) / n + w * 0.001
            rows.append({"date": d, "code": f"{s:06d}", "signal": sig,
                         "forward_return_5d": 0.001 * sig,
                         "forward_return_1d": 0.001 * sig})
    return pl.DataFrame(rows)


def test_evaluate_run_marks_degenerate_groups_and_note():
    outcome = _outcome(_tie_heavy_panel())
    dr = outcome.evaluation["decile_returns"]
    assert dr["degenerate_groups"] == [0, 1, 2, 4, 5, 6]
    # D2（R30 Task 1）后 layered 与 kernel 同 average-rank 分档——同一跳档事实
    # 在两处都显式化：kernel 空 decile note + layered 空档 note（都不静默）。
    assert len(outcome.notes) == 2
    note, bt_note = outcome.notes
    assert "全期无有效收益" in note
    assert "重并列" in note
    assert "建议降低分组数或改用其他评估口径" in note
    for g in (0, 1, 2, 4, 5, 6):
        assert str(g) in note
    assert bt_note.startswith("档位 ") and "全期无股票" in bt_note


def test_evaluate_run_no_false_positive_for_continuous_signal():
    outcome = _outcome(_continuous_panel())
    assert outcome.notes == []
    assert "degenerate_groups" not in outcome.evaluation["decile_returns"]


def _dated_panel(dates: tuple[dt.date, ...], n: int = 30) -> pl.DataFrame:
    """指定日期序列的连续信号面板（min/max 随参数变化，硬编码区间必败）。"""
    rows = []
    for d in dates:
        for s in range(n):
            sig = (s + 1) / n
            rows.append({"date": d, "code": f"{s:06d}", "signal": sig,
                         "forward_return_5d": 0.001 * sig,
                         "forward_return_1d": 0.001 * sig})
    return pl.DataFrame(rows)


@pytest.mark.parametrize("dates", [
    (dt.date(2023, 3, 14), dt.date(2023, 3, 21), dt.date(2023, 3, 30)),
    (dt.date(2021, 11, 2), dt.date(2021, 11, 9), dt.date(2021, 11, 17)),
])
def test_evaluate_run_records_panel_date_span(dates):
    """spec §7 line 136：evaluation 与 layered_backtest 记真实面板 date_start/date_end。

    面板日期参数化（两组不相交区间）——固定值存根必败；值 = 评估面板 min/max。
    """
    panel = _dated_panel(dates)
    expected = (panel["date"].min().isoformat(), panel["date"].max().isoformat())

    outcome = _outcome(panel)

    ev = outcome.evaluation
    assert (ev["date_start"], ev["date_end"]) == expected
    bt = ev["layered_backtest"]
    assert (bt["date_start"], bt["date_end"]) == expected


@pytest.mark.parametrize("dates", [
    (dt.date(2023, 3, 14), dt.date(2023, 3, 21), dt.date(2023, 3, 30)),
    (dt.date(2021, 11, 2), dt.date(2021, 11, 9), dt.date(2021, 11, 17)),
])
def test_evaluate_run_multi_output_records_panel_date_span(dates):
    """多输出路径同样逐输出记 date_start/date_end（顶层键结构不变）。"""
    panel = _dated_panel(dates)
    panel = panel.with_columns((pl.col("signal") * -1.0).alias("sig_neg")) \
                 .rename({"signal": "sig_pos"})
    expected = (panel["date"].min().isoformat(), panel["date"].max().isoformat())
    spec = _spec(outputs=["sig_pos", "sig_neg"])

    outcome = _outcome(panel, spec)

    ev = outcome.evaluation
    assert set(ev) == {"outputs", "frequency"}
    for name in ("sig_pos", "sig_neg"):
        ev_o = ev["outputs"][name]
        assert (ev_o["date_start"], ev_o["date_end"]) == expected
        bt = ev_o["layered_backtest"]
        assert (bt["date_start"], bt["date_end"]) == expected


def test_evaluate_run_multi_output_note_names_output():
    rows = _tie_heavy_panel().to_dicts()
    cont = {(r["date"], r["code"]): r["signal"] for r in _continuous_panel().to_dicts()}
    for r in rows:
        r["sig_cont"] = cont[(r["date"], r["code"])]
    panel = pl.DataFrame(rows).rename({"signal": "sig_tie"})
    spec = _spec(outputs=["sig_tie", "sig_cont"])
    outcome = _outcome(panel, spec)
    assert outcome.evaluation["outputs"]["sig_tie"]["decile_returns"]["degenerate_groups"] \
        == [0, 1, 2, 4, 5, 6]
    assert "degenerate_groups" not in \
        outcome.evaluation["outputs"]["sig_cont"]["decile_returns"]
    # D2 后跳出档的 layered 空档 note 同批出现（逐输出前缀一致，不误指其他输出）
    assert len(outcome.notes) == 2
    assert all(n.startswith("输出 sig_tie ") for n in outcome.notes)
