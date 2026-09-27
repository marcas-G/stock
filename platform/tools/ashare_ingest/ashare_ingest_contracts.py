# ashare_ingest 的事实层列契约（原始价 + 复权因子）；复权价由股票池段的读取层派生。
# 模块名带工具前缀，避免与其他工具的 contracts 模块产生顶层导入冲突。
DAILY_REQUIRED = {
    'trade_date', 'code', 'open', 'high', 'low', 'close', 'adj_factor',
    'amount', 'volume'
}

# Local normalized names deliberately mirror the frozen jqdata source semantics.
FUNDAMENTALS_REQUIRED = {
    'available_date', 'code', 'market_cap', 'pe_ratio', 'operating_revenue',
    'total_assets', 'total_liability', 'list_date', 'is_st'
}

INDEX_DAILY_REQUIRED = {'trade_date', 'pre_close'}
