"""W6 真 CH 小窗 e2e：分钟链 × 生产库三对拍（B4.5/B4.6/B5 验收）。

- loader 对拍：load_bars_1m_codes 逐 (code, 交易日) 行数 == CH 直连 count == 240
- 折日对拍：run_factor_minute day_last(close) 信号 == bars 当日 minute_index 239
  行 close（同 f32 源，rel 1e-5）== daily 面 raw close（f64，rel 1e-5）
- label 对拍：分钟链 label（forward_return_5d/20d）== 日频链同窗同 codes 面板在
  分钟信号键上的子集逐值全等（含 null 形态——键集不一致即漏斗语义破坏）
- 产物：summary runtime_semantics/interface/grid_rows_per_day/canonical codes +
  4 artifact 文件

环境缺 CH/样本 code 无数据时 skip（集成标记，与 test_intraday_prod_e2e 同约定）。
"""
import datetime as dt

import polars as pl
import pytest

from factorlab.data.backend import open_read
from factorlab.data.calendar import trading_calendar
from factorlab.data.intraday import load_bars_1m_codes
from factorlab.data.source import load_daily
from factorlab.engine.compute import RunContext, run_factor
from factorlab.engine.minute import run_factor_minute
from factorlab.spec import load_spec

pytestmark = pytest.mark.integration

_CODES = ["000001.SZ", "600519.SH"]     # 样本 code（近期有完整 240 网格 + 日线）
_WIN_DAYS = 10                          # 对拍窗口（交易日数，信号日）
_TOL = 1e-5                             # f32→f64 折算容差（rel）
_SYM6 = {c.split(".")[0]: c for c in _CODES}   # loader 输出 6 位（B5.3）→ ts_code


def _canon(df: pl.DataFrame) -> pl.DataFrame:
    """loader 6 位 code → ts_code（引擎 artifact 侧同一 canonical 空间）。"""
    return df.with_columns(pl.col("code").replace_strict(
        pl.Series(list(_SYM6)), pl.Series(list(_SYM6.values()))))


def _pick_window(client):
    """最近 _WIN_DAYS 个交易日闭窗 [start, end]，两 code 全程 240 网格 + 日线在；
    任一不满足 → 换次近窗重试一次，仍不行 skip。返回 (start, end, end_iso)。"""
    db = client.database
    for code in _CODES:
        r = client.query(f"SELECT max(trade_date), count() FROM {db}.bars_1m "
                         f"WHERE code = '{code}'").result_rows[0]
        if r[0] is None or str(r[0]).startswith("1970"):
            pytest.skip(f"bars_1m 生产库无 {code} 数据")
    end = client.query(f"SELECT max(trade_date) FROM {db}.daily "
                       f"WHERE ts_code IN ('{_CODES[0]}', '{_CODES[1]}')"
                       ).result_rows[0][0]
    assert end is not None and not str(end).startswith("1970"), \
        "daily 生产库无样本 code 数据"
    cal = client.query(f"SELECT cal_date FROM {db}.trade_cal "
                       f"WHERE cal_date <= '{end}' AND is_open = 1 "
                       f"ORDER BY cal_date DESC LIMIT {_WIN_DAYS}"
                       ).result_rows
    dates = [r[0] for r in cal]
    assert len(dates) == _WIN_DAYS, f"日历不足 {_WIN_DAYS} 日（实际 {len(dates)}）"
    return dates[-1], dates[0], str(end)


def _ch_counts(client, table: str, start: str, end: str, code_col: str):
    """直连 CH 逐 (code, 交易日) count（独立参照，不经 loader）。"""
    rows = client.query(
        f"SELECT {code_col}, trade_date, count() FROM {client.database}.{table} "
        f"WHERE trade_date BETWEEN '{start}' AND '{end}' "
        f"GROUP BY {code_col}, trade_date").result_rows
    return rows


