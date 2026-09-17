"""M6-06：Semantic Guard Closure——Label contract / manifest integrity / key alignment。

核心：M6 已建立的 Signal/Label/PIT/Artifact 边界变成不可静默绕过的 fail-fast invariants。

双腿参数化（env：duckdb|ch，见 tests/conftest.py）：依赖 DB 的测试（tamper/meta/
bundle/version guard 等需 run_factor 产物的）经 env.seed 数据描述建库 +
RunContext(data_backend) 双腿真跑——guard 断言作用于磁盘 parquet/summary
（清一色 ValueError），双腿共享同一条断言。纯 domain / 纯文件 artifact 测试
不触 DB，保持原位单腿。
"""

import datetime
import json

import polars as pl
import pytest

from factorlab.adapters.parquet_artifacts import (FactorArtifactBundle, SIGNAL_FILE, LABELS_FILE,
                                 LEGACY_PANEL_FILE, SUMMARY_FILE,
                                 load_factor_artifacts, load_label_artifact,
                                 load_signal_artifact, write_factor_artifacts)
from factorlab.core.engine.alignment import validate_signal_label_alignment
from factorlab.core.domain.frames import LabelArtifact, SignalArtifact, SignalMeta
from factorlab.core.domain.timing import DEFAULT_EOD_SIGNAL_TIMING
from factorlab.app.run import run_factor
from factorlab.app.context import RunContext
from factorlab.core.spec import load_spec


# ================================================================
# 建库（双腿数据描述 seed——等价原 duckdb 直建 DDL，经 env.seed 双腿灌数）
# ================================================================

_DAILY_COLS = [("ts_code", "str"), ("trade_date", "date"), ("open", "f64"),
               ("high", "f64"), ("low", "f64"), ("close", "f64"),
               ("vol", "f64"), ("amount", "f64")]
_ADJ_COLS = [("ts_code", "str"), ("trade_date", "date"), ("adj_factor", "f64")]
_CAL_COLS = [("exchange", "str"), ("cal_date", "date"), ("is_open", "i64")]
# list_date 按生产 ch DDL 为 Date（duckdb 腿 = VARCHAR 'YYYYMMDD' 同平台形态）
_SB_COLS = [("ts_code", "str"), ("symbol", "str"), ("exchange", "str"),
            ("list_date", "date"), ("industry", "str"), ("market", "str"),
            ("delist_date", "str?")]
_ST_COLS = [("ts_code", "str"), ("name", "str"), ("trade_date", "date"),
            ("type", "str"), ("type_name", "str")]


def _tables(n_days: int = 12):
    """原 build_db 等价数据描述：2024-01-02 起 n_days 个自然日，双票连续价格。

    daily：open=close=10+i / 20-i，high 1.01x / low 0.99x，vol 1e6；
    adj_factor 全 1.0；trade_cal 全日历 is_open=1；stock_basic 双票 20240101 上市
    （delist_date 未退市 NULL）；stock_st 空表。
    """
    dates = [datetime.date(2024, 1, 2) + datetime.timedelta(days=i)
             for i in range(n_days)]
    daily_rows, adj_rows = [], []
    for code, fn in (("000001.SZ", lambda i: 10.0 + i),
                     ("000002.SZ", lambda i: 20.0 - i)):
        for i, d in enumerate(dates):
            ds = d.strftime("%Y%m%d")
            daily_rows.append((code, ds, fn(i), fn(i) * 1.01, fn(i) * 0.99,
                               fn(i), 1e6, fn(i) * 1e6))
            adj_rows.append((code, ds, 1.0))
    return {
        "daily": (_DAILY_COLS, daily_rows),
        "adj_factor": (_ADJ_COLS, adj_rows),
        "trade_cal": (_CAL_COLS, [("SSE", d.strftime("%Y%m%d"), 1)
                                  for d in dates]),
        "stock_basic": (_SB_COLS,
                        [(code, code[:6], "SZSE", "20240101", "x", "主板", None)
                         for code in ("000001.SZ", "000002.SZ")]),
        "stock_st": (_ST_COLS, []),
    }


def _seed(env, n_days: int = 12) -> None:
    env.seed(_tables(n_days))


def _spec(tmp_path) -> object:
    path = tmp_path / "spec.yaml"
    path.write_text("""
name: demo
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "000002.SZ"]
date:
  start: "2024-01-02"
  end: "2024-01-17"
formula: |
  signal = close
""", encoding="utf-8")
    return load_spec(path)


