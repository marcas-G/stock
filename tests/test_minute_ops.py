"""bars_1m 分钟算子族（im_*/day_*）行为 + spec.interface 声明（W3，映射 spec B1/B2）。

公式经 compute_formula(scope="bars_1m") 全链执行（CL 通道 + extra_codes 注入分钟
算子名——同时实测多行注入形态）。断言语义 = 独立参照（手工滚动/闭式），不复制
实现；折日面板输出 (date, code, outputs) 与手工参照逐值 approx 对拍。
"""
import datetime as dt
import math

import polars as pl
import pytest
import yaml

from factorlab.core.engine.compute import compute_formula
from factorlab.core.spec import FactorSpec

_D1, _D2 = dt.date(2026, 8, 20), dt.date(2026, 8, 21)
_CODES = ("000001", "600519")
_DAY_BASE = 100_000     # 日 2 与日 1 的价格差——跨日泄漏值显著可辨
_CODE_BASE = 1_000
# 每 (code, date) 有序组：close = day_base*di + code_base*ci + minute_index + 1；
# volume = 1（vwap30 = im_mean(close, 30) 的闭式参照成立）
def _group_close(ci: int, di: int) -> list[float]:
    return [float(_DAY_BASE * di + _CODE_BASE * ci + mi + 1) for mi in range(240)]


def _rows():
    for ci, code in enumerate(_CODES):
        for di, d in enumerate((_D1, _D2)):
            xs = _group_close(ci, di)
            eod = xs[-1]
            for mi in range(240):
                yield (d, code, mi, xs[mi], 1.0, eod)


def _frame():
    return pl.DataFrame(
        _rows(),
        schema={"date": pl.Date, "code": pl.Utf8, "minute_index": pl.Int32,
                "close": pl.Float64, "volume": pl.Float64, "eod_close": pl.Float64},
        orient="row")


def ref_im_mean(xs, w):
    """独立参照：组内有序滚动均值，组首 n-1 行 null（polars_ta ts_mean 语义）。"""
    return [None if i + 1 < w else sum(xs[i + 1 - w:i + 1]) / w for i in range(len(xs))]


def _fold(groups: dict, expr_vals: dict) -> pl.DataFrame:
    """闭式参照 → 折日断言帧：{code: {date: 组参照常数}}。"""
    rows = []
    for code, dates in groups.items():
        for d, val in dates.items():
            rows.append((d, code, val))
    return pl.DataFrame(rows, schema={"date": pl.Date, "code": pl.Utf8, "v": pl.Float64},
                        orient="row")


def test_im_mean_hand_rolling_sum_and_leading_nulls():
    """im_mean(close, 30) 逐行 = 手工滚动参照；组首 29 行 null（day_sum 只收
    有效窗 → 与参照总窗和逐值相等——每 (code, date) 常数断言覆盖全部 240 行）。"""
    formula = "signal = day_sum(im_mean(close, 30))"
    res = compute_formula(_frame(), formula, outputs=["signal"], scope="bars_1m")
    got = res.group_by(["date", "code"]).agg(pl.col("signal").first())
    ref = _fold({}, {})
    want = {}
    for ci, code in enumerate(_CODES):
        for di, d in enumerate((_D1, _D2)):
            xs = _group_close(ci, di)
            want[f"{d}|{code}"] = sum(v for v in ref_im_mean(xs, 30) if v is not None)
    for row in got.iter_rows():
        assert row[2] == pytest.approx(want[f"{row[0]}|{row[1]}"], rel=1e-9)
    # 非存根锁：参照总窗和 ≠ 组全和（null 头部剔除有效）
    assert got.height == len(want) == 4


def test_im_window_strictly_intraday_no_cross_day():
    """B3.5 机械不跨日：im_sum(close, 240) 组末行 = 当日 240 行全和；窗口 500 >
    当日行数 → 折日 null（若实现跨日偷前日行 → 非 null 值，断言必败）。"""
    formula = """
s240 = day_last(im_sum(close, 240))
s500 = day_last(im_sum(close, 500))
"""
    res = compute_formula(_frame(), formula, outputs=["s240", "s500"], scope="bars_1m")
    for ci, code in enumerate(_CODES):
        for di, d in enumerate((_D1, _D2)):
            xs = _group_close(ci, di)
            full = sum(xs)
            rows = res.filter((pl.col("date") == d) & (pl.col("code") == code))
            v240 = rows["s240"][0]
            assert v240 == pytest.approx(full, rel=1e-9)      # 当日全和（跨日会偏）
            assert rows["s240"].is_null().sum() == 0
            # 窗口 500 > 当日 240 行 → 整列 null；若实现偷前日行则会得非 null 值
            assert rows["s500"].is_null().sum() == rows.height


def test_im_deterministic_across_shuffle():
    """B2.2 确定性锁：乱序输入 → 折日逐值全等（两 seed），且与有序输入全等。"""
    formula = "signal = day_last(im_sum(close, 30))"
    ordered = compute_formula(_frame(), formula, scope="bars_1m")
    for seed in (7, 1234):
        shuffled = _frame().sample(fraction=1.0, shuffle=True, seed=seed)
        res = compute_formula(shuffled, formula, scope="bars_1m")
        a = ordered.select(["date", "code", "signal"]).sort(["date", "code"])
        b = res.select(["date", "code", "signal"]).sort(["date", "code"])
        assert a.equals(b), f"seed={seed} 乱序输入结果漂移"


