"""PIT 交集对齐（Plan CX-C1 C1-04，design §5 冻结）。

C1 语义：只保留所有成员都有**有效值**的 `(date, code)`——intersection + reject。
- 有效值 = signal 非 null 且非 NaN；含无效值的行被丢弃并计数（绝不填充）；
- 顺序 = `(date, code)` 升序，且所有成员帧与 index 逐行同序（X 列对齐前提）；
- 审计：每成员 rows / valid_rows / kept_rows / dropped_missing_value /
  dropped_uncovered + 交集行数（spec §17 验收⑤）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import polars as pl

from factorlab.app.composite.resolver import MemberRef

_KEY = ["date", "code"]


class AlignmentError(ValueError):
    """对齐失败（重复键/缺列/交集为空/成员顺序错位）——文案含成员名与计数。"""


@dataclass(frozen=True)
class MemberCoverage:
    """单成员覆盖审计（rows = valid_rows + dropped_missing_value 恒成立）。"""

    position: int
    member: str
    rows: int
    valid_rows: int
    kept_rows: int
    dropped_missing_value: int
    dropped_uncovered: int

    def as_dict(self) -> dict:
        return {"position": self.position, "member": self.member, "rows": self.rows,
                "valid_rows": self.valid_rows, "kept_rows": self.kept_rows,
                "dropped_missing_value": self.dropped_missing_value,
                "dropped_uncovered": self.dropped_uncovered}


@dataclass(frozen=True)
class AlignmentAudit:
    members: tuple[MemberCoverage, ...]
    intersection_rows: int

    def as_dict(self) -> dict:
        return {"intersection_rows": self.intersection_rows,
                "members": [m.as_dict() for m in self.members]}


@dataclass(frozen=True)
class AlignedIndex:
    """交集对齐结果：index=[date, code]（升序）+ 各成员值帧（frozens 列序=refs 序）。"""

    index: pl.DataFrame
    frames: tuple[pl.DataFrame, ...]
    audit: AlignmentAudit


def _valid_signal_mask(frame: pl.DataFrame, ref: MemberRef) -> pl.Expr:
    missing = [c for c in ("date", "code", "signal") if c not in frame.columns]
    if missing:
        raise AlignmentError(
            f"成员 {ref.member!r} 缺必需列 {missing}（实际 {list(frame.columns)}）")
    if frame.schema["date"] != pl.Date or frame.schema["code"] != pl.String:
        raise AlignmentError(
            f"成员 {ref.member!r} date/code dtype 必须为 pl.Date/pl.String，实际 "
            f"{frame.schema['date']}/{frame.schema['code']}")
    dup = frame.select(_KEY).is_duplicated().sum()
    if dup:
        raise AlignmentError(
            f"成员 {ref.member!r} 存在 {dup} 行重复 (date, code)——PIT 键必须唯一，"
            f"绝不静默 dedup")
    dtype = frame.schema["signal"]
    if dtype != pl.Null and not dtype.is_numeric():
        raise AlignmentError(
            f"成员 {ref.member!r} signal 列必须为 numeric，实际 {dtype}")
    mask = pl.col("signal").is_not_null()
    if dtype.is_float():
        mask = mask & pl.col("signal").is_not_nan()
    return mask


def align_members(refs: Sequence[MemberRef]) -> AlignedIndex:
    """按 refs 给定顺序做 `(date, code)` 交集（禁止对 refs 重排）。"""
    if not refs:
        raise AlignmentError("align_members 需要至少一个成员（空 refs）")
    prepared: list[tuple[MemberRef, pl.DataFrame, pl.DataFrame]] = []
    for ref in refs:
        frame = ref.frame
        valid = frame.filter(_valid_signal_mask(frame, ref)).select(["date", "code", "signal"])
        prepared.append((ref, frame, valid))

    common = prepared[0][2].select(_KEY)
    for _, _, valid in prepared[1:]:
        common = common.join(valid.select(_KEY), on=_KEY, how="semi")
    common = common.sort(_KEY)
    if common.height == 0:
        coverage = ", ".join(f"{ref.member}={frame.height}" for ref, frame, _ in prepared)
        raise AlignmentError(
            f"成员交集为空（intersection_rows=0）——各成员覆盖: {coverage}；"
            f"C1 冻结 intersection+reject 不做填充，请检查成员 artifact 日期范围/有效值")

    frames: list[pl.DataFrame] = []
    coverages: list[MemberCoverage] = []
    for ref, frame, valid in prepared:
        aligned = valid.join(common, on=_KEY, how="inner").sort(_KEY)
        if not aligned.select(_KEY).equals(common):
            raise AlignmentError(
                f"成员 {ref.member!r} 对齐后键序与交集不一致（内部不变量破坏）")
        frames.append(aligned)
        coverages.append(MemberCoverage(
            position=ref.position, member=ref.member, rows=frame.height,
            valid_rows=valid.height, kept_rows=aligned.height,
            dropped_missing_value=frame.height - valid.height,
            dropped_uncovered=valid.height - aligned.height))
    audit = AlignmentAudit(members=tuple(coverages), intersection_rows=common.height)
    return AlignedIndex(index=common, frames=tuple(frames), audit=audit)