def _run(env, tmp_path, spec, chunk_days=None):
    # ch 腿无文件路径概念：open_read 按 data_backend="ch" 分派，db_path 忽略
    kw = {"db_path": env.path} if env.backend == "duckdb" else {}
    return run_factor(spec, RunContext(
        data_backend=env.backend, output_dir=tmp_path / ("out_c" if chunk_days else "out"),
        float32=False, chunk_days=chunk_days, warmup_days=3, **kw))


def _meta(name="demo"):
    return SignalMeta(name=name, frequency="1d", timing=DEFAULT_EOD_SIGNAL_TIMING,
                      adjustment="qfq")


# ================================================================
# LabelArtifact strict contract（domain 级）
# ================================================================

def _label_frame(**extra):
    base = {
        "date": pl.Series(["2024-01-02", "2024-01-03"], dtype=pl.Date),
        "code": pl.Series(["000001", "000002"], dtype=pl.String),
        "forward_return_5d": pl.Series([0.1, 0.2], dtype=pl.Float64),
        "forward_return_20d": pl.Series([0.3, 0.4], dtype=pl.Float64),
    }
    base.update(extra)
    return pl.DataFrame(base)


@pytest.mark.parametrize("bad_col", ["signal", "close", "open", "future_price",
                                     "__factorlab_x", "foo", "industry"])
def test_label_artifact_rejects_non_label_columns(bad_col):
    with pytest.raises(ValueError, match="不允许非 label 列"):
        LabelArtifact(frame=_label_frame(**{bad_col: pl.Series([1.0, 2.0])}))


def test_label_artifact_arbitrary_horizons_still_allowed():
    LabelArtifact(frame=pl.DataFrame({
        "date": pl.Series(["2024-01-02"], dtype=pl.Date),
        "code": pl.Series(["000001"], dtype=pl.String),
        "forward_return_1d": pl.Series([0.1], dtype=pl.Float64),
        "forward_return_60d": pl.Series([0.2], dtype=pl.Float64),
    }))


# ================================================================
# __factorlab_* 内部列不允许落盘
# ================================================================

def _sig_frame(n=2, **extra):
    base = {
        "date": pl.Series([datetime.date(2024, 1, 2) + datetime.timedelta(days=i)
                           for i in range(n)], dtype=pl.Date),
        "code": pl.Series([f"00000{i + 1}" for i in range(n)], dtype=pl.String),
        "signal": pl.Series([0.5] * n, dtype=pl.Float64),
    }
    base.update(extra)
    return pl.DataFrame(base)


def test_internal_column_rejected_in_signal_persistence(tmp_path):
    sig = SignalArtifact(frame=_sig_frame(__factorlab_x=pl.Series([1.0, 2.0])), meta=_meta())
    lab = LabelArtifact(frame=pl.DataFrame({
        "date": pl.Series([datetime.date(2024, 1, 2), datetime.date(2024, 1, 3)], dtype=pl.Date),
        "code": pl.Series(["000001", "000002"], dtype=pl.String),
        "forward_return_5d": pl.Series([0.1, 0.2], dtype=pl.Float64),
    }))
    with pytest.raises(ValueError, match="内部保留列"):
        write_factor_artifacts(tmp_path / "out", sig, lab, _sig_frame(), {})
    assert not (tmp_path / "out" / SIGNAL_FILE).exists()   # 零文件写入


def test_internal_column_rejected_in_label_domain():
    """Label 的 __factorlab_* 在 domain 构造时已被拒（contract 收紧）——
    persistence 内部列 guard 对 label 不可达（先于构造失败），由 domain 测试锁定。"""
    with pytest.raises(ValueError, match="不允许非 label 列"):
        LabelArtifact(frame=pl.DataFrame({
            "date": pl.Series([datetime.date(2024, 1, 2), datetime.date(2024, 1, 3)], dtype=pl.Date),
            "code": pl.Series(["000001", "000002"], dtype=pl.String),
            "forward_return_5d": pl.Series([0.1, 0.2], dtype=pl.Float64),
            "__factorlab_x": pl.Series([1.0, 2.0]),
        }))


