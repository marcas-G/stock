"""R22 Task 2：纯分钟窗口成交引擎——表驱动手算对拍。

断言来源：design.md §2（窗口/口径/分批/参与率/触发/兜底语义）与 plan.md
Task 2 Interfaces（全部手算，不引用实现输出）。"""

import pytest

from factorlab.core.execution.minute_window import (MinuteBar, simulate_window,
                                                    WindowFillResult)
from factorlab.core.execution.spec import MinuteWindowSpec, SliceSpec, TriggerSpec


def bar(i, o, h, l, c, v, a):
    return MinuteBar(minute_index=i, open=o, high=h, low=l, close=c,
                     volume=v, amount=a)


BARS = {
    0: bar(0, 10.0, 10.2, 9.9, 10.1, 1000.0, 10100.0),
    1: bar(1, 10.1, 10.5, 10.0, 10.4, 2000.0, 20800.0),
    2: bar(2, 10.4, 10.6, 10.3, 10.5, 500.0, 5250.0),
}


def test_vwap_basis_participation_and_carry():
    # 目标 1500 股、参与率 0.1：分钟 0 上限 100、分钟 1 上限 200、分钟 2 上限 50
    spec = MinuteWindowSpec(start=0, end=2, price_basis="vwap", participation=0.1)
    r = simulate_window(BARS, side="buy", target_qty=1500, spec=spec,
                        ref_price=10.0, limit_up=None, limit_down=None)
    # 手算：100@vwap(0)=10100/1000=10.1；200@vwap(1)=20800/2000=10.4；
    #       50@vwap(2)=5250/500=10.5；合计 350 成交、1150 未成交（窗口只剩 3 分钟）
    assert r.filled_qty == 350
    assert r.unfilled_qty == 1150
    assert r.avg_price == pytest.approx((100*10.1 + 200*10.4 + 50*10.5)/350)
    assert [f.minute_index for f in r.fills] == [0, 1, 2]
    assert [f.quantity for f in r.fills] == [100, 200, 50]
    assert [f.price for f in r.fills] == pytest.approx([10.1, 10.4, 10.5])
    assert all(f.fell_back is False for f in r.fills)
    assert sum(f.quantity for f in r.fills) + r.unfilled_qty == 1500


def test_limit_trigger_fill_price_is_min_or_better():
    spec = MinuteWindowSpec(start=0, end=2, trigger=TriggerSpec(mode="limit", ref="pre_close", offset_bps=0),
                            participation=1.0)
    # 买：limit=pre_close=10.2；分钟 0 low=9.9<=10.2 → 成交价 min(10.2, open=10.0)=10.0
    r = simulate_window(BARS, side="buy", target_qty=1000, spec=spec,
                        ref_price=10.2, limit_up=None, limit_down=None)
    assert r.fills[0].price == 10.0
    assert r.fills[0].minute_index == 0 and r.fills[0].quantity == 1000
    # 后续分钟继续按 limit 成交（min(limit, open)）：分钟 1 open=10.1 →
    # 但本用例目标已在分钟 0 完成，故只有一笔
    assert len(r.fills) == 1


def test_limit_trigger_not_hit_no_fill():
    spec = MinuteWindowSpec(start=0, end=2, trigger=TriggerSpec(mode="limit", ref="pre_close", offset_bps=-500),
                            participation=1.0)
    # 买：limit=10.2*0.95=9.69，所有 low>9.69 → 不成交
    r = simulate_window(BARS, side="buy", target_qty=1000, spec=spec,
                        ref_price=10.2, limit_up=None, limit_down=None)
    assert r.filled_qty == 0 and r.unfilled_qty == 1000
    assert r.fills == () and r.avg_price is None