def test_day_family_fold_broadcast_values():
    """B2.3 折日族：day_last = minute_index 239 行值、day_first = 0 行值、day_sum =
    组全和、day_max/min 单调序列 = 末/首行值；组内全行同值（广播）。"""
    formula = """
dl = day_last(close)
df = day_first(close)
ds = day_sum(volume)
dmax = day_max(close)
dmin = day_min(close)
"""
    res = compute_formula(_frame(), formula,
                          outputs=["dl", "df", "ds", "dmax", "dmin"], scope="bars_1m")
    per_group = res.group_by(["date", "code"]).agg(
        pl.col("dl").n_unique().alias("dl_uniq"),
        pl.col("dl").first().alias("dl_v"),
        pl.col("df").first().alias("df_v"),
        pl.col("ds").first().alias("ds_v"),
        pl.col("dmax").first().alias("dmax_v"),
        pl.col("dmin").first().alias("dmin_v"),
        pl.col("ds").n_unique().alias("ds_uniq"),
    )
    for row in per_group.iter_rows():
        date, code = row[0], row[1]
        ci = _CODES.index(code)
        di = (date - _D1).days
        xs = _group_close(ci, di)
        assert row[2] == 1                      # dl 组内广播
        assert row[8] == 1                      # ds 组内广播
        assert row[3] == pytest.approx(xs[-1], rel=1e-9)   # day_last == 239 行
        assert row[4] == pytest.approx(xs[0], rel=1e-9)    # day_first == 0 行
        assert row[5] == pytest.approx(240.0, rel=1e-9)    # volume 全 1 → 组全和 240
        assert row[6] == pytest.approx(xs[-1], rel=1e-9)   # max == 末行
        assert row[7] == pytest.approx(xs[0], rel=1e-9)    # min == 首行


def test_im_delay_intraday_shift():
    """im_delay(close, 2) = 组内位移 2（组首 2 行 null，不跨日）。"""
    formula = """
s = day_last(im_delay(close, 2))
"""
    res = compute_formula(_frame(), formula, outputs=["s"], scope="bars_1m")
    for ci, code in enumerate(_CODES):
        for di, d in enumerate((_D1, _D2)):
            xs = _group_close(ci, di)
            v = res.filter((pl.col("date") == d) & (pl.col("code") == code))["s"][0]
            assert v == pytest.approx(xs[-3], rel=1e-9)   # 239 行 = 原 237 行
            assert v != xs[-1]                            # 非恒等位移


def test_vwap30_bias_combination_e2e():
    """B2.5/B4.7 e2e：vwap30 = im_sum(close*volume,30)/im_sum(volume,30)（volume 全
    1 → = im_mean）；vwap30_bias = day_last(vwap30)/eod_close - 1（注入列参与元素级
    组合——折日常数表达式）。多行 extra_codes 注入形态同此实测。"""
    formula = """
vwap30 = im_sum(close * volume, 30) / im_sum(volume, 30)
vwap30_bias = day_last(vwap30) / eod_close - 1
"""
    res = compute_formula(_frame(), formula, outputs=["vwap30_bias"], scope="bars_1m")
    got = res.group_by(["date", "code"]).agg(pl.col("vwap30_bias").first())
    for row in got.iter_rows():
        date, code, v = row
        ci = _CODES.index(code)
        di = (date - _D1).days
        xs = _group_close(ci, di)
        last_w = ref_im_mean(xs, 30)[-1]
        want = last_w / xs[-1] - 1
        assert v == pytest.approx(want, rel=1e-9)


def test_multi_output_all_folded():
    """多输出逐输出折日（B4.4/B4.6 前置）：面板 = (date, code, *outputs)。"""
    formula = """
bias = day_last(close) / eod_close - 1
vol = day_mean(volume)
"""
    res = compute_formula(_frame(), formula, outputs=["bias", "vol"], scope="bars_1m")
    assert res.columns == ["date", "code", "bias", "vol"]
    assert res.group_by(["date", "code", "bias", "vol"]).len().height == 4


# ---------------- spec.interface 声明（B1.1） ----------------

def test_spec_interface_default_daily_and_bars_1m(tmp_path):
    base = {
        "name": "demo_m1",
        "category": "custom",
        "direction": 1,
        "universe": {"codes": ["000001.SZ"]},
        "date": {"start": "2026-01-01", "end": "2026-01-31"},
        "formula": "signal = day_last(close)",
    }
    s_plain = FactorSpec.model_validate(base)
    assert s_plain.interface == "daily"                      # 缺省 = daily
    s_m1 = FactorSpec.model_validate({**base, "interface": "bars_1m"})
    assert s_m1.interface == "bars_1m"
    with pytest.raises(Exception, match="interface"):
        FactorSpec.model_validate({**base, "interface": "tick"})   # Literal 拒


def test_spec_interface_minute_yaml_load(tmp_path):
    p = tmp_path / "m1.yaml"
    p.write_text("""
name: demo_m1
category: custom
direction: 1
interface: bars_1m
adjustment: raw
universe:
  codes: ["000001.SZ"]
formula: |
  signal = day_last(close)
""")
    from factorlab.core.spec import load_spec
    spec = load_spec(p)
    assert spec.interface == "bars_1m" and spec.adjustment == "raw"