def test_internal_column_rejected_in_panel(tmp_path):
    sig = SignalArtifact(frame=_sig_frame(), meta=_meta())
    lab = LabelArtifact(frame=pl.DataFrame({
        "date": pl.Series([datetime.date(2024, 1, 2), datetime.date(2024, 1, 3)], dtype=pl.Date),
        "code": pl.Series(["000001", "000002"], dtype=pl.String),
        "forward_return_5d": pl.Series([0.1, 0.2], dtype=pl.Float64),
    }))
    panel = _sig_frame(__factorlab_y=pl.Series([3.0, 4.0]))
    with pytest.raises(ValueError, match="内部保留列"):
        write_factor_artifacts(tmp_path / "out", sig, lab, panel, {})
    assert not (tmp_path / "out" / SIGNAL_FILE).exists()


# ================================================================
# Alignment（row count / key / order）
# ================================================================

def test_alignment_row_count_mismatch():
    sig = SignalArtifact(frame=_sig_frame(3), meta=_meta())
    lab = LabelArtifact(frame=pl.DataFrame({
        "date": pl.Series([datetime.date(2024, 1, 2), datetime.date(2024, 1, 3)], dtype=pl.Date),
        "code": pl.Series(["000001", "000002"], dtype=pl.String),
        "forward_return_5d": pl.Series([0.1, 0.2], dtype=pl.Float64),
    }))
    with pytest.raises(ValueError, match="row count 不一致"):
        validate_signal_label_alignment(sig, lab)


def test_alignment_key_mismatch():
    sig = SignalArtifact(frame=_sig_frame(), meta=_meta())
    lab = LabelArtifact(frame=pl.DataFrame({
        "date": pl.Series([datetime.date(2024, 1, 2), datetime.date(2024, 1, 3)], dtype=pl.Date),
        "code": pl.Series(["000001", "000003"], dtype=pl.String),   # B → C
        "forward_return_5d": pl.Series([0.1, 0.2], dtype=pl.Float64),
    }))
    with pytest.raises(ValueError, match="key 不一致"):
        validate_signal_label_alignment(sig, lab)


def test_alignment_key_order_mismatch():
    sig = SignalArtifact(frame=_sig_frame(), meta=_meta())
    lab = LabelArtifact(frame=pl.DataFrame({
        "date": pl.Series([datetime.date(2024, 1, 3), datetime.date(2024, 1, 2)], dtype=pl.Date),
        "code": pl.Series(["000002", "000001"], dtype=pl.String),   # 顺序颠倒
        "forward_return_5d": pl.Series([0.2, 0.1], dtype=pl.Float64),
    }))
    with pytest.raises(ValueError, match="key 不一致"):
        validate_signal_label_alignment(sig, lab)


def test_alignment_mismatch_zero_files_written(tmp_path):
    """pair mismatch → write_factor_artifacts 在任何文件写出前失败（零文件）。"""
    sig = SignalArtifact(frame=_sig_frame(), meta=_meta())
    lab = LabelArtifact(frame=pl.DataFrame({
        "date": pl.Series([datetime.date(2024, 1, 2), datetime.date(2024, 1, 3)], dtype=pl.Date),
        "code": pl.Series(["000001", "000003"], dtype=pl.String),
        "forward_return_5d": pl.Series([0.1, 0.2], dtype=pl.Float64),
    }))
    with pytest.raises(ValueError, match="key 不一致"):
        write_factor_artifacts(tmp_path / "out", sig, lab, _sig_frame(), {})
    assert not (tmp_path / "out" / SIGNAL_FILE).exists()
    assert not (tmp_path / "out" / SUMMARY_FILE).exists()


# ================================================================
# Manifest tampering（rows / columns / horizons / meta）
# ================================================================

def _summary(tmp_path) -> dict:
    return json.loads((tmp_path / "out" / SUMMARY_FILE).read_text(encoding="utf-8"))


def _tamper(tmp_path, path: str, key: str, value) -> None:
    s = _summary(tmp_path)
    obj = s
    for part in path.split("."):
        if part:
            obj = obj[part]
    obj[key] = value
    (tmp_path / "out" / SUMMARY_FILE).write_text(json.dumps(s), encoding="utf-8")


def test_tamper_manifest_signal_rows(env, tmp_path):
    _seed(env)
    _run(env, tmp_path, _spec(tmp_path))
    _tamper(tmp_path, "artifacts.signal", "rows", 999)
    with pytest.raises(ValueError, match="signal manifest rows 999 != 实际 parquet rows"):
        load_signal_artifact(tmp_path / "out")


