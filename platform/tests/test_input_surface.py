"""M1（G3 骨架）：输入面（列供给 + 名字类墙）双腿参数化。

覆盖三件事（设计：knowledge/design/platform/specs/2026-09-06-factorlab-dsl-shape-design.md，
M1 验收行——未知列/相似名/保留名前缀三路测试 + 无白名单证明）：

1. 报错助手：未知列的报错从 polars 深层下探到引擎装配层，含**当前数据面可用列**
   （schema 实探，非静态清单）+ difflib 最相似候选（≤2）+ 原始列映射提示。
2. 无白名单证明：未被目录收录的真实 daily 列（vwap）可直接加载/计算——白名单
   时代会报"未知列名"，此测试锁定它可跑且数值真实流入。
3. 名字类墙：公式读取内部保留名（in_universe / __factorlab_*）与绑定内部名
   （无 universe_mask 时也不放行）→ fail fast。

env：duckdb|ch 双腿；数据描述复用 test_source._BASE_TABLES / test_run_factor._tables
（vwap 列由各测试局部扩展——不动共享 helper）。
"""

import json

import polars as pl
import pytest

from factorlab.adapters.read.source import load_daily, load_daily_fill_state
from factorlab.app.run import run_factor
from factorlab.app.context import RunContext
from factorlab.core.engine.compute import compute_formula
from test_run_factor import _ctx, _spec, _tables
from test_source import _BASE_TABLES

_VWAP = ("vwap", "f64")


def _with_vwap(tables: dict) -> dict:
    """daily 表追加 vwap 列（行值恒 100.0，逐行对齐原表结构）。"""
    out = dict(tables)
    cols, rows = tables["daily"]
    out["daily"] = (cols + [_VWAP], [r + (100.0,) for r in rows])
    return out


def _subset_daily_face(tables: dict) -> dict:
    """daily 只保留 subset（含 vwap，无 amount/open）——真实面由 schema 决定，
    不是静态清单。行数/其他表沿用原库。"""
    out = dict(tables)
    cols, rows = tables["daily"]
    keep = {"ts_code", "trade_date", "close", "vol"}
    idx = [i for i, (name, _kind) in enumerate(cols) if name in keep]
    new_cols = [cols[i] for i in idx] + [_VWAP]
    new_rows = [tuple(r[i] for i in idx) + (100.0,) for r in rows]
    out["daily"] = (new_cols, new_rows)
    return out


# ================================================================
# 1. 报错助手：未知列 → 装配层文案（当前数据面可用列 + difflib 最相似 + 映射提示）
# ================================================================

def test_typo_col_error_suggests_closest(env):
    """`clos`（close 的笔误）→ 报错含未知名、数据面可用列与 'close' 建议（difflib）。"""
    env.seed(_BASE_TABLES)
    with pytest.raises(ValueError) as exc:
        load_daily(env.rd, ["000001"], cols=["clos"]).collect()
    msg = str(exc.value)
    assert "未知列名" in msg and "clos" in msg
    assert "可用列" in msg
    # 建议以引用形式出现（区别于可用列清单里的裸 close）
    assert "'close'" in msg
    assert "最接近的列" in msg


def test_typo_col_error_in_fill_state_path(env):
    """fill_state（跨 chunk 边界 seed）同一报错助手——不退回 polars 深层报错。"""
    env.seed(_BASE_TABLES)
    with pytest.raises(ValueError) as exc:
        load_daily_fill_state(env.rd, ["000001"], before="2024-01-03",
                              cols=["clos"]).collect()
    msg = str(exc.value)
    assert "未知列名" in msg and "clos" in msg and "最接近的列" in msg


def test_unknown_col_message_reflects_current_schema_face(env):
    """可用列清单 = 当前数据面实探 schema，不是静态目录：
    subset 面（无 amount/open、有 vwap）→ 报错含 vwap/volume，不含 amount/open。"""
    env.seed(_subset_daily_face(_BASE_TABLES))
    with pytest.raises(ValueError) as exc:
        load_daily(env.rd, ["000001"], cols=["zzz"]).collect()
    msg = str(exc.value)
    assert "vwap" in msg and "volume" in msg      # 面上真实存在的引擎列
    assert "amount" not in msg and "open" not in msg  # 面上不存在的不提示


def test_raw_hidden_name_gets_mapping_hint(env):
    """vol/ts_code/trade_date 是原始列名（非引擎名）——报错给映射提示不是静默。"""
    env.seed(_BASE_TABLES)
    with pytest.raises(ValueError) as exc:
        load_daily(env.rd, ["000001"], cols=["vol"]).collect()
    msg = str(exc.value)
    assert "未知列名" in msg and "volume" in msg and "映射" in msg


