"""δ 滞后归因纯函数（W1 校准; 口径见规格 §2 δ 滞后定律与 test_delta 文档）

attrib_lag 输入已由调用方裁剪为同一 (side, price) 的加单序列 adds（(ms, vol) 升序,
ms ∈ (t1, t2+window]）; snap_delta = S2 量 − S1 量（tick_fact snapshots, ms-of-day）。
返回 {cls, lag_ms?}: zero_lag / over / no_exact / lag(ms 命中) / lag_gt_window。
"""
WINDOW_MS = 500  # δ 探测窗上限（超窗只统计不归因）

def attrib_lag(adds, t1, t2, snap_delta, window=WINDOW_MS):
    base = 0
    for ms, v in adds:
        if ms <= t2:
            base += v
        else:
            break
    if snap_delta == base:
        return dict(cls='zero_lag')
    if snap_delta < base:
        # S1 自身已载入部分窗内加单（S1 渲染滞后 δ1）→ 结构桶, 下一窗自愈
        return dict(cls='over')
    need = snap_delta - base
    cum = 0
    for ms, v in adds:
        if ms <= t2:
            continue
        if ms - t2 > window:
            return dict(cls='lag_gt_window')
        cum += v
        if cum >= need:
            if cum == need:
                return dict(cls='lag', lag_ms=ms - t2)
            return dict(cls='no_exact')   # 前缀跨过目标量（无恰等加单边界）
    return dict(cls='lag_gt_window')