def test_limit_trigger_carry_after_first_hit():
    """限价单：逐分钟重判触发条件；命中分钟按 min(limit, open) 成交，未命中
    分钟不成交（真实限价单语义——价格回到 limit 才继续成交）。"""
    spec = MinuteWindowSpec(start=0, end=2, participation=0.1,
                            trigger=TriggerSpec(mode="limit", ref="pre_close",
                                                offset_bps=0))
    r = simulate_window(BARS, side="buy", target_qty=1500, spec=spec,
                        ref_price=10.2, limit_up=None, limit_down=None)
    # 分钟 0 low=9.9<=10.2 → 100@min(10.2,10.0)=10.0；分钟 1 low=10.0<=10.2
    # → 200@min(10.2,10.1)=10.1；分钟 2 low=10.3>10.2 → 不触发
    assert [(f.minute_index, f.quantity, f.price) for f in r.fills] == \
        [(0, 100, 10.0), (1, 200, 10.1)]
    assert r.unfilled_qty == 1200


def test_sell_limit_trigger_and_price():
    spec = MinuteWindowSpec(start=0, end=2, participation=1.0,
                            trigger=TriggerSpec(mode="limit", ref="pre_close",
                                                offset_bps=-100))
    # 卖：limit = pre_close×(1-(-100)/1e4)=10.0×1.01=10.1；分钟 0 high=10.2≥10.1
    # → 成交价 max(10.1, open=10.0)=10.1
    r = simulate_window(BARS, side="sell", target_qty=500, spec=spec,
                        ref_price=10.0, limit_up=None, limit_down=None)
    assert r.fills[0].price == 10.1
    assert r.fills[0].quantity == 500


def test_vwap_offset_no_first_minute_and_uses_prev_cumulative():
    bars = {
        0: bar(0, 10.0, 10.05, 9.90, 10.0, 1000.0, 10000.0),   # cum vwap=10.0
        1: bar(1, 10.0, 10.1, 9.95, 10.05, 1000.0, 10050.0),
        2: bar(2, 10.1, 10.2, 10.05, 10.15, 1000.0, 10150.0),
    }
    spec = MinuteWindowSpec(start=0, end=2, participation=1.0,
                            trigger=TriggerSpec(mode="vwap_offset"))
    r = simulate_window(bars, side="buy", target_qty=100, spec=spec,
                        ref_price=None, limit_up=None, limit_down=None)
    # 分钟 0：无累计 VWAP → 不触发（即使 low 很低）；分钟 1：limit = 10.0
    # → low=9.95<=10.0 → 100@min(10.0, open=10.0)=10.0
    assert [(f.minute_index, f.quantity, f.price) for f in r.fills] == \
        [(1, 100, 10.0)]


def test_vwap_offset_sell_uses_cumulative_minus_offset():
    bars = {
        0: bar(0, 10.0, 10.0, 10.0, 10.0, 1000.0, 10000.0),
        1: bar(1, 10.1, 10.2, 10.0, 10.1, 1000.0, 10100.0),
    }
    spec = MinuteWindowSpec(start=0, end=1, participation=1.0,
                            trigger=TriggerSpec(mode="vwap_offset",
                                                offset_bps=-50))
    # 分钟 1：cum vwap=10.0；卖 limit=10.0×(1-(-50)/1e4)=10.05；high=10.2≥10.05
    # → 成交价 max(10.05, open=10.1)=10.1
    r = simulate_window(bars, side="sell", target_qty=100, spec=spec,
                        ref_price=None, limit_up=None, limit_down=None)
    assert [(f.minute_index, f.price) for f in r.fills] == [(1, 10.1)]


def test_price_basis_open_close_twap_mid():
    b = {0: bar(0, 10.0, 12.0, 8.0, 11.0, 1000.0, 11000.0)}
    for basis, expected in (("open", 10.0), ("close", 11.0),
                            ("twap", 10.25), ("mid", 10.0)):
        spec = MinuteWindowSpec(start=0, end=0, price_basis=basis,
                                participation=1.0)
        r = simulate_window(b, side="buy", target_qty=10, spec=spec,
                            ref_price=10.0, limit_up=None, limit_down=None)
        assert r.fills[0].price == expected, basis


