"""R34/C4b 证据脚本（离线可复现；平台 venv 直接跑）：

    POLARS_MAX_THREADS=1 platform/.venv/bin/python \
      governance/evidence/verification/R34/c4b/run_c4b_checks.py

三部分（与 Plan CX-C4b 验收逐条对应）：
1. top_k_buffered vs top_k 同参对照换手（长 fixture，确定性排名振荡）；
2. market_cap_weighted 手算（mv 逐股/逐日可区分，权重 w=gross×mv/Σmv）；
3. CompositeArtifact 显式元数据（frequency/adjustment）+ name 目录校验往返；
   附 market_cap 缺值报错样例（显式 fail fast 文案）。
"""

from __future__ import annotations

import datetime
import json
import tempfile
from pathlib import Path

import polars as pl

from factorlab.app.composite.artifact import (read_composite_artifact,
                                              write_composite_artifact)
from factorlab.app.composite.resolver import MemberRef
from factorlab.app.composite.runtime import LoadedImpl
from factorlab.core.composite import CompositeSpec, definition_hash
from factorlab.core.composite.provenance import build_provenance
from factorlab.core.domain.frames import SignalArtifact, SignalMeta
from factorlab.core.strategy import SelectionSpec, StrategySpec, WeightingSpec
from factorlab.core.strategy.constructor import construct_target_portfolio

CODES = [f"0000{i:02d}.SZ" if i % 2 else f"6000{i:02d}.SH" for i in range(1, 13)]
D0 = datetime.date(2024, 1, 2)


def _days(n: int) -> list[datetime.date]:
    out, d = [], D0
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


def _oscillating_frame(days: list[datetime.date]) -> pl.DataFrame:
    """确定性排名振荡（无随机/无并列）：P/Q 两组每日互换 rank3..10 的交错名次，
    组内名次仅 ±1——top_k 的 rank3 每日翻转（churn 2/日），buffered（retain_k=6）
    的持仓始终留在缓冲带内（churn 0）。"""
    p, q = CODES[2:6], CODES[6:10]
    rows = []
    for t, d in enumerate(days):
        even = t % 2 == 0
        order: list[str] = []
        for a, b in zip(p, q):
            order += [a, b] if even else [b, a]
        block = {c: 50.0 - j for j, c in enumerate(order)}  # rank3..10 交错
        rows += [{"date": d, "code": CODES[0], "signal": 100.0},
                 {"date": d, "code": CODES[1], "signal": 99.0}]
        rows += [{"date": d, "code": c, "signal": v} for c, v in block.items()]
        rows += [{"date": d, "code": CODES[10], "signal": 10.0},
                 {"date": d, "code": CODES[11], "signal": 9.0}]
    return pl.DataFrame(rows)


def _signal(frame: pl.DataFrame) -> SignalArtifact:
    return SignalArtifact(frame=frame, meta=SignalMeta(name="alpha_c4b"))


def _members(tp) -> dict[datetime.date, set[str]]:
    return {d: set(tp.frame.filter(pl.col("decision_date") == d)["code"].to_list())
            for d in tp.decision_dates}


def _churn(tp) -> int:
    by_day = _members(tp)
    days = sorted(by_day)
    return sum(len(by_day[b] ^ by_day[a]) for a, b in zip(days, days[1:]))


def _base_spec(name: str, selection, weighting,
               gross: float = 1.0) -> StrategySpec:
    return StrategySpec(name=name, signal_name="alpha_c4b", direction=1,
                        selection=selection, weighting=weighting,
                        gross_exposure=gross)


def part1_buffered_turnover() -> dict:
    days = _days(60)
    sa = _signal(_oscillating_frame(days))
    k, enter_k, retain_k = 3, 3, 6
    topk = construct_target_portfolio(sa, _base_spec(
        "topk", SelectionSpec(method="top_k", k=k), WeightingSpec()))
    buf = construct_target_portfolio(sa, _base_spec(
        "buffered", SelectionSpec(method="top_k_buffered",
                                  enter_k=enter_k, retain_k=retain_k),
        WeightingSpec()))
    n_trans = len(days) - 1
    topk_churn, buf_churn = _churn(topk), _churn(buf)
    result = {
        "days": len(days), "codes": len(CODES), "k": k,
        "enter_k": enter_k, "retain_k": retain_k,
        "topk_member_changes": topk_churn,
        "buffered_member_changes": buf_churn,
        "topk_one_side_turnover_per_day": topk_churn / (2 * k * n_trans),
        "buffered_one_side_turnover_per_day": buf_churn / (2 * k * n_trans),
    }
    print(f"[1] buffered 换手对照（{len(days)} 决策日 × {len(CODES)} codes；"
          f"k={k}, enter_k={enter_k}, retain_k={retain_k}）")
    print(f"    top_k    : 成员变化 {topk_churn}，单边换手/日 "
          f"{result['topk_one_side_turnover_per_day']:.4f}")
    print(f"    buffered : 成员变化 {buf_churn}，单边换手/日 "
          f"{result['buffered_one_side_turnover_per_day']:.4f}")
    assert buf_churn < topk_churn, "buffered 必须低于 top_k"
    # 确定性：两次运行逐帧一致
    buf2 = construct_target_portfolio(sa, _base_spec(
        "buffered", SelectionSpec(method="top_k_buffered",
                                  enter_k=enter_k, retain_k=retain_k),
        WeightingSpec()))
    assert buf2.frame.equals(buf.frame), "buffered 必须确定性"
    print("    确定性：两次同输入逐帧一致 ✓")
    return result