def test_tamper_manifest_label_rows(env, tmp_path):
    _seed(env)
    _run(env, tmp_path, _spec(tmp_path))
    _tamper(tmp_path, "artifacts.labels", "rows", 999)
    with pytest.raises(ValueError, match="labels manifest rows 999 != 实际 parquet rows"):
        load_label_artifact(tmp_path / "out")


def test_tamper_manifest_signal_columns(env, tmp_path):
    _seed(env)
    _run(env, tmp_path, _spec(tmp_path))
    _tamper(tmp_path, "artifacts.signal", "columns", ["date", "signal", "code"])
    with pytest.raises(ValueError, match="signal manifest columns"):
        load_signal_artifact(tmp_path / "out")


def test_tamper_parquet_rows(env, tmp_path):
    _seed(env)
    _run(env, tmp_path, _spec(tmp_path))
    sig = pl.read_parquet(tmp_path / "out" / SIGNAL_FILE)
    sig.head(sig.height - 1).write_parquet(tmp_path / "out" / SIGNAL_FILE)  # 少一行，manifest 不改
    # R02-I8：内容 sha256 交叉校验先于 manifest rows 计数（更强，仍拒载）
    with pytest.raises(ValueError, match="signal 内容 sha256 与 manifest 不一致"):
        load_signal_artifact(tmp_path / "out")


def test_tamper_parquet_extra_column(env, tmp_path):
    _seed(env)
    _run(env, tmp_path, _spec(tmp_path))
    sig = pl.read_parquet(tmp_path / "out" / SIGNAL_FILE)
    sig.with_columns(pl.lit(1.0).alias("extra")).write_parquet(tmp_path / "out" / SIGNAL_FILE)
    # R02-I8：列变化同样先被内容 hash 拒绝
    with pytest.raises(ValueError, match="signal 内容 sha256 与 manifest 不一致"):
        load_signal_artifact(tmp_path / "out")


@pytest.mark.parametrize("horizons", [[5], [5, 60], [20, 5]])
def test_tamper_label_horizons(env, tmp_path, horizons):
    _seed(env)
    _run(env, tmp_path, _spec(tmp_path))
    _tamper(tmp_path, "artifacts.labels", "horizons", horizons)
    with pytest.raises(ValueError, match="horizons"):
        load_label_artifact(tmp_path / "out")


# ================================================================
# Meta corrupt（清晰 ValueError，非裸 KeyError）
# ================================================================

def _del_meta(tmp_path, key: str, sub: str | None = None) -> None:
    s = _summary(tmp_path)
    meta = s["artifacts"]["signal"]["meta"]
    if sub:
        del meta["timing"][sub]
    else:
        del meta[key]
    (tmp_path / "out" / SUMMARY_FILE).write_text(json.dumps(s), encoding="utf-8")


def test_meta_missing_timing(env, tmp_path):
    _seed(env)
    _run(env, tmp_path, _spec(tmp_path))
    _del_meta(tmp_path, "timing")
    with pytest.raises(ValueError, match="signal meta 缺少字段"):
        load_signal_artifact(tmp_path / "out")


def test_meta_missing_name(env, tmp_path):
    _seed(env)
    _run(env, tmp_path, _spec(tmp_path))
    _del_meta(tmp_path, "name")
    with pytest.raises(ValueError, match="signal meta 缺少字段"):
        load_signal_artifact(tmp_path / "out")


@pytest.mark.parametrize("sub", ["information_cutoff", "available_at", "default_earliest_execution"])
def test_meta_missing_timing_subfield(env, tmp_path, sub):
    _seed(env)
    _run(env, tmp_path, _spec(tmp_path))
    _del_meta(tmp_path, None, sub)
    with pytest.raises(ValueError, match="signal meta timing 缺少字段"):
        load_signal_artifact(tmp_path / "out")


def test_meta_invalid_timing_enum(env, tmp_path):
    _seed(env)
    _run(env, tmp_path, _spec(tmp_path))
    _tamper(tmp_path, "artifacts.signal.meta.timing", "information_cutoff", "tomorrow")
    with pytest.raises(ValueError, match="invalid signal timing value"):
        load_signal_artifact(tmp_path / "out")


# ================================================================
# Bundle loader
# ================================================================