def test_sealed_limit_up_blocks_buy():
    # 一字涨停：open==high==low==limit_up
    bars = {0: bar(0, 11.0, 11.0, 11.0, 11.0, 1000.0, 11000.0)}
    spec = MinuteWindowSpec(start=0, end=0, participation=1.0)
    r = simulate_window(bars, side="buy", target_qty=100, spec=spec,
                        ref_price=10.0, limit_up=11.0, limit_down=10.0)
    assert r.filled_qty == 0
    assert r.unfilled_qty == 100


def test_sealed_limit_down_blocks_sell():
    bars = {0: bar(0, 9.0, 9.0, 9.0, 9.0, 1000.0, 9000.0)}
    spec = MinuteWindowSpec(start=0, end=0, participation=1.0)
    r = simulate_window(bars, side="sell", target_qty=100, spec=spec,
                        ref_price=10.0, limit_up=11.0, limit_down=9.0)
    assert r.filled_qty == 0


def test_partial_board_not_blocked():
    """触板但未封死（high≠low）的分钟允许成交；封死分钟跳过、下一分钟恢复。"""
    bars = {
        0: bar(0, 11.0, 11.0, 11.0, 11.0, 1000.0, 11000.0),   # 一字 → 跳过
        1: bar(1, 10.9, 11.0, 10.5, 10.8, 1000.0, 10800.0),   # 打开 → 可买
    }
    spec = MinuteWindowSpec(start=0, end=1, participation=1.0)
    r = simulate_window(bars, side="buy", target_qty=100, spec=spec,
                        ref_price=10.0, limit_up=11.0, limit_down=10.0)
    assert [(f.minute_index, f.quantity) for f in r.fills] == [(1, 100)]


def test_fallback_close_fills_remainder():
    spec = MinuteWindowSpec(start=0, end=2, participation=0.1, fallback="close")
    r = simulate_window(BARS, side="buy", target_qty=400, spec=spec,
                        ref_price=10.0, limit_up=None, limit_down=None)
    assert r.filled_qty == 400
    assert r.fills[-1].price == 10.5          # 尾盘 close 兜底
    assert r.fills[-1].fell_back is True
    assert r.fills[-1].minute_index == 2
    assert r.unfilled_qty == 0


def test_fallback_close_after_untriggered_limit():
    """限价未触发 + fallback=close：按设计 §2 窗口结束仍兜底。"""
    spec = MinuteWindowSpec(start=0, end=2, participation=1.0, fallback="close",
                            trigger=TriggerSpec(mode="limit", offset_bps=-500))
    r = simulate_window(BARS, side="buy", target_qty=100, spec=spec,
                        ref_price=10.2, limit_up=None, limit_down=None)
    assert r.filled_qty == 100
    assert r.fills[-1].price == 10.5 and r.fills[-1].fell_back is True


def test_fallback_none_and_sealed_close_not_filled():
    spec = MinuteWindowSpec(start=0, end=2, participation=0.1, fallback="none")
    r = simulate_window(BARS, side="buy", target_qty=400, spec=spec,
                        ref_price=10.0, limit_up=None, limit_down=None)
    assert r.unfilled_qty == 50 and r.filled_qty == 350
    # 一字涨停日 fallback=close 也不得成交（真实约束：封板买不进）
    sealed = {0: bar(0, 11.0, 11.0, 11.0, 11.0, 1000.0, 11000.0)}
    spec2 = MinuteWindowSpec(start=0, end=0, participation=1.0, fallback="close")
    r2 = simulate_window(sealed, side="buy", target_qty=100, spec=spec2,
                         ref_price=10.0, limit_up=11.0, limit_down=10.0)
    assert r2.filled_qty == 0


def test_slices_weight_split_and_tail_to_last():
    spec = MinuteWindowSpec(start=0, end=2, participation=1.0,
                            slices=[SliceSpec(start=0, end=0, weight=0.3),
                                    SliceSpec(start=1, end=2, weight=0.7)])
    r = simulate_window(BARS, side="buy", target_qty=101, spec=spec,
                        ref_price=10.0, limit_up=None, limit_down=None)
    # 30 → 尾差 71；分钟 0 量 1000*1.0 足够
    assert sum(f.quantity for f in r.fills) == 101
    assert [(f.minute_index, f.quantity) for f in r.fills] == [(0, 30), (1, 71)]


