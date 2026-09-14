# R4d quark→CH 链路梳理（2026-09-15）

## 链路（唯一路径）
```
quark_download/（原始 zip）→ converters/（parquet 事实表 + _SUCCESS + manifest）
  → ch_ingest/（CH 灌入：bars_1m / tick_* / daily 族 + derive_stk_limit）
  → reconcile.py（唯一对账入口，全库一致才 exit 0）
```

## 本轮完成
- 文档失真修正（3 处，均有行号证据）：
  · ch_ingest/README.md：端口 19000→**8123**（HTTP；19000 是 tcp client 端口）；
    源路径 →、→、
    →
  · 1m_features/README.md：→；
    bars/daily 数据路径补  段
  · docs/strategies/crash_bottom_leader_strategy.md：→
- 三份缺失 README（converters / quark_download / strategies）补齐（入口/输入输出/解释器/下游）
- platform/.venv/bin/python research/tools/ch_ingest/reconcile.py
daily 层:
  daily        CH=      18,162,795 源=      18,162,795  一致
  adj_factor   CH=      18,162,795 源=      18,162,795  一致
  daily_basic  CH=      18,162,795 源=      18,162,795  一致
  trade_cal    CH=           8,772 源=           8,772  一致
  stock_basic  CH=           5,866 源=           5,866  一致
bars_1m:
  bars_1m: 全部 80 分区一致
tick_trades:
  tick_trades: 全部 13 分区一致
tick_orders:
  tick_orders: 全部 13 分区一致
tick_snapshots:
  tick_snapshots: 全部 13 分区一致
全库一致 接入 Makefile（唯一对账入口；需 CH 在线 + 平台 venv）
- converters 的 T2 兼容复验：emb(3.11) 下可导入且 parse_ms 走 factio 单点（冒烟 34200000）

## 延后（pending #12②③）
- 入口改名（quark_download_v2→download_level2 等）：阻塞于用户级技能副本，需同批更新
- 各工具入口统一为 run.py 子命令形态：需逐工具 CLI 重构 + 冒烟测试先行