def test_bundle_loader(env, tmp_path):
    _seed(env)
    r = _run(env, tmp_path, _spec(tmp_path))
    bundle = load_factor_artifacts(tmp_path / "out")
    assert isinstance(bundle, FactorArtifactBundle)
    assert bundle.signal.frame.equals(r.signal_artifact.frame)
    assert bundle.labels.frame.equals(r.label_artifact.frame)
    assert not (tmp_path / "out" / LEGACY_PANEL_FILE).exists() is False  # panel 存在但 bundle 不加载


# ================================================================
# E2E Semantic Gate（run → disk → bundle 全链验证）
# ================================================================

def test_semantic_gate_e2e(env, tmp_path):
    _seed(env)
    r = _run(env, tmp_path, _spec(tmp_path), chunk_days=4)
    bundle = load_factor_artifacts(tmp_path / "out_c")
    # Signal 无未来字段（磁盘复验）
    for c in bundle.signal.frame.columns:
        assert not (c.startswith("forward_") or c.startswith("future_")
                    or c in ("target", "label"))
    # Label 无 signal/非 label 字段
    for c in bundle.labels.frame.columns:
        assert c in {"date", "code"} or c.startswith("forward_return_")
    assert "signal" not in bundle.labels.frame.columns
    # key 对齐（bundle loader 已验证——显式断言）
    assert bundle.signal.frame.select(["date", "code"]).equals(
        bundle.labels.frame.select(["date", "code"]))
    # timing
    assert bundle.signal.meta.timing == DEFAULT_EOD_SIGNAL_TIMING
    # disk round-trip
    assert bundle.signal.frame.equals(r.signal_artifact.frame)
    assert bundle.labels.frame.equals(r.label_artifact.frame)


# ================================================================
# M6-06A：persistence label schema self-consistency（v2；R30 fix 波）
# ================================================================

def _label_60d():
    return LabelArtifact(frame=pl.DataFrame({
        "date": pl.Series([datetime.date(2024, 1, 2), datetime.date(2024, 1, 3)], dtype=pl.Date),
        "code": pl.Series(["000001", "000002"], dtype=pl.String),
        "forward_return_60d": pl.Series([0.1, 0.2], dtype=pl.Float64),
    }))


def _label_5_60():
    return LabelArtifact(frame=pl.DataFrame({
        "date": pl.Series([datetime.date(2024, 1, 2), datetime.date(2024, 1, 3)], dtype=pl.Date),
        "code": pl.Series(["000001", "000002"], dtype=pl.String),
        "forward_return_5d": pl.Series([0.1, 0.2], dtype=pl.Float64),
        "forward_return_60d": pl.Series([0.3, 0.4], dtype=pl.Float64),
    }))


def test_domain_allows_60d_only_label():
    """Domain 仍允许任意 horizon——只是不能用 label schema v2 落盘。"""
    _label_60d()   # 构造 PASS


def test_writer_rejects_60d_only_label(tmp_path):
    lab = _label_60d()
    with pytest.raises(ValueError, match="Label schema v2"):
        write_factor_artifacts(tmp_path / "out", SignalArtifact(frame=_sig_frame(), meta=_meta()),
                               lab, _sig_frame(), {})
    for f in (SIGNAL_FILE, LABELS_FILE, LEGACY_PANEL_FILE, SUMMARY_FILE):
        assert not (tmp_path / "out" / f).exists(), f"{f} 被写出（fail-before-write 违反）"


def test_writer_rejects_5_60_label(tmp_path):
    lab = _label_5_60()
    with pytest.raises(ValueError, match="Label schema v2"):
        write_factor_artifacts(tmp_path / "out", SignalArtifact(frame=_sig_frame(), meta=_meta()),
                               lab, _sig_frame(), {})
    for f in (SIGNAL_FILE, LABELS_FILE, LEGACY_PANEL_FILE, SUMMARY_FILE):
        assert not (tmp_path / "out" / f).exists()


# ── R30 fix 波：label schema v1 → v2（horizons=(1, 5, 20)）────────────────────

def test_writer_manifest_labels_schema_version_v2(env, tmp_path):
    """writer 必须把 labels schema_version 写成 2（v1 老产物不可再读——迁移节）。"""
    from factorlab.adapters.parquet_artifacts import LABEL_SCHEMA_VERSION
    assert LABEL_SCHEMA_VERSION == 2, "LABEL_SCHEMA_VERSION 未 bump 到 2"
    _seed(env)
    _run(env, tmp_path, _spec(tmp_path))
    s = _summary(tmp_path)
    assert s["artifacts"]["labels"]["schema_version"] == 2
    assert s["artifacts"]["labels"]["horizons"] == [1, 5, 20]


