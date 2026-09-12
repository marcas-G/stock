"""事实库列契约单点（DER-005）：bars_1m 与 tick 三表的列名与顺序。

从 data/intraday.py 上移（WS3c-2）；适配器（data/intraday.py）与核
（core/engine/minute.py 批读投影）共同消费，禁止第二份拷贝。
"""
from __future__ import annotations

BARS_1M_COLS = ["datetime", "trade_date", "code", "minute_index",
                    "session_type", "open", "high", "low", "close", "amount",
                    "volume"]
TICK_TRADES_COLS = ["trade_date", "code", "time_ms", "trade_no", "bs",
                      "price_x10000", "volume", "ask_seq", "bid_seq"]
TICK_ORDERS_COLS = ["trade_date", "code", "time_ms", "order_no",
                      "exch_order_no", "order_type", "bs", "price_x10000",
                      "volume"]
TICK_SNAP_COLS = ["trade_date", "code", "time_ms", "price", "volume",
                    "amount", "n_trades", "iopv", "trade_flag", "bs",
                    "cum_volume", "cum_amount", "high", "low", "open",
                    "prev_close", "ask_p1", "ask_p2", "ask_p3", "ask_p4",
                    "ask_p5", "ask_p6", "ask_p7", "ask_p8", "ask_p9",
                    "ask_p10", "ask_v1", "ask_v2", "ask_v3", "ask_v4",
                    "ask_v5", "ask_v6", "ask_v7", "ask_v8", "ask_v9",
                    "ask_v10", "bid_p1", "bid_p2", "bid_p3", "bid_p4",
                    "bid_p5", "bid_p6", "bid_p7", "bid_p8", "bid_p9",
                    "bid_p10", "bid_v1", "bid_v2", "bid_v3", "bid_v4",
                    "bid_v5", "bid_v6", "bid_v7", "bid_v8", "bid_v9",
                    "bid_v10", "wavg_ask", "wavg_bid", "ask_total",
                    "bid_total", "unweighted_index", "n_issues", "n_up",
                    "n_down", "n_flat"]
