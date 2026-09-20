"""R37 T4：19 行现代单位 bug 的 bars_1m 分钟三角复核（只读 CH + 本地 parquet）。

方法（同 R33 BJ 三角验证）：
1. 取 scoped-55-rows.csv 中 class=unit_like 的 19 行；
2. 逐行从 ``bars_1m`` 取同日分钟聚合（Σamount/Σvolume、min(low)/max(high)、bar 数）；
   bars_1m 覆盖 2020-01-02 起 → 19 行中 15 行可核（4 个 2015 行无覆盖，另有
   vendor xlsx 源与相邻日连续性证据，见 rca.md）；
3. **对照组**：每个 (code, 异常日) 取同码上一交易日（非隔离日）做同一对拍——
   对照日 vol/amt 比应 ≈1，证明分钟聚合是完整参考；
4. 判定字段归属：
   - ``volume`` 单位 bug（手→股）：``m_volume/daily_volume ≈ 100`` 且
     ``m_amount/daily_amount ≈ 1``，且 daily 的 amount/(volume×100) 落 OHLC 带；
   - ``amount`` 问题：``m_amount/daily_amount ≈ 100`` 且 vol 比 ≈1。

输出（证据目录）：
- ``minute-triangulation-19.csv``：逐行对照表；
- ``minute-triangulation-19.json``：汇总 + 对照组统计 + 判定。

只读：CH SELECT + 本地 parquet；不写 data/。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import clickhouse_connect
import polars as pl


def _load_targets(csv_path: Path) -> pl.DataFrame:
    return (pl.read_csv(csv_path, try_parse_dates=True)
            .filter(pl.col("class") == "unit_like")
            .sort(["trade_date", "code"]))


def _daily_frame(codes: list[str], date_min, date_max) -> pl.DataFrame:
    return (pl.scan_parquet("data/fact/daily_fact/daily_fact.parquet")
            .filter(pl.col("code").is_in(codes)
                    & (pl.col("trade_date") >= date_min)
                    & (pl.col("trade_date") <= date_max))
            .select(["code", "trade_date", "open", "high", "low", "close",
                     "volume", "amount"])
            .collect())


def _minute_agg(client, codes: list[str], dates: list) -> pl.DataFrame:
    code_list = ", ".join(f"'{c}'" for c in sorted(set(codes)))
    date_list = ", ".join(f"toDate('{d.isoformat()}')" for d in sorted(set(dates)))
    pdf = client.query_df(f"""
        SELECT code, trade_date,
               sum(volume) AS m_volume, sum(amount) AS m_amount,
               min(low) AS m_low, max(high) AS m_high, count() AS n_bars
        FROM bars_1m
        WHERE code IN ({code_list}) AND trade_date IN ({date_list})
        GROUP BY code, trade_date""")
    df = pl.from_pandas(pdf)
    if df.height and isinstance(df.schema["trade_date"], pl.Datetime):
        df = df.with_columns(pl.col("trade_date").dt.date())
    return df


def _control_dates(daily: pl.DataFrame, targets: pl.DataFrame) -> pl.DataFrame:
    """每个异常日取同码上一交易日；排除异常日自身（相邻两日双异常时跳过）。"""
    bad = set(zip(targets["code"].to_list(), targets["trade_date"].to_list()))
    rows = []
    for r in targets.iter_rows(named=True):
        prev = (daily.filter((pl.col("code") == r["code"])
                             & (pl.col("trade_date") < r["trade_date"]))
                .sort("trade_date").tail(1))
        if prev.height:
            d = prev["trade_date"][0]
            if (r["code"], d) not in bad:
                rows.append({"code": r["code"], "trade_date": d,
                             "anomaly_date": r["trade_date"]})
    return pl.DataFrame(rows)


def _compare(daily: pl.DataFrame, m: pl.DataFrame) -> pl.DataFrame:
    j = daily.join(m, on=["code", "trade_date"], how="left")
    return j.with_columns(
        (pl.col("amount") / pl.col("volume")).alias("d_vwap"),
        (pl.col("amount") / (pl.col("volume") * 100)).alias("d_vwap_v100"),
        (pl.col("m_amount") / pl.col("m_volume")).alias("m_vwap"),
        (pl.col("m_volume") / pl.col("volume")).alias("vol_ratio"),
        (pl.col("m_volume") / (pl.col("volume") * 100)).alias("vol_ratio_v100"),
        (pl.col("m_amount") / pl.col("amount")).alias("amt_ratio"),
    ).with_columns(
        ((pl.col("m_vwap") >= pl.col("low") * 0.99)
         & (pl.col("m_vwap") <= pl.col("high") * 1.01)).alias("m_vwap_in_band"),
        ((pl.col("d_vwap_v100") >= pl.col("low") * 0.99)
         & (pl.col("d_vwap_v100") <= pl.col("high") * 1.01)).alias("d_vwap_v100_in_band"),
        ((pl.col("d_vwap") >= pl.col("low") * 0.99)
         & (pl.col("d_vwap") <= pl.col("high") * 1.01)).alias("d_vwap_in_band"),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--out-prefix", required=True)
    args = ap.parse_args()

    targets = _load_targets(Path(args.rows))
    codes = sorted(set(targets["code"].to_list()))
    dates = targets["trade_date"].to_list()
    dmin, dmax = min(dates), max(dates)
    daily = _daily_frame(codes, dmin - dt.timedelta(days=30), dmax)
    ctrl = _control_dates(daily, targets)

    client = clickhouse_connect.get_client(host="127.0.0.1", port=8123,
                                           database="factorlab")
    all_dates = sorted(set(dates) | set(ctrl["trade_date"].to_list()))
    m = _minute_agg(client, codes, all_dates)

    anom = (_compare(daily.join(targets.select(["code", "trade_date"]),
                                on=["code", "trade_date"], how="semi"), m)
            .sort(["trade_date", "code"]))
    ctrl_cmp = (_compare(daily.join(
        ctrl.select(pl.col("code"), pl.col("trade_date")),
        on=["code", "trade_date"], how="semi"), m).sort(["trade_date", "code"]))

    out_csv = Path(f"{args.out_prefix}.csv")
    anom.write_csv(out_csv)
    ctrl_csv = Path(f"{args.out_prefix}-control.csv")
    ctrl_cmp.write_csv(ctrl_csv)

    bars = anom.filter(pl.col("m_volume").is_not_null())
    no_bars = anom.filter(pl.col("m_volume").is_null())
    report = {
        "target_rows": anom.height,
        "with_bars_1m": bars.height,
        "without_bars_1m": [
            {"code": r["code"], "trade_date": str(r["trade_date"])}
            for r in no_bars.iter_rows(named=True)],
        "control_rows": ctrl_cmp.height,
        "control_stats": {
            "vol_ratio": _stats(ctrl_cmp["vol_ratio"]),
            "amt_ratio": _stats(ctrl_cmp["amt_ratio"]),
            "m_vwap_out_of_band": int((~ctrl_cmp["m_vwap_in_band"]).sum()),
        },
        "anomaly_with_bars_verdicts": [
            {
                "code": r["code"], "trade_date": str(r["trade_date"]),
                "daily_volume": r["volume"], "m_volume": r["m_volume"],
                "daily_amount": r["amount"], "m_amount": r["m_amount"],
                "vol_ratio": r["vol_ratio"], "vol_ratio_v100": r["vol_ratio_v100"],
                "amt_ratio": r["amt_ratio"],
                "m_vwap": r["m_vwap"], "d_vwap": r["d_vwap"],
                "d_vwap_v100": r["d_vwap_v100"],
                "d_vwap_v100_in_band": bool(r["d_vwap_v100_in_band"]),
                "volume_is_lots_v100": bool(
                    abs(r["vol_ratio_v100"] - 1) < 0.01
                    and abs(r["amt_ratio"] - 1) < 0.01),
            }
            for r in bars.iter_rows(named=True)
        ],
        "csv": str(out_csv),
        "control_csv": str(ctrl_csv),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    out_json = Path(f"{args.out_prefix}.json")
    out_json.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def _stats(s: pl.Series) -> dict:
    if s.len() == 0:
        return {}
    d = s.drop_nulls().describe()
    return {k: (round(float(v), 8) if v is not None else None)
            for k, v in zip(d["statistic"], d["value"])
            if k in ("count", "mean", "50%", "min", "max")}


if __name__ == "__main__":
    raise SystemExit(main())