def test_loader_legacy_label_v1_error_points_to_migration(env, tmp_path):
    """v1 老产物（schema_version=1，(5, 20) 时代）读取 → 报错必须指明 v1→v2 迁移。

    裁定（R30 终评审）：bump LABEL_SCHEMA_VERSION=2；读取器不 silent migrate——
    错误文案必须给出「v1→v2 迁移（重跑 run 重生成）」路径。
    """
    _seed(env)
    _run(env, tmp_path, _spec(tmp_path))
    _tamper(tmp_path, "artifacts.labels", "schema_version", 1)
    with pytest.raises(ValueError, match="v1→v2 迁移"):
        load_label_artifact(tmp_path / "out")


def test_writer_accepts_v2_1_5_20_roundtrip(env, tmp_path):
    """v2 正常 roundtrip：范围 = 实际 (1, 5, 20)（旧名 5_20 与 v2 语义脱节）。"""
    _seed(env)
    r = _run(env, tmp_path, _spec(tmp_path))
    loaded = load_label_artifact(tmp_path / "out")
    assert loaded.frame.equals(r.label_artifact.frame)


def test_manifest_horizons_from_actual_columns(env, tmp_path):
    _seed(env)
    _run(env, tmp_path, _spec(tmp_path))
    s = _summary(tmp_path)
    from factorlab.adapters.parquet_artifacts import extract_forward_horizons
    lab = pl.read_parquet(tmp_path / "out" / LABELS_FILE)
    assert s["artifacts"]["labels"]["horizons"] == list(extract_forward_horizons(list(lab.columns)))
    assert s["artifacts"]["labels"]["horizons"] == [1, 5, 20]


@pytest.mark.parametrize("bad", [True, 1.0, "1", -1])
def test_format_version_strict_type(env, tmp_path, bad):
    _seed(env)
    _run(env, tmp_path, _spec(tmp_path))
    _tamper(tmp_path, "", "artifact_format_version", bad)
    with pytest.raises(ValueError, match="invalid artifact format version"):
        load_signal_artifact(tmp_path / "out")


@pytest.mark.parametrize("bad", [True, 1.0, "1", 0, -1])
def test_signal_schema_strict_type(env, tmp_path, bad):
    _seed(env)
    _run(env, tmp_path, _spec(tmp_path))
    _tamper(tmp_path, "artifacts.signal", "schema_version", bad)
    with pytest.raises(ValueError, match="invalid signal schema version"):
        load_signal_artifact(tmp_path / "out")


@pytest.mark.parametrize("bad", [True, 1.0, "1", 0, -1])
def test_label_schema_strict_type(env, tmp_path, bad):
    _seed(env)
    _run(env, tmp_path, _spec(tmp_path))
    _tamper(tmp_path, "artifacts.labels", "schema_version", bad)
    with pytest.raises(ValueError, match="invalid labels schema version"):
        load_label_artifact(tmp_path / "out")


def test_int_unsupported_version_still_unsupported(env, tmp_path):
    _seed(env)
    _run(env, tmp_path, _spec(tmp_path))
    _tamper(tmp_path, "", "artifact_format_version", 2)
    with pytest.raises(ValueError, match="unsupported artifact format version 2"):
        load_signal_artifact(tmp_path / "out")


def test_meta_name_non_string(env, tmp_path):
    _seed(env)
    _run(env, tmp_path, _spec(tmp_path))
    _tamper(tmp_path, "artifacts.signal.meta", "name", 123)
    with pytest.raises(ValueError, match="signal meta name 必须为 non-empty str"):
        load_signal_artifact(tmp_path / "out")


def test_meta_adjustment_non_string(env, tmp_path):
    _seed(env)
    _run(env, tmp_path, _spec(tmp_path))
    _tamper(tmp_path, "artifacts.signal.meta", "adjustment", [])
    with pytest.raises(ValueError, match="adjustment 必须为 null 或 str"):
        load_signal_artifact(tmp_path / "out")


def test_meta_timing_non_string(env, tmp_path):
    _seed(env)
    _run(env, tmp_path, _spec(tmp_path))
    _tamper(tmp_path, "artifacts.signal.meta.timing", "information_cutoff", 1)
    with pytest.raises(ValueError, match="information_cutoff 必须为 str"):
        load_signal_artifact(tmp_path / "out")