def part2_market_cap_handcheck() -> dict:
    d = D0
    codes = ["000001.SZ", "000002.SZ", "000003.SZ", "600000.SH"]
    signals = [9.0, 8.0, 7.0, 6.0]        # Top-3 = 前 3 只
    mv = {"000001.SZ": 100.0, "000002.SZ": 300.0, "000003.SZ": 600.0}
    frame = pl.DataFrame({"date": [d] * 4, "code": codes, "signal": signals})
    mv_frame = pl.DataFrame({
        "date": [d] * 3, "code": list(mv), "total_mv": list(mv.values())})
    gross = 0.8
    tp = construct_target_portfolio(
        _signal(frame), _base_spec(
            "mv", SelectionSpec(method="top_k", k=3),
            WeightingSpec(method="market_cap_weighted"), gross=gross),
        market_cap=mv_frame)
    got = {r["code"]: r["target_weight"] for r in tp.frame.iter_rows(named=True)}
    total = sum(mv.values())
    expected = {c: gross * v / total for c, v in mv.items()}
    print(f"[2] market_cap_weighted 手算（gross={gross}，Σmv={total}）")
    for c in mv:
        print(f"    {c}: mv={mv[c]:>6.1f}  w={got[c]:.6f}  "
              f"手算={expected[c]:.6f}  {'✓' if abs(got[c]-expected[c]) < 1e-12 else '✗'}")
    assert abs(sum(got.values()) - gross) < 1e-12
    assert all(abs(got[c] - expected[c]) < 1e-12 for c in mv)

    # 缺值路径：选中股缺 mv → 显式 ValueError（打印原文）
    try:
        construct_target_portfolio(
            _signal(frame), _base_spec(
                "mv", SelectionSpec(method="top_k", k=3),
                WeightingSpec(method="market_cap_weighted")),
            market_cap=mv_frame.filter(pl.col("code") != "000002.SZ"))
    except ValueError as exc:
        print(f"    缺值路径报错原文: {exc}")
    else:
        raise AssertionError("缺 mv 必须 fail fast")
    return {"expected": expected, "got": got}


def part3_artifact_metadata() -> dict:
    with tempfile.TemporaryDirectory(prefix="c4b_evidence_") as td:
        root = Path(td) / "runs"
        out = root / "composites" / "cx_c4b"
        spec = CompositeSpec.model_validate({
            "name": "cx_c4b", "members": ["base_factor"],
            "implementation": {"entrypoint": "research.composites.impls.w_sum:compute"},
            "params": {"w": 1.0}})
        frame = pl.DataFrame({"date": [D0, D0], "code": CODES[:2],
                              "signal": [0.5, -0.5]})
        impl = LoadedImpl(compute=lambda X, params: X[:, 0],
                          source_hash="impl-hash",
                          entrypoint="research.composites.impls.w_sum:compute",
                          path=Path("/nonexistent.py"), git_commit=None)
        ref = MemberRef(position=1, member="base_factor", kind="factor",
                        name="base_factor", artifact_dir=root / "base_factor",
                        artifact_hash="member-hash-1", frame=frame,
                        value_col="signal", meta={})
        prov = build_provenance(spec, impl, spec.params, [ref], spec.alignment,
                                output_hash=None)
        write_composite_artifact(out, frame,
                                 {"name": "cx_c4b",
                                  "definition_hash": definition_hash(spec, ["member-hash-1"])},
                                 prov)
        doc = json.loads((out / "artifact.json").read_text(encoding="utf-8"))
        print("[3] artifact 元数据往返")
        print(f"    name={doc['name']!r} frequency={doc['frequency']!r} "
              f"adjustment={doc['adjustment']!r}")
        assert doc["frequency"] == "1d" and "adjustment" in doc and doc["adjustment"] is None
        _f, meta, _p = read_composite_artifact(out)
        assert meta["frequency"] == "1d" and meta["adjustment"] is None
        print("    read_composite_artifact 读回 frequency/adjustment ✓")

        doc["name"] = "other_name"
        (out / "artifact.json").write_text(json.dumps(doc), encoding="utf-8")
        try:
            read_composite_artifact(out)
        except ValueError as exc:
            print(f"    name 目录错配拒绝原文: {exc}")
        else:
            raise AssertionError("name 错配必须拒绝")
        return {"frequency": doc.get("frequency"), "name_check": "rejected"}


if __name__ == "__main__":
    r1 = part1_buffered_turnover()
    r2 = part2_market_cap_handcheck()
    r3 = part3_artifact_metadata()
    print("\n[summary]")
    print(json.dumps({"buffered": r1, "market_cap": r2, "artifact": r3},
                     ensure_ascii=False, indent=2, default=str))
