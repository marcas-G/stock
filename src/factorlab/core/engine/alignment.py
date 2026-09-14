"""Signal/Label (date, code) 键对齐契约（纯核；从 artifacts.py 下沉，WS3c-2）。

报错文案与原始实现**逐字一致**（被 tests/test_m6_semantic_guards 等锁定）；
裸 frame 助手 `_validate_key_alignment` 供多输出 per-output 帧复用。
"""
from __future__ import annotations

import polars as pl

from factorlab.core.domain.frames import LabelArtifact, SignalArtifact


def _validate_key_alignment(left: pl.DataFrame, right: pl.DataFrame,
                            left_name: str, right_name: str) -> None:
    """(date, code) key 严格对齐（M6-06）：行数、键、**键顺序**三者一致。

    Polars-native equals——不对千万行做 Python set 转换。不一致 → ValueError
    （artifact pair mismatch）。裸 frame 助手——多输出 per-output frame（无
    signal 列，借不了 SignalArtifact）复用同一契约。
    """
    if left.height != right.height:
        raise ValueError(f"{left_name}/{right_name} row count 不一致: {left_name} "
                         f"{left.height} vs {right_name} {right.height}")
    if not left.select(["date", "code"]).equals(right.select(["date", "code"])):
        raise ValueError(f"{left_name}/{right_name} (date, code) key 不一致（含顺序）"
                         f"——artifact pair mismatch")


def validate_signal_label_alignment(signal: SignalArtifact,
                                    labels: LabelArtifact) -> None:
    """Signal/Label (date, code) key 严格对齐（M6-06，语义不变——委托
    _validate_key_alignment，报错文案逐字一致）。"""
    _validate_key_alignment(signal.frame, labels.frame, "Signal", "Label")


