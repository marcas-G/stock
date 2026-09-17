from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from factorlab.core.engine.reserved import is_future_column, is_internal_name


class _StrictModel(BaseModel):
    """spec 严格解析（R05-I2）：未知字段 = 加载期明确报错，绝不静默忽略。

    R05 实测 `bogus_field: 123` 过 lint（无任何提示）——用户写错的字段名/照抄
    未实现机制的指引都无声失败。所有 spec 模型（含嵌套）统一 strict；冒烟库
    不变量：全库 spec lint 必须仍通过（无 spec 依赖静默容忍）。
    """

    model_config = ConfigDict(extra="forbid")


NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]{0,63}$"
PROCESS_PATTERN = r"^[a-z_][a-z0-9_]*(\(.*\))?$"
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


class UniverseSpec(_StrictModel):
    ref: str | None = None          # 命名引用或文件路径（查 universes_dir）
    codes: list[str] | None = None
    rules: dict[str, Any] | None = None
    # M4（G2）公式化股票池：布尔条件逐 (code, 交易日) 定池（design §4）。
    # 池公式语法 v1：单个布尔表达式（裸表达式或 signal = 表达式赋值形式，
    # 赋值名不参与语义——引擎统一归一）。四选一互斥；需要"名单 ∧ 条件"时名单
    # 经数据面成分标志属性（per-code 0/1）进公式，不设第二个 spec 来源。
    formula: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_string(cls, value: Any) -> Any:
        if isinstance(value, str):
            if not value.strip():
                raise ValueError("universe 引用名不能为空")
            return {"ref": value}
        return value

    @model_validator(mode="after")
    def _exactly_one_universe(self) -> "UniverseSpec":
        chosen = sum(x is not None
                     for x in (self.ref, self.codes, self.rules, self.formula))
        if chosen != 1:
            raise ValueError(
                "universe 必须且只能提供 ref / codes / rules / formula 之一")
        return self


class DateRange(_StrictModel):
    start: str | None = None
    end: str | None = None

    @model_validator(mode="after")
    def _valid_dates(self) -> "DateRange":
        for field in ("start", "end"):
            value = getattr(self, field)
            if value is not None and not re.match(DATE_PATTERN, value):
                raise ValueError(f"{field} 必须为 YYYY-MM-DD 格式")
        return self


class OperatorMacro(_StrictModel):
    params: list[str] = Field(default_factory=list)
    formula: str


class SubFactorSpec(_StrictModel):
    name: str = Field(pattern=NAME_PATTERN)
    formula: str
    process: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _valid_process_names(self) -> "SubFactorSpec":
        for item in self.process:
            if not re.match(PROCESS_PATTERN, item):
                raise ValueError(f"非法 process 项: {item}")
        return self


class CombineSpec(_StrictModel):
    method: Literal["ic_weight", "equal_weight", "weight_sum"]
    weights: list[float] | None = None

    @model_validator(mode="after")
    def _valid_weights(self) -> "CombineSpec":
        if self.method == "weight_sum" and not self.weights:
            raise ValueError("weight_sum 必须提供非空 weights")
        return self


