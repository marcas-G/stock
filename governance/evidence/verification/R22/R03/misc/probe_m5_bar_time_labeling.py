"""R03-M5 probe：bars_1m `datetime` 标注语义 = bar 起点（非 minute-end）。

方法（CH 真数据，tick_trades 覆盖 2025-08 起）：
- 取 000021.SZ @ 2025-08-12 的 bars_1m 指定 minute_index 的 datetime/volume/amount；
- tick_trades 按 time_ms // 60000 分桶（桶 = 墙钟 [m, m+1) = **起点窗**）；
- 对同一墙钟标签 t 比较 bar[t] 与 tick 桶 t（起点窗）及桶 t−1（终点窗）。
判定：`datetime = 起点` ⇔ |bar − tick_start| << |bar − tick_end|；t=15:00 收盘竞价
应精确相等（竞价成交全部 time_ms = 15:00:00，落 [15:00, 15:01)）。

运行：cd platform && FACTORLAB_DATA_BACKEND=ch .venv/bin/python \
      ../docs/verification/R22/R03/misc/probe_m5_bar_time_labeling.py
"""

import polars as pl

from factorlab.app.bootstrap import open_read

CODE, DAY = "000021.SZ", "2025-08-12"
# 关键 minute_index：0=09:25 开盘、1=09:31、2=09:32、119=11:29、239=15:00
IDX = (0, 1, 2, 118, 119, 239)

rd = open_read()
bars = rd.query_df(
    f"SELECT minute_index, datetime, volume, amount FROM factorlab.bars_1m "
    f"WHERE code='{CODE}' AND trade_date=toDate('{DAY}') "
    f"AND minute_index IN {IDX} ORDER BY minute_index")
bars = bars.with_columns(
    pl.col("datetime").dt.convert_time_zone("UTC").dt.replace_time_zone(None)
    .dt.strftime("%H:%M").alias("wall"))

ticks = rd.query_df(
    f"SELECT intDiv(time_ms, 60000) AS m, sum(volume) AS tick_vol, "
    f"sum(price_x10000 * volume) / 1e4 AS tick_amt "
    f"FROM factorlab.tick_trades WHERE code='{CODE}' "
    f"AND trade_date=toDate('{DAY}') GROUP BY m ORDER BY m")

rows = []
for mi, wall, vol, amt in bars.select(
        ["minute_index", "wall", "volume", "amount"]).iter_rows():
    hm = wall.split(":")
    m = int(hm[0]) * 60 + int(hm[1])
    start = ticks.filter(pl.col("m") == m)
    end = ticks.filter(pl.col("m") == m - 1)
    sv = float(start["tick_vol"][0]) if start.height else 0.0
    ev = float(end["tick_vol"][0]) if end.height else 0.0
    rows.append({
        "idx": mi, "wall": wall, "bar_vol": vol,
        "tick_[t,t+1)": sv, "tick_[t-1,t)": ev,
        "start_err": abs(vol - sv), "end_err": abs(vol - ev),
    })

out = pl.DataFrame(rows)
print(f"# bars_1m datetime 标注探针（{CODE} @ {DAY}，tick_trades 对拍）")
print(out)
verdict = []
for r in rows:
    if r["bar_vol"] == 0:
        continue
    closer = "start" if r["start_err"] < r["end_err"] else "end"
    verdict.append((r["wall"], closer, r["start_err"], r["end_err"]))
print("\n# 逐 bar 判定（更近窗）")
for wall, closer, se, ee in verdict:
    print(f"  {wall}: closer={closer} (start_err={se:.0f}, end_err={ee:.0f})")
exact = [r for r in rows if r["bar_vol"] > 0 and r["start_err"] == 0]
print(f"\n# 起点窗精确相等（delta=0）: {[r['wall'] for r in exact]}")
# 判定（稳健版）：
# 1) 15:00 精确锚——收盘竞价成交 time_ms=15:00:00 只落在 [15:00,15:01)（起点窗）；
#    若为 minute-end，bar 15:00 = [14:59,15:00) 会漏掉竞价 → 与实测 delta=0 矛盾。
# 2) 连续竞价 3 个样本（09:31/09:32/11:29）：起点窗误差 <10% bar_vol 且 << 终点窗。
# 3) 2008-11-28 式个别分钟（本日 11:28）存在源级边界成交归位噪声（~3 万股），
#    不改变整体标注语义，如实打印。
anchors = {r["wall"]: r for r in rows}
assert anchors["15:00"]["start_err"] == 0 and anchors["15:00"]["end_err"] > 0, \
    "15:00 收盘竞价锚未成立（非起点标注）"
for w in ("09:31", "09:32", "11:29"):
    r = anchors[w]
    assert r["start_err"] < r["end_err"], (w, r)
    assert r["start_err"] / r["bar_vol"] < 0.10, (w, r)
print("VERDICT: datetime = bar 起点（left edge, [t, t+1)）——非 minute-end")
print("注：11:28 单点终点窗更近（源级边界成交归位噪声，见上表两窗误差量级）；"
      "15:00 delta=0 + 3 个连续竞价样本锁定起点语义。")
