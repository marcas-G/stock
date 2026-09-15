"""M5 端到端集成：真实 results 目录的 Web 可视化冒烟。

真实 results 目录默认取本工作树的 `results/`（相对本文件定位；含
m4b_smoke/acceptance/demo_vol_skew 等）——与 conftest REAL_DB 同模式：
缺失即 skip（FACTORLAB_RESULTS_DIR 可覆盖，指向别处的 results）。
"""
import json
import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from factorlab.adapters import results_fs
from factorlab.core.eval.ic_series import weekly_ic
from factorlab.surfaces.web.app import create_app

REAL_RESULTS = Path(os.environ.get(
    "FACTORLAB_RESULTS_DIR",
    str(Path(__file__).resolve().parents[1] / "results"),
))

pytestmark = pytest.mark.integration


def _chart_figure(html: str, chart_id: str) -> dict:
    m = re.search(rf'Plotly\.newPlot\("{chart_id}",\s*', html)
    assert m, f"页面未找到 {chart_id} 图表"
    fig, _ = json.JSONDecoder().raw_decode(html[m.end():])
    return fig


# 历史档冒烟因子（本文件断言的固定名字）——目录存在 ≠ 是这批产物：
# 用户自己的挖掘轮次也会写 results/，仅判"目录存在"会把新结果误当历史档而假失败。
FIXTURE_FACTORS = ("m4b_smoke", "demo_vol_skew", "momentum_20d")


def missing_fixture_factors(results_dir: Path) -> list[str]:
    """results 目录里缺失的历史档因子名（空列表 = 齐备，可跑真断言）。"""
    return [name for name in FIXTURE_FACTORS
            if not (results_dir / name / "summary.json").is_file()]


@pytest.fixture
def real_results_dir():
    if not REAL_RESULTS.is_dir():
        pytest.skip(f"真实 results 目录不存在: {REAL_RESULTS}")
    missing = missing_fixture_factors(REAL_RESULTS)
    if missing:
        pytest.skip(f"真实 results 缺历史档因子 {missing}（目录存在但非该批产物；"
                    f"设 FACTORLAB_RESULTS_DIR 指向含这些因子的目录）")
    return REAL_RESULTS


def test_web_index_lists_real_factors(real_results_dir):
    # 列表页：真实 3 个因子全部展示（含 IC 摘要列）
    resp = TestClient(create_app(real_results_dir)).get("/")
    assert resp.status_code == 200
    for name in ("m4b_smoke", "demo_vol_skew", "momentum_20d"):  # 目录名与 summary.name 一致的可稳定断言
        assert name in resp.text


def test_web_factor_detail_real_charts(real_results_dir):
    # 详情页：真实数据渲染图表（IC 曲线/十分位/分层净值）与指标
    resp = TestClient(create_app(real_results_dir)).get("/factor/m4b_smoke")
    assert resp.status_code == 200
    assert "m4b_smoke" in resp.text
    assert "0.0786" in resp.text  # 真实 IC mean 格式化 %.4f（0.07857 → 0.0786）
    assert "Plotly" in resp.text  # 图表脚本与 figure JSON 内嵌
    assert "long_short" in resp.text  # 分层回测净值序列数据
    # R01-EVAL-I1/I9：曲线数值 == 真实 weekly 面板按 summary.target 重算的 IC 序列
    # （字符串断言升级为逐点数值断言；target 接错/取错列必败）
    summary = json.loads(
        (real_results_dir / "m4b_smoke" / "summary.json").read_text(encoding="utf-8"))
    target = summary.get("evaluation", {}).get("target", "forward_return_5d")
    expected = weekly_ic(results_fs.read_weekly(real_results_dir, "m4b_smoke"),
                         target=target)["ic"].to_list()
    y = _chart_figure(resp.text, "ic-chart")["data"][0]["y"]
    assert len(y) == len(expected)
    for got, want in zip(y, expected):
        if want is None:
            assert got is None
        else:
            assert got == pytest.approx(want, abs=1e-9)


def test_web_factor_detail_degraded_real(real_results_dir):
    # 真实数据边界：无 evaluation 的旧因子详情降级展示（不崩溃）
    resp = TestClient(create_app(real_results_dir)).get("/factor/demo_vol_skew")
    assert resp.status_code == 200
    assert "demo_vol_skew" in resp.text


def test_web_factor_missing_real_404(real_results_dir):
    # 真实数据错误路径：不存在的因子 → 404
    resp = TestClient(create_app(real_results_dir)).get("/factor/ghost")
    assert resp.status_code == 404


def test_missing_fixture_factors_detects_partial_results(tmp_path):
    """守卫：仅当历史档因子齐备才跑真断言——用户自有 results 不得假失败。"""
    (tmp_path / "my_own_factor").mkdir()
    (tmp_path / "my_own_factor" / "summary.json").write_text("{}", encoding="utf-8")
    assert missing_fixture_factors(tmp_path) == list(FIXTURE_FACTORS)

    for name in FIXTURE_FACTORS:
        (tmp_path / name).mkdir()
        (tmp_path / name / "summary.json").write_text("{}", encoding="utf-8")
    assert missing_fixture_factors(tmp_path) == []