class FactorSpec(_StrictModel):
    name: str = Field(pattern=NAME_PATTERN)
    category: Literal["ohlcv_core", "ohlcv_retail", "valuation", "custom"]
    direction: Literal[1, -1]
    description: str = ""
    universe: UniverseSpec
    date: DateRange = Field(default_factory=DateRange)
    target: Literal["forward_return_5d", "forward_return_20d"] = "forward_return_5d"
    # D9（R30 Task 13）：评估频率——daily（默认：逐日截面/每日调仓/1 日 forward）
    # / weekly（旧口径可选对照，零变更：ISO 周对齐 + spec.target）。
    evaluation_frequency: Literal["daily", "weekly"] = "daily"
    process: list[str] = Field(default_factory=list)
    operators: dict[str, OperatorMacro] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)  # 顶层参数（formula 内 ${name} 引用）
    # R05-I2：op_meta（Plan 2 预留：黑盒/外部函数分区声明）当前未实现——非空
    # 显式拒绝。此前 pydantic 静默吞掉该字段，而引擎报错文案又引导用户来补
    # （指引无效且无声：写对了机制也不存在）。
    op_meta: dict[str, Any] | None = None
    formula: str | None = None
    factors: list[SubFactorSpec] | None = None
    combine: CombineSpec | None = None
    # 复权视图口径：pit_qfq 预留（需 asof 研究日，审计场景 M4b 消费）
    adjustment: Literal["raw", "qfq", "hfq", "pit_qfq"] = "qfq"
    # 模板接口（2026-09-08）：daily（缺省，主链逐字节不变）| bars_1m（分钟模板：
    # im_*/day_* 算子 → run_factor_minute 折日输出——artifact 契约零放宽，
    # frequency 恒 "1d"/EOD/raw；分钟性只存在本字段与运行路径）。tick 预留。
    interface: Literal["daily", "bars_1m"] = "daily"
    # 调仓成本（R10 / #15）：每单位**单边换手**的买卖总成本（费率语义，例 0.0007 ≈
    # 0.1% 印花税 + 双边佣金 0.005%×2）；0.0（缺省）= 零成本，与历史结果逐值一致。
    # 由 evaluate 透传到 layered_backtest，结果里回显 cost_rate 与 turnover（可审计）。
    cost_rate: float = Field(default=0.0, ge=0.0, lt=1.0)
    # E1（R30 fix 波）：市值加权 decile——`equal_weight` 缺省零回归；`market_cap`
    # 时评估链按 `total_mv`（E1a）加权（组收益 Σ(mv×fwd)/Σ(mv)，PIT），结果披露
    # `weighting={mode, mv_col}`。E1b（circ_mv）kernel/bridge 已支持（`mv_col`
    # 参数，R07-DATA-I4 已完成），spec 入口当前只接 total_mv（interface E1 节）。
    weighting: Literal["equal_weight", "market_cap"] = "equal_weight"
    # M2（G1）多信号输出：None → 下游按 ["signal"] 处理（缺省完全兼容旧 spec）。
    # 面板结构列 / artifact 落盘文件名冲突（date/code/close/panel/labels/summary）
    # 与内部/未来保留名一样不可作输出名（design doc §3.1（c）+ 文件命名安全）。
    outputs: list[str] | None = None
    _OUTPUT_COLLISION_NAMES = frozenset(
        {"date", "code", "close", "panel", "labels", "summary"})

    @property
    def weighting_mv_col(self) -> str | None:
        """E1 市值列单点：market_cap → `total_mv`（E1a）；等权 → None（按需供给）。"""
        return "total_mv" if self.weighting == "market_cap" else None

    @model_validator(mode="after")
    def _validate_weighting_interface(self) -> "FactorSpec":
        if self.weighting == "market_cap" and self.interface == "bars_1m":
            raise ValueError(
                "weighting=market_cap 暂不支持 interface: bars_1m"
                "（分钟链不供给 total_mv）——请改用 interface: daily，"
                "或保持 weighting: equal_weight")
        return self

    @model_validator(mode="after")
    def _reject_op_meta(self) -> "FactorSpec":
        if self.op_meta:
            raise ValueError(
                "op_meta 暂未支持（Plan 2）——未知算子请改用公式内 def（本因子"
                "专用）或注册插件算子（factorlab op add）；写了 op_meta 也不会"
                "被采纳，故在此明确拒绝而非静默忽略")
        return self

    @model_validator(mode="after")
    def _validate_outputs(self) -> "FactorSpec":
        if self.outputs is None:
            return self
        if not self.outputs:
            raise ValueError("outputs 不能为空（缺省 = [signal]，或列出声明输出名）")
        for name in self.outputs:
            if not re.match(NAME_PATTERN, name):
                raise ValueError(
                    f"outputs 名字不合法: {name!r}（须匹配 {NAME_PATTERN}）")
            if name in self._OUTPUT_COLLISION_NAMES:
                raise ValueError(
                    f"outputs 保留名: {name!r}（date/code/close/panel/labels/summary"
                    f" 与面板结构列/落盘文件冲突，不可作输出名）")
            if is_internal_name(name):
                raise ValueError(
                    f"outputs 保留名: {name!r}（__factorlab_* / in_universe 为平台"
                    f"内部保留，公式模板不可输出）")
            if is_future_column(name):
                raise ValueError(
                    f"outputs 保留名: {name!r}（forward_*/future_*/target/label"
                    f" 为数据侧未来列命名纪律，公式输出不可用）")
        seen: dict[str, int] = {}
        for i, name in enumerate(self.outputs):
            if name in seen:
                raise ValueError(
                    f"outputs 重复: {name!r}（全局唯一——第 {seen[name]} 与第 {i + 1} 位置冲突）")
            seen[name] = i + 1
        return self

    @model_validator(mode="after")
    def _validate_script(self) -> "FactorSpec":
        if (self.formula is None) == (self.factors is None):
            raise ValueError("formula 与 factors 必须二选一")
        if self.factors is not None and self.combine is None:
            raise ValueError("使用 factors 时必须提供 combine")
        if self.factors is not None and self.combine is not None:
            if self.combine.method == "weight_sum":
                if self.combine.weights is None or len(self.combine.weights) != len(self.factors):
                    raise ValueError("weight_sum 的 weights 数量必须等于 factors 数量")
        for item in self.process:
            if not re.match(PROCESS_PATTERN, item):
                raise ValueError(f"非法 process 项: {item}")
        return self


def load_spec(path: str | Path) -> FactorSpec:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return FactorSpec.model_validate(data)