def test_slices_three_slices_tail_to_last():
    spec = MinuteWindowSpec(start=0, end=2, participation=1.0,
                            slices=[SliceSpec(start=0, end=0, weight=0.25),
                                    SliceSpec(start=1, end=1, weight=0.25),
                                    SliceSpec(start=2, end=2, weight=0.5)])
    r = simulate_window(BARS, side="buy", target_qty=101, spec=spec,
                        ref_price=10.0, limit_up=None, limit_down=None)
    assert [(f.minute_index, f.quantity) for f in r.fills] == \
        [(0, 25), (1, 25), (2, 51)]


def test_missing_and_null_bars_skipped():
    bars = {
        0: bar(0, 10.0, 10.2, 9.9, 10.1, 1000.0, 10100.0),
        # 1 缺行
        2: bar(2, None, None, None, None, 500.0, 0.0),     # 无价 → 跳过
        3: bar(3, 10.4, 10.6, 10.3, 10.5, 1000.0, 10500.0),
    }
    spec = MinuteWindowSpec(start=0, end=3, participation=0.1)
    r = simulate_window(bars, side="buy", target_qty=150, spec=spec,
                        ref_price=10.0, limit_up=None, limit_down=None)
    # cap(0)=100、minute 2 无价跳过、cap(3)=100 → 100@10.1 + 50@10.5
    assert [(f.minute_index, f.quantity, f.price) for f in r.fills] == \
        [(0, 100, 10.1), (3, 50, 10.5)]
    assert r.filled_qty == 150


def test_participation_floor_small_volume_skipped():
    bars = {
        0: bar(0, 10.0, 10.0, 10.0, 10.0, 5.0, 50.0),      # floor(0.1*5)=0 → 跳过
        1: bar(1, 10.0, 10.0, 10.0, 10.0, 100.0, 1000.0),  # cap 10
    }
    spec = MinuteWindowSpec(start=0, end=1, participation=0.1)
    r = simulate_window(bars, side="sell", target_qty=10, spec=spec,
                        ref_price=10.0, limit_up=None, limit_down=None)
    assert [(f.minute_index, f.quantity) for f in r.fills] == [(1, 10)]


def test_target_zero_and_invalid_guards():
    spec = MinuteWindowSpec(start=0, end=2, participation=0.1)
    with pytest.raises(ValueError, match="target_qty"):
        simulate_window(BARS, side="buy", target_qty=0, spec=spec,
                        ref_price=10.0, limit_up=None, limit_down=None)
    with pytest.raises(ValueError, match="side"):
        simulate_window(BARS, side="hold", target_qty=100, spec=spec,
                        ref_price=10.0, limit_up=None, limit_down=None)
    # trigger=None → ref_price 不参与（允许 None）
    r = simulate_window(BARS, side="buy", target_qty=100, spec=spec,
                        ref_price=None, limit_up=None, limit_down=None)
    assert r.filled_qty == 100
    # limit 触发必须提供 ref_price（fail fast，不发明基准）
    limit_spec = MinuteWindowSpec(start=0, end=2, participation=0.1,
                                  trigger=TriggerSpec(mode="limit"))
    with pytest.raises(ValueError, match="ref_price"):
        simulate_window(BARS, side="buy", target_qty=100, spec=limit_spec,
                        ref_price=None, limit_up=None, limit_down=None)


def test_result_type_shape():
    spec = MinuteWindowSpec(start=0, end=2, participation=0.1)
    r = simulate_window(BARS, side="buy", target_qty=100, spec=spec,
                        ref_price=10.0, limit_up=None, limit_down=None)
    assert isinstance(r, WindowFillResult)
    assert isinstance(r.fills, tuple)
    assert r.filled_qty == 100 and r.unfilled_qty == 0