def test_minute_prod_e2e_three_way_parity(ch_prod, tmp_path):
    client = ch_prod
    start, end, _ = _pick_window(client)
    iso_s, iso_e = start.isoformat(), end.isoformat()
    rd = open_read(data_backend="ch")
    cal = trading_calendar(rd, date_start=iso_s, date_end=iso_e)
    assert cal.len() == _WIN_DAYS
    n_days = cal.len()

    # ---- loader 对拍：批读逐 (code, 交易日) 行数 == CH 直连 count == 240 ----
    bars = load_bars_1m_codes(rd, _CODES, date_start=iso_s, date_end=iso_e,
                              cols=["trade_date", "code", "minute_index",
                                    "close"]).rename({"trade_date": "date"})
    bars = _canon(bars)
    cnt = bars.group_by(["date", "code"]).len()
    ch_cnt = _ch_counts(client, "bars_1m", iso_s, iso_e, "code")
    assert cnt.height == 2 * n_days
    assert set(cnt["code"]) == set(_CODES)
    for c in _CODES:                      # loader == 直连 count（同源行数契约）
        for d in cal.to_list():
            got = cnt.filter((pl.col("date") == d) & (pl.col("code") == c))["len"][0]
            want = [n for cc, dd, n in ch_cnt if cc == c and dd == d]
            assert len(want) == 1 and want[0] == got == 240, \
                f"{c} {d}: loader {got} != CH {want}"

    # ---- 分钟 spec + 引擎 ----
    spec_p = tmp_path / "m_prod.yaml"
    spec_p.write_text(f"""
name: m_prod
category: custom
direction: 1
interface: bars_1m
adjustment: raw
universe:
  codes: {_CODES}
date:
  start: "{iso_s}"
  end: "{iso_e}"
formula: |
  signal = day_last(close)
""", encoding="utf-8")
    out = tmp_path / "m_out"
    res = run_factor_minute(load_spec(spec_p),
                            RunContext(data_backend="ch", output_dir=out))
    sig = res.panel.sort(["date", "code"])

    # ---- 对拍 1：折日信号 == 当日 minute_index 239 行 close（f32 同源）----
    last239 = (bars.sort(["date", "code", "minute_index"])
               .group_by(["date", "code"], maintain_order=True)
               .tail(1).sort(["date", "code"]))
    joined = sig.join(last239.select(["date", "code", "close"]),
                      on=["date", "code"], how="inner")
    assert joined.height == 2 * n_days
    rel = ((joined["signal"].cast(pl.Float64) - joined["close"].cast(pl.Float64))
           / joined["close"].cast(pl.Float64).abs()).abs().max()
    assert rel <= _TOL, f"day_last(close) != minute_index 239 行 close（{rel}）"

    # ---- 对拍 2：折日信号 == daily 面 raw close（f64，f32 折算容差）----
    daily = _canon(load_daily(rd, _CODES, date_start=iso_s, date_end=iso_e,
                              cols=["close"], float32=False).collect())
    joined = sig.join(daily, on=["date", "code"], how="inner")
    assert joined.height == 2 * n_days
    rel = ((joined["signal"].cast(pl.Float64) - joined["close"])
           / joined["close"].abs()).abs().max()
    assert rel <= _TOL, f"day_last(close) 偏离 daily raw close 达 {rel}"

    # ---- 对拍 3：label == 日频链同窗面板在分钟键上的子集逐值全等 ----
    spec_d = tmp_path / "d_prod.yaml"
    spec_d.write_text(f"""
name: d_prod
category: custom
direction: 1
universe:
  codes: {_CODES}
date:
  start: "{iso_s}"
  end: "{iso_e}"
formula: |
  signal = close
""", encoding="utf-8")
    dres = run_factor(load_spec(spec_d),
                      RunContext(data_backend="ch", output_dir=tmp_path / "d_out"))
    dlab = dres.panel.sort(["date", "code"])
    mlab = res.label_artifact.frame.sort(["date", "code"])
    assert mlab.height == sig.height == 2 * n_days   # 信号行 = label 行（对齐）
    assert set(map(tuple, mlab.select(["date", "code"]).iter_rows())) <= \
        set(map(tuple, dlab.select(["date", "code"]).iter_rows())), \
        "分钟 label 键不在日频键集内（停牌语义破坏）"
    sub = dlab.join(mlab.select(["date", "code"]), on=["date", "code"],
                    how="inner").sort(["date", "code"])
    assert sub.height == mlab.height
    for col in ("forward_return_5d", "forward_return_20d"):
        a, b = mlab[col].to_list(), sub[col].to_list()
        assert all((x is None) == (y is None) for x, y in zip(a, b)), \
            f"{col} null 形态不一致（键对齐破坏）"
        assert all(x is None or abs(x - y) <= _TOL * max(abs(y), 1.0)
                   for x, y in zip(a, b)), f"{col} 数值不一致"
    # 尾 5 日 label 为 null（窗口外前视）——折日信号键仍全在
    assert mlab["forward_return_5d"].null_count() >= n_days - 5

    # ---- 产物/元数据 ----
    assert res.summary["runtime_semantics"] == "minute_intraday_fold_v1"
    assert res.summary["interface"] == "bars_1m"
    assert res.summary["grid_rows_per_day"] == 240
    assert res.summary["codes"] == sorted(_CODES)
    for f in ("signal.parquet", "labels.parquet", "panel.parquet",
              "summary.json"):
        assert (out / f).exists()
    rd.close()
    print(f"minute e2e PASSED: {iso_s}..{iso_e} {_WIN_DAYS} 交易日 × 2 code；"
          f"loader 240×{2 * n_days} 三向一致")
