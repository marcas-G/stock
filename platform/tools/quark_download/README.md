# quark_download —— 夸克网盘原始数据下载

| 入口 | 用途 |
|---|---|
| `quark_share.py` | 分享链接目录清单/下载（manifest 生成） |
| `quark_download_v2.py` | 逐日逐码批量下载（断点续传；输出 `data/raw/quark_downloaded/<YYYYMMDD>/<code>/*.zip`） |
| `quark_download_server.py` | 常驻下载服务（配合上者做长任务） |

**解释器**：单解释器 = `platform/.venv/bin/python`（3.13；R27 前为 emb/T2，已退役）。**输出位置**：`data/raw/quark_downloaded/`（原始区，不入库）。
**下游**：`platform/tools/converters/`（→ 事实库 parquet）→ `platform/tools/ch_ingest/`（→ ClickHouse）。
