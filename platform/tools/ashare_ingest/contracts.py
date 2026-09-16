# 事实层列（原始价 + 复权因子）；复权价由股票池段的读取层（universe_stages.data.daily.load_daily）派生
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
