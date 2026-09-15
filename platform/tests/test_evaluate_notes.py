"""R03-I3：离散/重并列信号的分层评估静默 NaN → evaluate_run 生成 notes + 落盘标记。

设计依据（R03 挖矿报告 R03-I3 修法）：按"组内收益全 NaN / 组无有限收益"判定并进
notes（如 degenerate_groups / notes 列表），CLI/摘要显著提示（指引：高并列信号可
降组数或改用其他评估）；不要静默。
"""
from __future__ import annotations

import datetime as dt

import polars as pl

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
                         "forward_return_5d": 0.001 * sig + 1e-6 * s})
    return pl.DataFrame(rows)


def _continuous_panel(weeks=4, n=100):
    rows = []
    for w in range(weeks):
        d = dt.date(2024, 1, 5) + dt.timedelta(weeks=w)
        for s in range(n):
            sig = (s + 1) / n + w * 0.001
            rows.append({"date": d, "code": f"{s:06d}", "signal": sig,
                         "forward_return_5d": 0.001 * sig})
    return pl.DataFrame(rows)


def test_evaluate_run_marks_degenerate_groups_and_note():
    outcome = _outcome(_tie_heavy_panel())
    dr = outcome.evaluation["decile_returns"]
    assert dr["degenerate_groups"] == [0, 1, 2, 4, 5, 6]
    assert len(outcome.notes) == 1
    note = outcome.notes[0]
    assert "全期无有效收益" in note
    assert "重并列" in note
    assert "建议降低分组数或改用其他评估口径" in note
    for g in (0, 1, 2, 4, 5, 6):
        assert str(g) in note


def test_evaluate_run_no_false_positive_for_continuous_signal():
    outcome = _outcome(_continuous_panel())
    assert outcome.notes == []
    assert "degenerate_groups" not in outcome.evaluation["decile_returns"]


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
    assert len(outcome.notes) == 1
    assert outcome.notes[0].startswith("输出 sig_tie ")
