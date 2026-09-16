"""R22 Task 1：分钟执行配置域——NEXT_WINDOW + 窗口/切片/触发 spec 校验。

断言来源：knowledge/design/workspace/2026-09-15-minute-execution/design.md §2/§3 与 plan.md
Task 1（非法窗口/切片权重和/参与率越界/NEXT_WINDOW 缺窗口全部 fail fast）。
"""

import pytest
from pydantic import ValidationError

from factorlab.core.domain.timing import ExecutionTiming
from factorlab.core.execution.spec import (ExecutionSpec, MinuteWindowSpec,
                                           SliceSpec, TriggerSpec)


def _ok_window(**kw):
    base = dict(start=0, end=30)
    base.update(kw)
    return MinuteWindowSpec(**base)


def test_next_window_enum_exists():
    assert ExecutionTiming.NEXT_WINDOW.value == "next_window"


def test_next_window_requires_window():
    with pytest.raises(ValidationError, match="minute_window"):
        ExecutionSpec(execution_timing=ExecutionTiming.NEXT_WINDOW)


def test_next_open_forbids_window():
    with pytest.raises(ValidationError, match="minute_window"):
        ExecutionSpec(minute_window=_ok_window())


def test_next_window_accepts_window_and_defaults():
    spec = ExecutionSpec(execution_timing=ExecutionTiming.NEXT_WINDOW,
                         minute_window=_ok_window())
    assert spec.execution_timing is ExecutionTiming.NEXT_WINDOW
    assert spec.minute_window.start == 0 and spec.minute_window.end == 30
    # 默认 ExecutionTiming = NEXT_OPEN（向后兼容）
    assert ExecutionSpec().execution_timing is ExecutionTiming.NEXT_OPEN
    assert ExecutionSpec().minute_window is None


def test_window_bounds():
    with pytest.raises(ValidationError, match="end"):
        MinuteWindowSpec(start=10, end=9)
    with pytest.raises(ValidationError, match="239"):
        MinuteWindowSpec(start=0, end=240)
    with pytest.raises(ValidationError, match="start"):
        MinuteWindowSpec(start=-1, end=30)


def test_participation_range():
    with pytest.raises(ValidationError, match="participation"):
        _ok_window(participation=0.0)
    with pytest.raises(ValidationError, match="participation"):
        _ok_window(participation=1.01)
    with pytest.raises(ValidationError, match="participation"):
        _ok_window(participation=True)
    with pytest.raises(ValidationError, match="participation"):
        _ok_window(participation=float("nan"))
    assert _ok_window(participation=1.0).participation == 1.0


def test_slices_weight_sum_and_overlap():
    good = _ok_window(slices=[SliceSpec(start=0, end=9, weight=0.3),
                              SliceSpec(start=10, end=30, weight=0.7)])
    assert good.slices[1].weight == 0.7
    with pytest.raises(ValidationError, match="权重"):
        _ok_window(slices=[SliceSpec(start=0, end=9, weight=0.5),
                           SliceSpec(start=10, end=30, weight=0.4)])
    with pytest.raises(ValidationError, match="重叠"):
        _ok_window(slices=[SliceSpec(start=0, end=20, weight=0.5),
                           SliceSpec(start=10, end=30, weight=0.5)])


def test_slices_out_of_window_rejected():
    with pytest.raises(ValidationError, match="窗口"):
        _ok_window(start=5, end=30,
                   slices=[SliceSpec(start=0, end=4, weight=0.5),
                           SliceSpec(start=5, end=30, weight=0.5)])


def test_slice_weight_positive_and_order():
    with pytest.raises(ValidationError, match="weight"):
        SliceSpec(start=0, end=9, weight=0.0)
    with pytest.raises(ValidationError, match="start"):
        _ok_window(slices=[SliceSpec(start=10, end=30, weight=0.5),
                           SliceSpec(start=0, end=9, weight=0.5)])


def test_trigger_validation():
    t = TriggerSpec(mode="limit", ref="pre_close", offset_bps=-50)
    assert t.offset_bps == -50
    with pytest.raises(ValidationError):
        TriggerSpec(mode="magic")
    with pytest.raises(ValidationError, match="offset_bps"):
        TriggerSpec(mode="limit", offset_bps=float("inf"))
    assert TriggerSpec(mode="vwap_offset").ref == "pre_close"


def test_price_basis_and_fallback_literals():
    assert _ok_window(price_basis="mid").price_basis == "mid"
    assert _ok_window(fallback="close").fallback == "close"
    with pytest.raises(ValidationError):
        _ok_window(price_basis="magic")
    with pytest.raises(ValidationError):
        _ok_window(fallback="magic")


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        _ok_window(unknown_field=1)
    # NEXT_CLOSE 禁止携带 minute_window（拒绝条件与 NEXT_OPEN 相同）
    with pytest.raises(ValidationError, match="minute_window"):
        ExecutionSpec(execution_timing=ExecutionTiming.NEXT_CLOSE,
                      minute_window=_ok_window())
