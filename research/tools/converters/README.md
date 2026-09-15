# converters —— 原始行情 → 事实库 parquet

把 quark 下载的原始 CSV（zip）转换成 `data/fact/` 下的 parquet 事实表。

| 入口 | 输入 | 输出 | 说明 |
|---|---|---|---|
| `convert_tick_to_parquet.py` | `data/raw/quark_downloaded/<YYYYMMDD>/<code>/<code>.zip` 内 `逐笔成交.csv` / `逐笔委托.csv` / `行情.csv`（**GBK**） | 全量：`data/fact/tick_fact/{trades,orders,snapshots}/year=Y/month=M/part-*.parquet` + `_SUCCESS` + `_manifest/conversion_manifest.parquet`；`--only-day`：`data/calib/tick_fact_validation/...`（**不触碰月产物**） | 月分区；`MonthWriter` 原子落盘（tmp+fsync+os.replace）；flock 单实例 |
| `convert_minutes_to_parquet.py` | `data/raw/minutes/…` | `data/fact/bars_1m/year=Y/month=M/part-000.parquet` + `_SUCCESS` + `_state/…/_conversion.json` | 月级提交；`_committed_ok` 校验（schema/行数/row groups） |

**约定**：列契约与分区规则取平台 `core.factio.schema` / `core.factio.partitions`（研究侧不得自拼 `year=/month=`）；时间解析取 `core.factio.timeparse.parse_ms_numpy`（HHMMSSsss→ms-of-day）。
**解释器**：T2 = emb（`/data/students/gaolei/anaconda3/envs/emb/bin/python`，3.11）——本工具只需 `core.factio`（纯 polars/numpy/arrow），经 `tools/_env.py` 注入平台路径。
**不写 `data/` 以外的位置**。单日验证：`--only-day YYYYMMDD`——默认落
`data/calib/tick_fact_validation/`（独立根，`--out-root` 可覆盖；指向生产根会被拒绝），
避免"单日验证"原子替换掉整月 `part-000.parquet`。
