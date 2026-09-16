# quark_download —— 夸克网盘原始数据下载

| 入口 | 用途 |
|---|---|
| `share_manifest.py` | 分享链接目录清单/下载（manifest 生成） |
| `download_level2.py` | 逐日逐码批量下载（断点续传；输出 `data/raw/quark_downloaded/<YYYYMMDD>/<code>/*.zip`） |
| `download_share_dir.py` | 常驻下载服务（配合上者做长任务） |
| `quark_client.py` | 传输/鉴权/下载层单点（三个入口共享，勿再各写一份） |

**解释器**：单解释器 = `platform/.venv/bin/python`（3.13；R27 前为 emb/T2，已退役）。**输出位置**：`data/raw/quark_downloaded/`（原始区，不入库）。
**下游**：`platform/tools/converters/`（→ 事实库 parquet）→ `platform/tools/ch_ingest/`（→ ClickHouse）。
