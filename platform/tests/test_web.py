import asyncio
import json

import polars as pl
import pytest
from fastapi.testclient import TestClient

from factorlab.surfaces.web.app import create_app


def _raw_get(app, path):
    """直连 ASGI scope 发起 GET——绕过 httpx 客户端路径规范化（等价真实服务器/curl 的原始路径）。"""
    scope = {"type": "http", "method": "GET", "path": path, "raw_path": path.encode(),
             "query_string": b"", "headers": [], "client": ("127.0.0.1", 1),
             "server": ("test", 80), "scheme": "http", "root_path": "",
             "http_version": "1.1", "app": app}
    res = {"status": None, "body": b""}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            res["status"] = msg["status"]
        elif msg["type"] == "http.response.body":
            res["body"] += msg["body"]

    asyncio.run(app(scope, receive, send))
    return res["status"], res["body"].decode("utf-8", errors="replace")


def _write_factor(results_dir, name, with_weekly=True, with_layered=True):
    out = results_dir / name
    out.mkdir(parents=True, exist_ok=True)
    ev = {
        "ic": {"mean": 0.05, "t_stat": 1.5},
        "decile_returns": {"spread": {"ret": 0.02}, "groups": [
            {"group": 1, "mean_ret": 0.03}, {"group": 2, "mean_ret": 0.01}]},
        "turnover": {"monthly": 0.1},
        "coverage": {"pct_valid": 0.9},
    }
    if with_layered:
        ev["layered_backtest"] = {"periods": 2, "net_values": {"D1": [1.0, 1.01], "D10": [1.0, 0.99],
                                                              "long_short": [0.0, 0.02]},
                                  "summary": {"D1": {"annual_return": 0.5}}}
    summary = {
        "name": name, "category": "custom", "direction": 1,
        "universe_count": 5, "date_start": "2024-01-01", "date_end": "2025-12-31",
        "panel_rows": 100, "signal_null_ratio": 0.04,
        "spec_yaml": "name: demo\nformula: signal = close",
        "evaluation": ev,
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    if with_weekly:
        rows = []
        for w, d in enumerate(["2024-01-05", "2024-01-12"]):
            for s in range(10):
                # signal 与 fwd 单调（IC 可计算；R01-EVAL-I9 的数值断言依赖它）
                rows.append({"date": d, "code": f"{s:06d}", "signal": float(s),
                             "forward_return_5d": float(s) * 0.01})
        pl.DataFrame(rows).write_parquet(out / "weekly.parquet")


def test_index_lists_factors(tmp_path):
    _write_factor(tmp_path, "alpha_1")
    _write_factor(tmp_path, "beta_2")
    client = TestClient(create_app(results_dir=tmp_path))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "alpha_1" in resp.text and "beta_2" in resp.text


def test_index_empty_results(tmp_path):
    client = TestClient(create_app(results_dir=tmp_path / "nope"))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "暂无" in resp.text


def test_factor_detail_contains_charts(tmp_path):
    _write_factor(tmp_path, "alpha_1")
    client = TestClient(create_app(results_dir=tmp_path))
    resp = client.get("/factor/alpha_1")
    assert resp.status_code == 200
    # 图表数据（ic 曲线/十分位/净值）内嵌
    assert "0.05" in resp.text  # ic mean
    assert "Plotly" in resp.text or "plotly" in resp.text
    assert "net_values" in resp.text or "long_short" in resp.text


def test_factor_missing_404(tmp_path):
    client = TestClient(create_app(results_dir=tmp_path))
    resp = client.get("/factor/ghost")
    assert resp.status_code == 404


def test_factor_detail_without_weekly(tmp_path):
    _write_factor(tmp_path, "no_weekly", with_weekly=False)
    client = TestClient(create_app(results_dir=tmp_path))
    resp = client.get("/factor/no_weekly")
    assert resp.status_code == 200  # IC 曲线区域降级，其余照常


def test_factor_corrupt_summary_404(tmp_path):
    out = tmp_path / "broken"
    out.mkdir(parents=True)
    (out / "summary.json").write_text("{not json", encoding="utf-8")
    client = TestClient(create_app(results_dir=tmp_path))
    resp = client.get("/factor/broken")
    assert resp.status_code == 404
    # 列表页跳过损坏 summary，不中断
    assert "broken" not in client.get("/").text


def test_factor_detail_missing_evaluation_fields(tmp_path):
    # summary 缺 evaluation.ic/layered_backtest 等字段 → 页面不崩溃、降级展示
    out = tmp_path / "sparse"
    out.mkdir(parents=True)
    (out / "summary.json").write_text(json.dumps({
        "name": "sparse", "category": "custom", "direction": 1,
        "universe_count": 5, "date_start": "2024-01-01", "date_end": "2025-12-31",
        "panel_rows": 100, "signal_null_ratio": 0.04,
        "spec_yaml": "name: sparse\nformula: signal = close",
        "evaluation": {"decile_returns": {"groups": [{"group": 1, "mean_ret": 0.03}]}},
    }, ensure_ascii=False), encoding="utf-8")
    rows = []
    for d in ["2024-01-05", "2024-01-12"]:
        for s in range(10):
            rows.append({"date": d, "code": f"{s:06d}", "signal": float(s), "forward_return_5d": 0.01})
    pl.DataFrame(rows).write_parquet(out / "weekly.parquet")
    client = TestClient(create_app(results_dir=tmp_path))
    resp = client.get("/factor/sparse")
    assert resp.status_code == 200
    assert "sparse" in resp.text
    assert "decile-chart" in resp.text  # 有数据的图表照常渲染


def test_factor_name_path_traversal_rejected(tmp_path):
    """CWE-22 回归：因子名含路径穿越片段/分隔符 → 一律 404，不泄露 results_dir 之外的文件。"""
    results_dir = tmp_path / "results"
    _write_factor(results_dir, "alpha_1")
    # results_dir 上一级放一个可读目标目录（模拟越权读取的 summary）
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "summary.json").write_text(json.dumps(
        {"name": "victim", "spec_yaml": "TOP-SECRET"}, ensure_ascii=False), encoding="utf-8")
    app = create_app(results_dir=results_dir)
    client = TestClient(app)
    # TestClient 会把 %5c/%2e 解码后交给处理器——这些变体必须被服务端拒绝
    # （".." 会被 httpx 客户端规范化为 "/" 重写为首页，故在下方 RAW scope 中覆盖）
    for bad in ("%2e%2e", "..%5cvictim", "..%5c..%5c", "..%5cvictim%5csummary.json",
                "../victim", "..%2fvictim", "a/b", "a\\b", "a%5cb", "C%3asecret"):
        resp = client.get(f"/factor/{bad}")
        assert resp.status_code == 404, f"/factor/{bad} 应 404（实际 {resp.status_code}）"
        assert "TOP-SECRET" not in resp.text
    # 直连 ASGI scope：真实服务器会收到原始路径（curl 的原始反斜杠、裸 ".."）
    for path in ("/factor/..", "/factor/..\\victim", "/factor/a\\b"):
        status, body = _raw_get(app, path)
        assert status == 404, f"RAW {path} 应 404（实际 {status}）"
        assert "TOP-SECRET" not in body
    # 正常因子不受影响
    assert client.get("/factor/alpha_1").status_code == 200


