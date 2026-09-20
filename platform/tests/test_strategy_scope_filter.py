"""R37 范围收窄（2026-09-20 用户裁定）：策略组合前 scope 过滤（addendum §4）。

规格：knowledge/design/platform/specs/2026-09-20-dq-scope-cut-addendum.md §4：
- 策略组合前对信号帧应用同一 scope 谓词（历史产物兼容：不重算信号也能保证
  不交易 BJ）。

断言来源 = 规格：信号帧含 .BJ 行 → 组合产物不含 .BJ；非 BJ 行选择/行序/schema
不变；全范围外信号 → fail fast（不静默空跑）。复用 test_run_strategy 的合成
产物构造（真 run_strategy 链，仅信号来源注入）。
"""
from __future__ import annotations

import polars as pl
import pytest

from factorlab.core.domain.frames import SignalArtifact, SignalMeta
from test_run_strategy import (_A, _B, _C, _CLOSES, _DATES, _load_doc,
                               _results_dir, _SIGNAL_NAME, _tables)

BJ = "830001.BJ"


def _tables_with_bj() -> dict:
    """_tables() + BJ 完整行情/参考（红阶段可跑完链，失败点 = target 含 BJ）。"""
    tables = _tables()
    tables["stock_basic"][1].append(
        ("830001", BJ, "BSE", "19910101", "其他", "北交所"))
    for i, d in enumerate(_DATES):
        ds = d.strftime("%Y%m%d")
        c = 10.0 + i
        tables["daily"][1].append(
            (BJ, ds, c - 1.0, c - 0.5, c - 1.5, c, c - 1.0, 1.0, 0.01,
             1000.0, 1e6))
        tables["adj_factor"][1].append((BJ, ds, 1.0))
        tables["daily_basic"][1].append((ds, BJ, 100.0))
        tables["stk_limit"][1].append(
            (BJ, ds, round((c - 1.0) * 1.1, 4), round((c - 1.0) * 0.9, 4)))
    return tables


def _signal_frame(*, with_bj: bool) -> pl.DataFrame:
    """A/B/C close 信号 +（可选）高信号 BJ 行（每日期首位 = 行序敏感）。"""
    rows = []
    for i, d in enumerate(_DATES):
        if with_bj:
            rows.append({"date": d, "code": BJ, "signal": 1_000_000.0})
        for c in (_A, _B, _C):
            rows.append({"date": d, "code": c, "signal": float(_CLOSES[c][i])})
    return pl.DataFrame(rows)


def _patch_signal(monkeypatch, state: dict) -> None:
    import factorlab.app.strategy.run as R

    def fake_loader(_path):
        return SignalArtifact(frame=state["frame"],
                              meta=SignalMeta(name=_SIGNAL_NAME))

    monkeypatch.setattr(R, "load_signal_artifact", fake_loader)


def test_strategy_drops_bj_before_construction_and_keeps_others(
        env, tmp_path, monkeypatch):
    """含 BJ 的历史信号 → target 无 BJ；非 BJ 行逐值等于无 BJ 对照。"""
    from factorlab.app.strategy import run_strategy
    import factorlab.app.strategy.run as R

    results = _results_dir(tmp_path)
    env.seed(_tables_with_bj())
    state = {"frame": _signal_frame(with_bj=True)}
    _patch_signal(monkeypatch, state)
    doc = _load_doc(tmp_path)

    seen: dict = {}
    real_ctor = R.construct_target_portfolio

    def spy_ctor(signal, spec, **kw):
        seen["frame"] = signal.frame
        return real_ctor(signal, spec, **kw)

    monkeypatch.setattr(R, "construct_target_portfolio", spy_ctor)
    res = run_strategy(doc, env.rd, dataset=None, results_dir=results)

    assert BJ not in res.target.frame["code"].to_list()
    # 过滤发生在 construct_target_portfolio 之前；schema 与行序保持（仅剔 BJ）
    window = _signal_frame(with_bj=True).filter(
        (pl.col("date") >= doc.date.start) & (pl.col("date") <= doc.date.end))
    assert seen["frame"].columns == window.columns
    assert seen["frame"].equals(window.filter(pl.col("code") != BJ))

    # 对照：同一信号去掉 BJ 行（组合选择逐帧一致 = 非 BJ 行不变）
    state["frame"] = _signal_frame(with_bj=False)
    ctrl = run_strategy(doc, env.rd, dataset=None,
                        results_dir=_results_dir(tmp_path, tag="_ctrl"))
    assert res.target.frame.equals(ctrl.target.frame)
    # 高信号 BJ 若未被过滤，d1/d2 Top-2 会挤掉 A（close 最高）——点名锁定
    rows_d1 = res.target.frame.filter(pl.col("decision_date") == _DATES[0])
    assert set(rows_d1["code"].to_list()) == {_A, _C}


def test_signal_only_bj_fails_fast(env, tmp_path, monkeypatch):
    """窗口内信号全为 .BJ → 显式报错、不落任何策略产物（不静默空跑）。"""
    from factorlab.app.strategy import run_strategy

    results = _results_dir(tmp_path)
    env.seed(_tables_with_bj())
    frame = pl.DataFrame({"date": _DATES, "code": [BJ] * len(_DATES),
                          "signal": [1.0] * len(_DATES)})
    state = {"frame": frame}
    _patch_signal(monkeypatch, state)
    doc = _load_doc(tmp_path)

    with pytest.raises(ValueError) as ei:
        run_strategy(doc, env.rd, dataset=None, results_dir=results)
    msg = str(ei.value)
    assert "ws7_doc" in msg and ("BJ" in msg or "范围" in msg)
    assert _SIGNAL_NAME in msg
    assert not (results / "strategies" / "ws7_doc").exists()
