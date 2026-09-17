"""R08-DATA-I2：退市股 adj_factor 补灌工具测试（TDD）。

背景（R08 报告 §3-②）：131 只退市股 CH `adj_factor` 全 NULL → 全历史 signal/label
null（退市前整段历史不进 IC/分层）。根因：`data/raw/daily/退市股/*.xlsx` 为 8 列
精简格式（date/OHLCV），无复权列；全量 zip 不含退市代码。

修复：`delisted_adj_backfill.py` 从公开行情源（腾讯复权 K 线，hfq）取退市股全历史，
`adj = hfq/raw`（与"通达信后复权价/收盘"同语义，已用重叠代码对拍验证），按需用
现存 vendor adj 校准比例常数 → 写 sidecar parquet → `ingest_daily` 灌入时 coalesce
（只填 NULL，绝不覆盖 vendor 值）→ `reconcile` 加不变量。

断言来源：R08-DATA-I2（退市前历史恢复进评估）+ 数据纪律（不伪造：raw 逐日对拍、
比例校准不一致必须 loud fail）。
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("clickhouse_connect", reason="ch_ingest 属 T1（平台 venv）")

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import polars as pl  # noqa: E402

import delisted_adj_backfill as DAB  # noqa: E402
import ingest_daily  # noqa: E402

D1 = datetime.date(2024, 1, 2)
D2 = datetime.date(2024, 1, 3)
D3 = datetime.date(2024, 1, 4)
D4 = datetime.date(2024, 1, 5)


def _fact(rows: list[tuple]) -> pl.DataFrame:
    """rows: (code, date, close, adj_factor|null)。"""
    return pl.DataFrame(
        [{"code": c, "trade_date": d, "close": close, "adj_factor": a}
         for c, d, close, a in rows],
        schema={"code": pl.String, "trade_date": pl.Date,
                "close": pl.Float64, "adj_factor": pl.Float64})


# ── 代码符号 / 窗口分页 ───────────────────────────────────────────────

def test_sym_of_markets():
    assert DAB.sym_of("300379.SZ") == "sz300379"
    assert DAB.sym_of("600811.SH") == "sh600811"
    assert DAB.sym_of("920305.BJ") == "bj920305"
    with pytest.raises(ValueError):
        DAB.sym_of("000001.XX")


def test_windows_contiguous_cover_no_overlap():
    """窗口必须无缝连续、无重叠、覆盖到 end（腾讯单次返回有行数上限）。"""
    ws = DAB.windows(datetime.date(2019, 1, 10), datetime.date(2025, 6, 1),
                     years=2)
    assert ws[0][0] == datetime.date(2019, 1, 10)
    assert ws[-1][1] == datetime.date(2025, 6, 1)
    for (s1, e1), (s2, e2) in zip(ws, ws[1:]):
        assert s2 == e1 + datetime.timedelta(days=1), "窗口不连续/重叠"
        assert s1 <= e1 and s2 <= e2


def test_parse_kline_prefers_requested_fq_and_parses_rows():
    node = {
        "hfqday": [["2024-01-02", "10.0", "11.0", "11.5", "9.8", "1000"],
                   ["2024-01-03", "11.0", "12.0", "12.5", "10.5", "900"]],
        "day": [["2024-01-02", "5.0", "5.5", "5.6", "4.9", "1000"]],
    }
    rows = DAB.parse_kline(node, "hfq")
    assert rows == [(D1, 11.0), (D2, 12.0)]      # close = 第 3 个字段
    assert DAB.parse_kline(node, "") == [(D1, 5.5)]
    assert DAB.parse_kline({}, "hfq") == []


def test_fetch_code_falls_back_between_hosts(monkeypatch):
    """主域名被限流（501）→ 自动切备用域名/端点，不中断（腾讯双端点实测）。"""
    calls = []

    class _Resp:
        def __init__(self, code, payload=None):
            self.status_code = code
            self._payload = payload or {}

        def json(self):
            return self._payload

    class _Sess:
        def get(self, url, params=None, timeout=None):
            calls.append(url)
            if len(calls) == 1:
                return _Resp(501)
            return _Resp(200, {"data": {"sz000001": {
                "day": [["2024-01-02", "9.39", "9.21", "9.42", "9.21", "1"]]}}})

    assert any("newfqkline" in u for u in DAB._ENDPOINTS), \
        "端点表必须含 newfqkline 备用路径"
    rows = DAB.fetch_kline(_Sess(), "sz000001", D1, D2, "", tries=2, sleep=0)
    assert rows == [(datetime.date(2024, 1, 2), 9.21)]
    assert len(calls) >= 2 and calls[0] != calls[1], "必须切换端点重试"


# ── 派生：raw 对拍 + 比例 + 校准 ─────────────────────────────────────

def test_derive_adj_factor_ratio_and_exact_parity():
    fact = _fact([("X.SZ", D1, 10.0, None), ("X.SZ", D2, 11.0, None)])
    out, rep = DAB.derive_adj_factor(
        fact, [(D1, 10.0), (D2, 11.0)], [(D1, 20.0), (D2, 22.0)])
    assert out["adj_factor"].to_list() == [2.0, 2.0]
    assert rep["raw_max_abs_diff"] == 0.0
    assert rep["n_rows"] == 2 and rep["calibration"] is None


def test_derive_adj_factor_raw_mismatch_fails_loud():
    """raw 价格不一致 = 混源风险 → 必须抛（不静默填）。"""
    fact = _fact([("X.SZ", D1, 10.0, None), ("X.SZ", D2, 11.0, None)])
    with pytest.raises(ValueError, match="raw"):
        DAB.derive_adj_factor(fact, [(D1, 10.01), (D2, 11.0)],
                              [(D1, 20.0), (D2, 22.0)])


def test_derive_adj_factor_restricts_to_fact_dates():
    """腾讯多出的日期（fact 无行）不得进 sidecar（避免孤儿行）。"""
    fact = _fact([("X.SZ", D2, 11.0, None)])
    out, _ = DAB.derive_adj_factor(fact, [(D1, 10.0), (D2, 11.0)],
                                   [(D1, 20.0), (D2, 22.0)])
    assert out.height == 1 and out["trade_date"].to_list() == [D2]


def test_derive_adj_factor_calibrates_to_existing_vendor():
    """部分有 vendor adj 的代码：按 vendor 段末端锚定比例常数，填洞保持同一尺度。"""
    fact = _fact([("X.SZ", D1, 10.0, 5.0), ("X.SZ", D2, 11.0, 5.0),
                  ("X.SZ", D3, 12.0, None)])
    # vendor = 0.5 × (hfq/raw)，ratio=[10,10,11]
    out, rep = DAB.derive_adj_factor(
        fact, [(D1, 10.0), (D2, 11.0), (D3, 12.0)],
        [(D1, 100.0), (D2, 110.0), (D3, 132.0)], min_calib=2)
    assert out["adj_factor"].to_list() == pytest.approx([5.0, 5.0, 5.5])
    assert rep["calibration"]["const"] == pytest.approx(0.5)
    assert rep["calibration"]["anchor"] == str(D2)
    assert rep["calibration"]["rel_max_err"] < 1e-9


def test_derive_adj_factor_calibration_drift_fails_loud():
    """vendor 与 hfq/raw 漂移过大（混源/口径不同）→ 抛，不静默填错。"""
    fact = _fact([("X.SZ", D1, 10.0, 5.0), ("X.SZ", D2, 11.0, 9.9),
                  ("X.SZ", D3, 12.0, None)])
    with pytest.raises(ValueError, match="漂移"):
        DAB.derive_adj_factor(
            fact, [(D1, 10.0), (D2, 11.0), (D3, 12.0)],
            [(D1, 100.0), (D2, 110.0), (D3, 132.0)], min_calib=2)


def test_derive_adj_factor_drops_nonpositive_hfq():
    """hfq<=0 的源行（腾讯缺省/异常，实测 8 码 87 行）→ 剔除该日并记账，
    绝不产出非正 adj（CH argMax/qfq 基准会因此破坏）。"""
    fact = _fact([("X.SZ", D1, 10.0, None), ("X.SZ", D2, 11.0, None),
                  ("X.SZ", D3, 12.0, None)])
    out, rep = DAB.derive_adj_factor(
        fact, [(D1, 10.0), (D2, 11.0), (D3, 12.0)],
        [(D1, 20.0), (D2, -5.0), (D3, 24.0)])
    assert out["trade_date"].to_list() == [D1, D3]
    assert out["adj_factor"].to_list() == [2.0, 2.0]
    assert rep["n_dropped"] == 1
    assert all(v > 0 for v in out["adj_factor"].to_list())


# ── ingest_daily：sidecar 消费（只填 NULL）────────────────────────────

def _sidecar(rows: list[tuple]) -> pl.DataFrame:
    return pl.DataFrame(
        [{"code": c, "trade_date": d, "adj_factor": a} for c, d, a in rows],
        schema={"code": pl.String, "trade_date": pl.Date, "adj_factor": pl.Float64})


def _daily_frame(rows: list[tuple]) -> pl.DataFrame:
    """rows: (code, date, close, adj_factor)。"""
    return pl.DataFrame(
        [{"code": c, "trade_date": d, "close": close, "adj_factor": a}
         for c, d, close, a in rows],
        schema={"code": pl.String, "trade_date": pl.Date,
                "close": pl.Float64, "adj_factor": pl.Float64})


def test_apply_delisted_adj_fills_null_only_and_never_overwrites():
    df = _daily_frame([("X.SZ", D1, 10.0, None), ("X.SZ", D2, 11.0, 3.3),
                       ("X.SZ", D3, 12.0, None)])
    side = _sidecar([("X.SZ", D1, 2.0), ("X.SZ", D2, 9.9), ("X.SZ", D3, 4.0)])
    out = ingest_daily.apply_delisted_adj(df, side)
    assert out["adj_factor"].to_list() == [2.0, 3.3, 4.0]   # D2 vendor 3.3 不被覆盖


def test_apply_delisted_adj_no_sidecar_is_identity():
    df = _daily_frame([("X.SZ", D1, 10.0, None)])
    assert ingest_daily.apply_delisted_adj(df, None).equals(df)
    assert ingest_daily.apply_delisted_adj(df, _sidecar([])).equals(df)


def test_load_sidecar_missing_file_returns_none(tmp_path):
    assert ingest_daily.load_delisted_adj_sidecar(tmp_path / "nope.parquet") is None


def test_sidecar_roundtrip_and_meta(tmp_path):
    df = _sidecar([("X.SZ", D1, 2.0), ("Y.SZ", D2, 3.0)])
    out = DAB.write_sidecar(df, tmp_path, {"source": "tencent", "fetched_at": "t"})
    assert out.name == DAB.SIDECAR_NAME and out.is_file()
    assert ingest_daily.load_delisted_adj_sidecar(out).height == 2
    meta = json.loads((tmp_path / DAB.META_NAME).read_text(encoding="utf-8"))
    assert meta["source"] == "tencent" and meta["rows"] == 2


def test_sidecar_write_merges_existing_resumable(tmp_path):
    """断点续跑：已存在 sidecar 时合并（新值覆盖同键旧值）——限流失败重跑不丢已得数据。"""
    DAB.write_sidecar(_sidecar([("X.SZ", D1, 2.0), ("X.SZ", D2, 2.1)]),
                      tmp_path, {"source": "tencent"})
    out = DAB.write_sidecar(_sidecar([("X.SZ", D2, 9.9), ("Y.SZ", D1, 3.0)]),
                            tmp_path, {"source": "tencent"})
    merged = ingest_daily.load_delisted_adj_sidecar(out)
    assert merged.height == 3
    assert merged.filter(pl.col("code") == "X.SZ")["adj_factor"].to_list() == \
        pytest.approx([2.0, 9.9])          # 新值赢（同键）
    meta = json.loads((tmp_path / DAB.META_NAME).read_text(encoding="utf-8"))
    assert meta["rows"] == 3


# ── reconcile：不变量（sidecar 的 (code,date) 在 CH 必须有非空 adj）──

class _Res:
    def __init__(self, rows):
        self.result_rows = rows


class _FakeCH:
    def __init__(self, rows):
        self._rows = list(rows)
        self.queries: list[str] = []

    def query(self, sql: str):
        self.queries.append(sql)
        assert self._rows, f"多出未预置查询：{sql}"
        return _Res(self._rows.pop(0))


def test_reconcile_delisted_adj_consistent(tmp_path):
    import reconcile as RC
    side = _sidecar([("X.SZ", D1, 2.0), ("X.SZ", D2, 2.1)])
    p = side.write_parquet(tmp_path / RC.DELISTED_ADJ_NAME)
    fake = _FakeCH([[("X.SZ", D1, 2.0), ("X.SZ", D2, 2.1)]])
    assert RC._check_delisted_adj(fake, "db", tmp_path / RC.DELISTED_ADJ_NAME) is True
    assert any("adj_factor" in q for q in fake.queries)


def test_reconcile_delisted_adj_null_detected(tmp_path):
    import reconcile as RC
    side = _sidecar([("X.SZ", D1, 2.0), ("X.SZ", D2, 2.1)])
    side.write_parquet(tmp_path / RC.DELISTED_ADJ_NAME)
    fake = _FakeCH([[("X.SZ", D1, 2.0), ("X.SZ", D2, None)]])
    assert RC._check_delisted_adj(fake, "db", tmp_path / RC.DELISTED_ADJ_NAME) is False


def test_reconcile_delisted_adj_missing_row_detected(tmp_path):
    import reconcile as RC
    side = _sidecar([("X.SZ", D1, 2.0), ("X.SZ", D2, 2.1)])
    side.write_parquet(tmp_path / RC.DELISTED_ADJ_NAME)
    fake = _FakeCH([[("X.SZ", D1, 2.0)]])
    assert RC._check_delisted_adj(fake, "db", tmp_path / RC.DELISTED_ADJ_NAME) is False


def test_reconcile_delisted_adj_sidecar_missing_skips(tmp_path, capsys):
    import reconcile as RC
    assert RC._check_delisted_adj(_FakeCH([]), "db", tmp_path / "nope.parquet") is True
    assert "跳过" in capsys.readouterr().out