def test_factor_detail_corrupt_weekly_degrade(tmp_path):
    # 损坏的 weekly.parquet → IC 曲线区域降级为"无周频数据"（不 500，其余图表照常）
    _write_factor(tmp_path, "broken", with_weekly=True)
    (tmp_path / "broken" / "weekly.parquet").write_bytes(b"not a parquet file")
    client = TestClient(create_app(results_dir=tmp_path))
    resp = client.get("/factor/broken")
    assert resp.status_code == 200
    assert "无周频数据" in resp.text
    assert "decile-chart" in resp.text


def test_factor_detail_has_correlation_block(tmp_path):
    """详情页含相关热力图区块（库内有多因子时）。

    correlation 模块"有效周"语义要求每周横截面 ≥30 只（WS2 修正：不足 →
    rank_corr=nan 非 0.0，web 对 nan 对不展示）——fixture 每期 40 只保证有
    有效周，区块才应渲染。"""
    import polars as pl
    _write_factor(tmp_path, "alpha_1")
    # alpha_1 补 panel.parquet（corr 逻辑数据源）
    rows = []
    for w, d in enumerate(["2024-01-05", "2024-01-12"]):
        for s in range(40):
            rows.append({"date": d, "code": f"{s:06d}", "signal": float(s)})
    pl.DataFrame(rows).with_columns(
        pl.col("date").str.to_date()).write_parquet(tmp_path / "alpha_1" / "panel.parquet")
    # 第二个因子：只有 panel
    out2 = tmp_path / "beta_2"
    out2.mkdir()
    rows2 = []
    for w, d in enumerate(["2024-01-05", "2024-01-12"]):
        for s in range(40):
            rows2.append({"date": d, "code": f"{s:06d}", "signal": float(s * 2)})
    pl.DataFrame(rows2).with_columns(
        pl.col("date").str.to_date()).write_parquet(out2 / "panel.parquet")
    client = TestClient(create_app(results_dir=tmp_path))
    r = client.get("/factor/alpha_1")
    assert r.status_code == 200
    assert "correlation-chart" in r.text