# ================================================================
# 2. 无白名单证明：目录外的真实 daily 列可加载/计算/填值
# ================================================================

def test_unlisted_real_daily_column_loads(env):
    """vwap 不在任何目录/白名单——schema 放行：值原样流入（float32 无失真）。"""
    env.seed(_with_vwap(_BASE_TABLES))
    df = load_daily(env.rd, ["000001"], cols=["vwap"]).collect()
    assert df.columns == ["date", "code", "vwap", "close"]  # close 恒加载语义不变
    assert df["vwap"].to_list() == [100.0, 100.0]
    # 混合：目录列 + 开放列同一趟
    df2 = load_daily(env.rd, ["000001"], cols=["close", "vwap"]).collect()
    assert set(df2.columns) == {"date", "code", "close", "vwap"}


def test_unlisted_real_daily_column_flows_through_run_factor(env, tmp_path):
    """端到端：公式引用 vwap → 真实数值参与计算（signal 非全 null）→
    开放列不泄漏进 artifact/panel（只出 date/code/signal + 对齐列）。"""
    env.seed(_with_vwap(_tables()))
    spec = _spec(tmp_path)
    spec.formula = "signal = close / vwap"
    result = run_factor(spec, _ctx(env, tmp_path / "out"))
    assert result.signal_artifact.frame.height > 0
    assert result.signal_artifact.frame["signal"].null_count() == 0  # vwap 参与了计算
    assert result.panel["signal"].null_count() == 0
    assert "vwap" not in result.panel.columns
    summary = json.loads((tmp_path / "out" / "summary.json").read_text(encoding="utf-8"))
    assert summary["signal_rows"] > 0


def test_unlisted_real_daily_column_in_fill_state(env):
    """fill_state 同 schema 放行：vwap 也按 code 返回窗口前 latest non-null。"""
    env.seed(_with_vwap(_BASE_TABLES))
    fs = load_daily_fill_state(env.rd, ["000001", "600519"], before="2024-01-03",
                               cols=["close", "vwap"])
    assert fs["code"].to_list() == ["000001", "600519"]
    assert fs["vwap"].to_list() == [100.0, 100.0]   # 两 code 窗口前最后一行都是 vwap 行


# ================================================================
# 3. 名字类墙：内部保留名的读/绑定（无 mask 与 run 路径同样 fail fast）
# ================================================================

def test_run_factor_rejects_reading_in_universe(env, tmp_path):
    """in_universe 是运行时注入的 PIT 标记列——模板读取 → 内部保留名错误（装配层）。"""
    env.seed(_tables())
    spec = _spec(tmp_path)
    spec.formula = "signal = close / in_universe"
    with pytest.raises(ValueError, match="内部保留名"):
        run_factor(spec, _ctx(env, tmp_path / "out"))


def test_run_factor_rejects_reading_internal_prefix_column(env, tmp_path):
    """__factorlab_universe_active 在 masked 面板中存在——读取必须被墙挡下
    （否则公式直接拿到 mask 列值，绕过 CS 语义）。"""
    env.seed(_tables())
    spec = _spec(tmp_path)
    spec.formula = "signal = __factorlab_universe_active"
    with pytest.raises(ValueError, match="内部保留名"):
        run_factor(spec, _ctx(env, tmp_path / "out"))


def test_compute_formula_rejects_internal_read_without_mask():
    """compute_formula 直调（universe_mask=None）同样挡读——不只 masked 路径。"""
    df = pl.DataFrame({
        "date": pl.Series([__import__("datetime").date(2024, 1, 2)] * 2, dtype=pl.Date),
        "code": ["A", "B"],
        "close": [1.0, 2.0],
    })
    with pytest.raises(ValueError, match="内部保留名"):
        compute_formula(df, "signal = ts_mean(close, 2) + __factorlab_universe_active")


def test_internal_binding_rejected_even_without_mask():
    """M1 收紧：__factorlab_* 绑定此前只在 masked 路径校验——
    无 mask 直调也必须拒（名字类墙与 mask 无关）。"""
    df = pl.DataFrame({
        "date": pl.Series([__import__("datetime").date(2024, 1, 2)] * 2, dtype=pl.Date),
        "code": ["A", "B"],
        "close": [1.0, 2.0],
    })
    with pytest.raises(ValueError, match="reserved internal name"):
        compute_formula(df, "__factorlab_tmp = close\nsignal = __factorlab_tmp")


def test_in_universe_binding_rejected():
    """in_universe 不能作为用户绑定名（引擎 PIT 标记列的命名空间）。"""
    from factorlab.core.ops.universe_masking import validate_reserved_bindings
    with pytest.raises(ValueError, match="reserved internal name"):
        validate_reserved_bindings("in_universe = close\nsignal = in_universe")
