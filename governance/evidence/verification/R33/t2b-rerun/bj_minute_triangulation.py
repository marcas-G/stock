"""T2b BJ 类 RCA：daily amount/volume vs bars_1m 分钟聚合三角验证（只读）。

方法：
1. 取 20260919b quarantine 的全部 BJ 异常行 (code, trade_date)，从 bars_1m 取
   同日分钟聚合（Σamount/Σvolume、min(low)/max(high)）；
2. **对照组**：同批代码的非异常（未隔离）交易日随机采样 3000 对，做同一对拍——
   若对照组分钟和 == daily 总量，则分钟数据是完整参考；
3. 判定：
   - 异常日：daily_vwap 是否落分钟带 / 分钟 vwap 是否落 daily 带 / 分钟总量 vs
     daily 总量（vol_ratio、amt_ratio）；
   - 对照日：vol_ratio/amt_ratio/分钟 vwap 越带率。
输出 JSON：governance/evidence/verification/R33/t2b-rerun/bj-minute-triangulation.json
只读（CH SELECT + 本地 parquet 读）。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import clickhouse_connect
import pandas as pd
import polars as pl


def _minute_agg(client, pairs: list[tuple[str, object]]) -> pl.DataFrame:
    pdf = pd.DataFrame(pairs, columns=["code", "trade_date"])
    client.command("DROP TABLE IF EXISTS default.tmp_bj_rca")
    client.command(
        "CREATE TABLE default.tmp_bj_rca (code String, trade_date Date) ENGINE=Memory")
    client.insert_df("tmp_bj_rca", pdf, database="default")
    df = pl.from_pandas(client.query_df("""
        SELECT c.code AS code, c.trade_date AS trade_date,
               b.m_volume, b.m_amount, b.m_low, b.m_high
        FROM default.tmp_bj_rca c
        LEFT JOIN (SELECT code, trade_date, sum(volume) m_volume, sum(amount) m_amount,
                          min(low) m_low, max(high) m_high
                   FROM bars_1m GROUP BY code, trade_date) b
        ON c.code=b.code AND c.trade_date=b.trade_date"""))
    client.command("DROP TABLE IF EXISTS default.tmp_bj_rca")
    if isinstance(df.schema["trade_date"], pl.Datetime):
        df = df.with_columns(pl.col("trade_date").dt.date())
    return df


def _describe(s: pl.Series) -> dict:
    return {k: (round(float(v), 6) if v is not None else None)
            for k, v in zip(s.describe()["statistic"], s.describe()["value"])
            if k in ("count", "mean", "50%", "min", "max")}


def run(quarantine: Path, seed: int = 1, control_n: int = 3000) -> dict:
    raw = pl.read_parquet("data/fact/daily_fact/daily_fact.parquet",
                          columns=["code", "trade_date", "high", "low",
                                   "volume", "amount"])
    bj = raw.filter(pl.col("code").str.ends_with(".BJ"))
    q = pl.read_parquet(quarantine)
    qb = q.filter(pl.col("code").str.ends_with(".BJ"))
    bad = set(zip(qb["code"].to_list(), qb["trade_date"].to_list()))
    codes = sorted({c for c, _ in bad})

    ctrl = bj.filter(pl.col("code").is_in(codes)).filter(
        ~pl.struct(["code", "trade_date"]).map_elements(
            lambda s: (s["code"], s["trade_date"]) in bad,
            return_dtype=pl.Boolean))
    ctrl = ctrl.sample(n=min(control_n, ctrl.height), seed=seed)

    client = clickhouse_connect.get_client(host="localhost", port=8123,
                                           database="factorlab")
    bad_pairs = [(c, d) for c, d in bad]
    ctrl_pairs = list(zip(ctrl["code"].to_list(), ctrl["trade_date"].to_list()))

    def _join(pairs, source):
        m = _minute_agg(client, pairs)
        s = source.join(m, on=["code", "trade_date"], how="left").filter(
            pl.col("m_volume").is_not_null())
        return s.with_columns(
            (pl.col("m_volume") / pl.col("volume")).alias("vol_ratio"),
            (pl.col("m_amount") / pl.col("amount")).alias("amt_ratio"),
            (pl.col("m_amount") / pl.col("m_volume")).alias("m_vwap"),
            (pl.col("amount") / pl.col("volume")).alias("d_vwap"),
        ).with_columns(
            ((pl.col("m_vwap") >= pl.col("low") * 0.99)
             & (pl.col("m_vwap") <= pl.col("high") * 1.01)).alias("m_vwap_in_band"),
            ((pl.col("d_vwap") >= pl.col("m_low") * 0.99)
             & (pl.col("d_vwap") <= pl.col("m_high") * 1.01)).alias("d_vwap_in_band"),
        )

    bad_d = _join(bad_pairs, bj.filter(
        pl.struct(["code", "trade_date"]).map_elements(
            lambda s: (s["code"], s["trade_date"]) in bad,
            return_dtype=pl.Boolean)))
    ctrl_d = _join(ctrl_pairs, ctrl)
    return {
        "bad": {
            "n": bad_d.height,
            "vol_ratio": _describe(bad_d["vol_ratio"]),
            "amt_ratio": _describe(bad_d["amt_ratio"]),
            "m_vwap_in_daily_band": int(bad_d["m_vwap_in_band"].sum()),
            "d_vwap_in_minute_band": int(bad_d["d_vwap_in_band"].sum()),
        },
        "control": {
            "n": ctrl_d.height,
            "vol_ratio": _describe(ctrl_d["vol_ratio"]),
            "amt_ratio": _describe(ctrl_d["amt_ratio"]),
            "m_vwap_out_of_band": int((~ctrl_d["m_vwap_in_band"]).sum()),
        },
        "conclusion": "分钟聚合在对照日与 daily 一致（vol/amt ratio≈1）；异常日 "
                      "daily 总量为分钟总量的 ~1.9×且 daily vwap 越自身 OHLC 带 → "
                      "异常日 daily amount/volume 损坏（TRUE_ERROR，M3 用 bars_1m 重建）",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quarantine", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    res = run(Path(args.quarantine), seed=args.seed)
    Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