def test_factor_detail_correlation_single_factor(tmp_path):
    """库内只有当前因子 → 相关区块降级不崩溃。"""
    _write_factor(tmp_path, "alpha_1")
    client = TestClient(create_app(results_dir=tmp_path))
    r = client.get("/factor/alpha_1")
    assert r.status_code == 200
    assert "correlation-chart" not in r.text


# ── R01-EVAL-I1/I5/I7/I9：target 接线 / 缺字段降级 / 图表数值断言 ──
def _chart_figure(html: str, chart_id: str) -> dict:
    import re
    m = re.search(rf'Plotly\.newPlot\("{chart_id}",\s*', html)
    assert m, f"页面未找到 {chart_id} 图表"
    fig, _ = json.JSONDecoder().raw_decode(html[m.end():])
    return fig


def _write_dual_target_factor(tmp_path, name, target, include_20d=True):
    """5d 与 20d 符号相反的周频面板 + evaluation.target 声明。"""
    out = tmp_path / name
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for w, d in enumerate(["2024-01-05", "2024-01-12", "2024-01-19"]):
        for s in range(30):
            row = {"date": d, "code": f"{s:06d}", "signal": float(s),
                   "forward_return_5d": 0.1 * s + w * 0.001}
            if include_20d:
                row["forward_return_20d"] = -0.1 * s + w * 0.001
            rows.append(row)
    pl.DataFrame(rows).write_parquet(out / "weekly.parquet")
    ic_mean = -1.0 if target == "forward_return_20d" else 1.0
    (out / "summary.json").write_text(json.dumps({
        "name": name, "category": "demo", "direction": 1,
        "universe_count": 30, "date_start": "2024-01-05", "date_end": "2024-01-19",
        "panel_rows": 90, "signal_null_ratio": 0.0, "spec_yaml": "name: demo",
        "evaluation": {"target": target, "ic": {"mean": ic_mean, "t_stat": 10.0},
                       "decile_returns": {"spread": {"ret": 0.01}, "groups": []},
                       "turnover": {"monthly": 0.1}, "coverage": {"pct_valid": 1.0}},
    }, ensure_ascii=False), encoding="utf-8")


def test_factor_detail_ic_curve_uses_spec_target(tmp_path):
    """R01-EVAL-I1：20d 因子详情页的 IC 曲线必须来自 forward_return_20d
    （旧实现固定 5d → 同页 summary 与曲线符号相反）。"""
    _write_dual_target_factor(tmp_path, "f20", "forward_return_20d")
    client = TestClient(create_app(results_dir=tmp_path))
    resp = client.get("/factor/f20")
    assert resp.status_code == 200
    y = _chart_figure(resp.text, "ic-chart")["data"][0]["y"]
    assert len(y) == 3
    assert all(v == pytest.approx(-1.0) for v in y)   # 20d 与 signal 完全负相关
    assert max(y) < 0                                 # 旧实现（5d）会给 +1.0


def test_factor_detail_5d_target_regression(tmp_path):
    """target=forward_return_5d → 曲线取 5d（回归锁，不是恒取 20d）。"""
    _write_dual_target_factor(tmp_path, "f5", "forward_return_5d")
    client = TestClient(create_app(results_dir=tmp_path))
    y = _chart_figure(client.get("/factor/f5").text, "ic-chart")["data"][0]["y"]
    assert all(v == pytest.approx(1.0) for v in y)


def test_factor_detail_target_column_missing_degrades(tmp_path):
    """target=20d 但 weekly 无该列 → 不画错曲线，IC 区域降级（200 + 占位提示）。"""
    _write_dual_target_factor(tmp_path, "f20x", "forward_return_20d", include_20d=False)
    client = TestClient(create_app(results_dir=tmp_path))
    resp = client.get("/factor/f20x")
    assert resp.status_code == 200
    assert "无周频数据" in resp.text
    assert "ic-chart" not in resp.text


def test_factor_detail_missing_top_level_scalars_degrade(tmp_path):
    """R01-EVAL-I5：top-level 标量（signal_null_ratio/universe_count/…）缺失 →
    降级显示 —，不得 None*100 → 500。"""
    out = tmp_path / "bare"
    out.mkdir(parents=True)
    (out / "summary.json").write_text(json.dumps({
        "name": "bare", "spec_yaml": "name: bare", "direction": 1,
        "evaluation": {"ic": {"mean": 0.05, "t_stat": 1.5}},
    }, ensure_ascii=False), encoding="utf-8")
    client = TestClient(create_app(results_dir=tmp_path))
    resp = client.get("/factor/bare")
    assert resp.status_code == 200
    assert "null=—" in resp.text            # 旧实现 None*100 → TypeError 500
    assert "bare" in resp.text


def test_factor_detail_nan_t_stat_not_labelled_significant(tmp_path):
    """R01-EVAL-I7（UI 侧）：t_stat 为 NaN 时不得落入"显著/不显著"误标 → —。"""
    _write_factor(tmp_path, "nan_t")
    p = tmp_path / "nan_t" / "summary.json"
    s = json.loads(p.read_text(encoding="utf-8"))
    s["evaluation"]["ic"]["t_stat"] = float("nan")
    p.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
    client = TestClient(create_app(results_dir=tmp_path))
    resp = client.get("/factor/nan_t")
    assert resp.status_code == 200
    assert "IC t_stat" in resp.text
    assert "<div class=\"hint\">—</div>" in resp.text
    assert "不显著" not in resp.text


def test_factor_detail_chart_values_numeric(tmp_path):
    """R01-EVAL-I9：web e2e 数值断言——IC 曲线与十分位柱值来自真实数据，
    不是只查页面字符串。"""
    _write_factor(tmp_path, "num_ok")
    resp = TestClient(create_app(results_dir=tmp_path)).get("/factor/num_ok")
    assert resp.status_code == 200
    ic_y = _chart_figure(resp.text, "ic-chart")["data"][0]["y"]
    assert ic_y == pytest.approx([1.0, 1.0])          # signal 与 fwd 完全单调
    decile_y = _chart_figure(resp.text, "decile-chart")["data"][0]["y"]
    assert decile_y == pytest.approx([0.03, 0.01])    # 与 summary.decile_returns.groups 一致
