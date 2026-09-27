# 分钟级执行 实施计划（M8 扩展）

> **状态（R22）**：本计划 7 个任务均已实施并验收。原任务复选框已依据
> [R22 完成证据](../../../../governance/evidence/verification/R22/minute-execution/SUMMARY.md)
> 补记；分钟 NAV、量能触发与盘中临停规则不属于本计划 V1。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** M8 执行层支持「日频信号 + 次日分钟窗口成交」：窗口/价格口径/分批/参与率/价格触发/兜底全部可配；默认 `NEXT_OPEN` 行为逐值不变。

**Architecture:** 新增纯计算模块 `core/execution/minute_window.py`（窗口切片、价格口径、累计 VWAP、触发器、参与率顺延、封板/缺行判定）；`ExecutionSpec` 增分钟窗口配置；`run_backtest` 增 `NEXT_WINDOW` 分支与 `WINDOW_END_BASED` marks；数据复用 `load_bars_1m_codes`（仅 CH）；持久化 schema 向后兼容扩展。

**Tech Stack:** Python 3.13 / polars / pydantic v2 / pytest；平台五层架构（core 纯净、I/O 在 adapters）。

**Spec:** `knowledge/design/workspace/2026-09-15-minute-execution/design.md`（配置面 / 语义 / 接口 / 验收）

## Global Constraints（来自 spec 与仓库纪律）

- **core 纯净**：`factorlab.core.*` 禁 IO/三方数据库 import（`platform/tests/test_architecture.py` 静态+隔离子进程双门）。
- **`NEXT_OPEN` 逐值不变**：现有 M8 全量测试与抽样回测不得有任何行为差异；`NEXT_CLOSE` 的 7 处显式拒绝保持不动。
- **TDD**：先写失败测试再实现；测试须能识别存根（硬编码必败）。
- **分钟数据契约**（`adapters/intraday.py` docstring，禁止自造）：`bars_1m` 每 `(code, 交易日)` 240 行网格；`minute_index` 0=09:25 开盘集合竞价、239=15:00 收盘集合竞价；`session_type` 0/1/2；OHLC raw、`amount=元`、`volume=股`；**仅 CH 后端**。
- **门与基线**：`make gates` 全绿；平台全量 pytest ≥ 2570 passed / 13 skipped 不回退。
- **提交纪律**：按路径精确 `git add`，只动本 Task 列出的文件；工作区存在 R21 并发改动，不得扫入。
- **内存纪律**：16GB 无页面文件；分钟数据按决策日分块读取，禁止一次性全历史拉取。
- **价格口径**：信号为 qfq、执行/NAV 用 raw（与现有 M8 一致）；CA Gate/T+1/成本模型语义不动。

---

## 文件结构（本计划落点）

| 动作 | 路径 | 职责 |
|---|---|---|
| 修改 | `platform/src/factorlab/core/domain/timing.py` | `ExecutionTiming` 增 `NEXT_WINDOW`（`NEXT_CLOSE` 保留） |
| 修改 | `platform/src/factorlab/core/execution/spec.py` | `MinuteWindowSpec/SliceSpec/TriggerSpec` + `ExecutionSpec.execution_timing/minute_window` + 校验 |
| 新建 | `platform/src/factorlab/core/execution/minute_window.py` | 纯窗口引擎：切片/价格口径/累计 VWAP/触发/参与率顺延/封板缺行 |
| 新建 | `platform/src/factorlab/adapters/read/minute_window.py` | `load_execution_window`：包装 `load_bars_1m_codes` + 网格校验 + duckdb fail fast |
| 修改 | `platform/src/factorlab/app/backtest/fills.py` | 新增 `realize_window_fills`（既有 `FillBatch` 契约 + 未成交明细） |
| 修改 | `platform/src/factorlab/app/backtest/orders.py` | `NEXT_WINDOW` 规划参考价（窗口首分钟开盘价；fail fast） |
| 修改 | `platform/src/factorlab/app/backtest/backtest.py` | `run_backtest` NEXT_WINDOW 分支 + `MarksPolicy.WINDOW_END_BASED` |
| 修改 | `platform/src/factorlab/adapters/execution_store.py` | schema 扩展（窗口元数据 + 窗口成交明细），向后兼容加载 |
| 测试 | `platform/tests/test_window_spec.py` | 配置域校验 |
| 测试 | `platform/tests/test_minute_window.py` | 纯窗口引擎（表驱动手算） |
| 测试 | `platform/tests/test_read_minute_window.py` | 分钟读适配 |
| 测试 | `platform/tests/test_window_fills.py` | 窗口成交（部分成交/顺延/成本/现金） |
| 测试 | `platform/tests/test_backtest_window_runtime.py` | NEXT_WINDOW 运行时 + NEXT_OPEN 回归 |
| 测试 | `platform/tests/test_execution_store_window.py` | 持久化 round-trip 与版本兼容 |
| 测试 | `platform/tests/test_minute_execution_e2e.py` | CH 端到端（integration） |

---

### Task 1: 计时与配置域（`NEXT_WINDOW` + 窗口 spec + 校验）

**Files:**
- Modify: `platform/src/factorlab/core/domain/timing.py:32-36`
- Modify: `platform/src/factorlab/core/execution/spec.py`
- Test: `platform/tests/test_window_spec.py`

**Interfaces:**
- Produces:
  - `ExecutionTiming.NEXT_WINDOW = "next_window"`
  - `SliceSpec(start:int, end:int, weight:float)`（frozen, extra=forbid）
  - `TriggerSpec(mode:Literal["limit","vwap_offset"], ref:Literal["pre_close","window_open","window_vwap"]="pre_close", offset_bps:float=0.0)`
  - `MinuteWindowSpec(start:int, end:int, price_basis:Literal["vwap","open","close","twap","mid"]="vwap", slices:list[SliceSpec]|None=None, participation:float=0.10, trigger:TriggerSpec|None=None, fallback:Literal["none","close"]="none")`
  - `ExecutionSpec.execution_timing: ExecutionTiming = NEXT_OPEN`；`ExecutionSpec.minute_window: MinuteWindowSpec | None = None`
- 校验规则（全部 fail fast，文案含字段名与合法域）：
  - `NEXT_WINDOW` 必带 `minute_window`；`NEXT_OPEN/NEXT_CLOSE` 禁止带；
  - `0 <= start <= end <= 239`；
  - `0 < participation <= 1`（bool 拒绝、finite）；
  - `slices` 非空时：按 start 升序、互不重叠、落在 [start,end] 内、权重和 = 1（容差 1e-9）、每个 weight > 0；
  - `offset_bps` finite（负=买更低价/卖更高价，语义在 Task 2 固定）。

- [x] **Step 1: 写失败测试**

```python
# platform/tests/test_window_spec.py
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


def test_window_bounds():
    with pytest.raises(ValidationError, match="end"):
        MinuteWindowSpec(start=10, end=9)
    with pytest.raises(ValidationError, match="239"):
        MinuteWindowSpec(start=0, end=240)


def test_participation_range():
    with pytest.raises(ValidationError, match="participation"):
        _ok_window(participation=0.0)
    with pytest.raises(ValidationError, match="participation"):
        _ok_window(participation=1.01)


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


def test_trigger_validation():
    t = TriggerSpec(mode="limit", ref="pre_close", offset_bps=-50)
    assert t.offset_bps == -50
    with pytest.raises(ValidationError):
        TriggerSpec(mode="magic")
```

- [x] **Step 2: 跑测试确认失败**

Run: `cd platform && .venv/bin/python -m pytest tests/test_window_spec.py -q`
Expected: FAIL（`NEXT_WINDOW` 不存在 / spec 模型不存在）

- [x] **Step 3: 实现**

`timing.py` 增 `NEXT_WINDOW = "next_window"`；`spec.py` 按 Interfaces 增加三个模型与 `ExecutionSpec` 字段/校验（pydantic `model_validator(mode="after")` 做跨字段校验；`extra="forbid"`、`frozen=True` 沿用现有风格）。

- [x] **Step 4: 跑测试确认通过**

Run: `cd platform && .venv/bin/python -m pytest tests/test_window_spec.py -q`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add platform/src/factorlab/core/domain/timing.py platform/src/factorlab/core/execution/spec.py platform/tests/test_window_spec.py
git commit -m "feat(platform): 分钟执行配置域（NEXT_WINDOW + 窗口/切片/触发 spec）"
```

---

### Task 2: 纯分钟窗口引擎（核心）

**Files:**
- Create: `platform/src/factorlab/core/execution/minute_window.py`
- Test: `platform/tests/test_minute_window.py`

**Interfaces:**
- Produces:
  - `MinuteBar(minute_index:int, open:float|None, high:float|None, low:float|None, close:float|None, volume:float, amount:float)`（frozen dataclass）
  - `MinuteFill(minute_index:int, quantity:int, price:float)`
  - `WindowFillResult(filled_qty:int, avg_price:float|None, fills:tuple[MinuteFill,...], unfilled_qty:int)`
  - `simulate_window(bars: dict[int, MinuteBar], *, side: Literal["buy","sell"], target_qty:int, spec: MinuteWindowSpec, ref_price: float|None, limit_up: float|None, limit_down: float|None) -> WindowFillResult`
- 语义（spec §2）：
  - 切片循环（无 `slices` 视为单切片 [start,end,1.0]）；每切片目标量 = 权重×总量，尾差归最后一片；
  - `trigger=None`（必成交）：从切片起逐分钟成交；成交价按 `price_basis`（vwap = 该切片**实际成交分钟**的 Σamount/Σvolume；open/close/twap/mid 同理）；
  - `trigger.mode="limit"`：`limit` 由 `ref`（pre_close/window_open/window_vwap）与 `offset_bps` 及 side 推出；`BUY: high>=low 且 low<=limit` 命中，成交价 `min(limit, open)`；`SELL: high>=limit`，成交价 `max(limit, open)`；命中后其余分钟继续按 limit 成交；
  - `trigger.mode="vwap_offset"`：`ref` 忽略，`limit` = 截至上一分钟的**累计窗口 VWAP** × (1 ± offset)；第一分钟无累计 → 不触发；
  - 参与率：单分钟可成交上限 `floor(participation × bar.volume)`（>=1 股才可成交；不足按 0 跳过顺延）；
  - 封板：`limit_up` 且 `open==high==low==limit_up` → BUY 跳过；`limit_down` 同理 SELL；部分封板（high≠low）不拦；
  - 缺行分钟：读取处已过滤，缺失即跳过；
  - `fallback="close"`：窗口结束仍有剩余 → 取最后**已成交或窗口内最后一根有价 bar**的 close 一次性补足（不参与率约束，标注 fallback 成交）；`fallback="none"` → `unfilled_qty` 如实返回；
  - 全程不读未来：仅用当前及更早分钟。

- [x] **Step 1: 写失败测试（表驱动手算）**

```python
# platform/tests/test_minute_window.py
import pytest
from factorlab.core.execution.minute_window import (MinuteBar, simulate_window,
                                                    WindowFillResult)
from factorlab.core.execution.spec import MinuteWindowSpec, SliceSpec, TriggerSpec


def bar(i, o, h, l, c, v, a):
    return MinuteBar(i, o, h, l, c, v, a)


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


def test_limit_trigger_fill_price_is_min_or_better():
    spec = MinuteWindowSpec(start=0, end=2, trigger=TriggerSpec(mode="limit", ref="pre_close", offset_bps=0),
                            participation=1.0)
    # 买：limit=pre_close=10.2；分钟 0 low=9.9<=10.2 → 成交价 min(10.2, open=10.0)=10.0
    r = simulate_window(BARS, side="buy", target_qty=1000, spec=spec,
                        ref_price=10.2, limit_up=None, limit_down=None)
    assert r.fills[0].price == 10.0


def test_limit_trigger_not_hit_no_fill():
    spec = MinuteWindowSpec(start=0, end=2, trigger=TriggerSpec(mode="limit", ref="pre_close", offset_bps=-500),
                            participation=1.0)
    # 买：limit=10.2*0.95=9.69，所有 low>9.69 → 不成交
    r = simulate_window(BARS, side="buy", target_qty=1000, spec=spec,
                        ref_price=10.2, limit_up=None, limit_down=None)
    assert r.filled_qty == 0 and r.unfilled_qty == 1000


def test_sealed_limit_up_blocks_buy():
    # 一字涨停：open==high==low==limit_up
    bars = {0: bar(0, 11.0, 11.0, 11.0, 11.0, 1000.0, 11000.0)}
    spec = MinuteWindowSpec(start=0, end=0, participation=1.0)
    r = simulate_window(bars, side="buy", target_qty=100, spec=spec,
                        ref_price=10.0, limit_up=11.0, limit_down=10.0)
    assert r.filled_qty == 0


def test_fallback_close_fills_remainder():
    spec = MinuteWindowSpec(start=0, end=2, participation=0.1, fallback="close")
    r = simulate_window(BARS, side="buy", target_qty=400, spec=spec,
                        ref_price=10.0, limit_up=None, limit_down=None)
    assert r.filled_qty == 400
    assert r.fills[-1].price == 10.5          # 尾盘 close 兜底


def test_slices_weight_split_and_tail_to_last():
    spec = MinuteWindowSpec(start=0, end=2, participation=1.0,
                            slices=[SliceSpec(start=0, end=0, weight=0.3),
                                    SliceSpec(start=1, end=2, weight=0.7)])
    r = simulate_window(BARS, side="buy", target_qty=101, spec=spec,
                        ref_price=10.0, limit_up=None, limit_down=None)
    # 30 → 尾差 71；分钟 0 量 1000*1.0 足够
    assert sum(f.quantity for f in r.fills) == 101
```

- [x] **Step 2: 跑测试确认失败**

Run: `cd platform && .venv/bin/python -m pytest tests/test_minute_window.py -q`
Expected: FAIL（模块不存在）

- [x] **Step 3: 实现**（纯函数、无 IO；金额单位：`amount=元`、`volume=股` → vwap = amount/volume）

- [x] **Step 4: 跑测试确认通过**

Run: `cd platform && .venv/bin/python -m pytest tests/test_minute_window.py -q`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add platform/src/factorlab/core/execution/minute_window.py platform/tests/test_minute_window.py
git commit -m "feat(platform): 纯分钟窗口成交引擎（VWAP/触发/参与率/封板/兜底）"
```

---

### Task 3: 分钟读取适配（`load_execution_window`）

**Files:**
- Create: `platform/src/factorlab/adapters/read/minute_window.py`
- Test: `platform/tests/test_read_minute_window.py`

**Interfaces:**
- Consumes: `adapters/intraday.py:202 load_bars_1m_codes(rd, codes, date_start=, date_end=, cols=)`
- Produces: `load_execution_window(rd, codes: list[str], day: str, start: int, end: int) -> pl.DataFrame`
  - 列：`code, minute_index, open, high, low, close, volume, amount, session_type`；
  - 只取 `start <= minute_index <= end`；`code` 归一 6 位（沿用批读契约）；
  - 校验：`minute_index` 重复即 ValueError；`open/high/low/close` 出现 null 的分钟直接保留（由 Task 2 跳过），但 `volume<0`/`amount<0` 即 ValueError；
  - `rd.backend == "duckdb"` → `NotImplementedError("分钟数据仅 CH 后端提供")`（与 `adapters/intraday.py` 既有腿一致，fail fast）。

- [x] **Step 1: 写失败测试**

```python
# platform/tests/test_read_minute_window.py
import polars as pl
import pytest

from factorlab.adapters.read.minute_window import load_execution_window


class FakeRd:
    def __init__(self, backend, frame=None):
        self.backend = backend
        self._frame = frame
        self.calls = []

    def query_df(self, sql, params=None):
        self.calls.append((sql, params))
        return self._frame


def _minute_frame():
    return pl.DataFrame({
        "code": ["000001", "000001", "000001"],
        "minute_index": [0, 1, 5],
        "open": [10.0, 10.1, 10.2], "high": [10.2, 10.3, 10.4],
        "low": [9.9, 10.0, 10.1], "close": [10.1, 10.2, 10.3],
        "volume": [1.0, 2.0, 3.0], "amount": [10.1, 20.4, 30.9],
        "session_type": [0, 1, 1],
    })


def test_duckdb_fail_fast():
    with pytest.raises(NotImplementedError, match="CH"):
        load_execution_window(FakeRd("duckdb"), ["000001"], "2024-01-02", 0, 10)


def test_window_filter_and_schema():
    rd = FakeRd("ch", _minute_frame())
    out = load_execution_window(rd, ["000001"], "2024-01-02", 0, 1)
    assert out["minute_index"].to_list() == [0, 1]          # 5 被窗口剔除
    assert out.columns == ["code", "minute_index", "open", "high", "low",
                           "close", "volume", "amount", "session_type"]


def test_negative_volume_rejected():
    bad = _minute_frame().with_columns(pl.when(pl.col("minute_index") == 1)
                                       .then(-1.0).otherwise(pl.col("volume")).alias("volume"))
    with pytest.raises(ValueError, match="volume"):
        load_execution_window(FakeRd("ch", bad), ["000001"], "2024-01-02", 0, 10)
```

- [x] **Step 2-4: 红 → 实现 → 绿**

Run: `cd platform && .venv/bin/python -m pytest tests/test_read_minute_window.py -q`

- [x] **Step 5: 提交**

```bash
git add platform/src/factorlab/adapters/read/minute_window.py platform/tests/test_read_minute_window.py
git commit -m "feat(platform): 分钟执行窗口读取适配（CH only + 契约校验）"
```

---

### Task 4: 窗口成交（`realize_window_fills`）

**Files:**
- Modify: `platform/src/factorlab/app/backtest/fills.py`（新增函数；`NEXT_OPEN` 函数不动）
- Test: `platform/tests/test_window_fills.py`

**Interfaces:**
- Consumes: `simulate_window`（Task 2）、`load_execution_window` 输出、`core/execution/costs.py:compute_execution_cost`、`core/domain/execution.py` 的 `FillBatch`
- Produces: `realize_window_fills(orders, *, minute_frame, snapshot, spec, quantity_rules) -> WindowRealizedResult`
  - `WindowRealizedResult(fill_batch: FillBatch, unfilled: dict[str, int], detail: pl.DataFrame)`
  - `detail` 列：`code, side, minute_index, quantity, price, fell_back: bool`；
  - 顺序：SELL 先于 BUY（沿用 `orders` 的既有排序）；逐 code：从 `snapshot` 取 `pre_close/open/limit_up/limit_down`，从 `minute_frame` 取窗口 bars；
  - 现金约束：成交金额+费用不得使现金 < 0（沿用既有缩减逻辑，按分钟递增缩减）；
  - 成本：每笔成交调用 `compute_execution_cost`（slippage 作用于成交价；佣金/印花税/过户费照旧）。

- [x] **Step 1: 写失败测试**（合成 1 只股票 3 分钟）+ 成本和现金约束用例；断言 `detail` 分钟粒度与 `fill_batch` 汇总一致；断言 SELL 在前。
- [x] **Step 2-4: 红 → 实现 → 绿**

Run: `cd platform && .venv/bin/python -m pytest tests/test_window_fills.py tests/test_backtest_fills.py -q`
（后半为目标回归：`NEXT_OPEN` 成交测试不受影响）

- [x] **Step 5: 提交**

```bash
git add platform/src/factorlab/app/backtest/fills.py platform/tests/test_window_fills.py
git commit -m "feat(platform): 窗口成交实现（明细/未成交/成本/现金约束）"
```

---

### Task 5: `run_backtest` 集成 + `WINDOW_END_BASED` marks

**Files:**
- Modify: `platform/src/factorlab/app/backtest/backtest.py`
- Modify: `platform/src/factorlab/app/backtest/orders.py`（规划参考价分支）
- Test: `platform/tests/test_backtest_window_runtime.py`

**Interfaces:**
- Consumes: Task 3 读适配、Task 4 成交、既有 `resolve_execution_schedule`/`load_market_open_frame`
- Produces:
  - `MarksPolicy.WINDOW_END_BASED`：mark = 执行日窗口**末分钟 close**；该 code 当日无分钟行 → 沿用 `mark_map` 上次 mark（停牌冻结同 `OPEN_BASED`）；
  - `run_backtest(..., execution_spec.execution_timing is NEXT_WINDOW)` 分支：
    `schedule（严格 > decision 的下一交易日）→ daily snapshot（既有）→ load_execution_window → orders（规划参考价 = 窗口首分钟 open）→ 日级 fillability（既有闸门）→ realize_window_fills → apply_fill_batch → accounting → value_portfolio(WINDOW_END_BASED) → overnight`；
  - `NEXT_OPEN` 分支代码路径零改动；`NEXT_CLOSE` 保持显式 `NotImplementedError`（7 处不动）。

- [x] **Step 1: 写失败测试**

```python
# 核心断言示例（完整文件按既有 test_backtest_runtime.py 的 fixture 模式组织）
def test_next_window_end_to_end_synthetic():
    # 1 只股票、decision=2024-01-02、窗口 [0,2]、目标 1000 股、参与率 1.0
    # snapshot: pre_close=10.0, open=10.1, limit_up=11.0, limit_down=9.0
    # 分钟 0/1/2 量各 5000，VWAP 手算 10.2
    # 断言：fills 成交 1000@10.2（±成本滑点=0）、NAV=现金+持仓×窗口末 close
    ...

def test_next_open_unchanged_smoke():
    # 跑既有 NEXT_OPEN 样例，逐字段等于改动前结果（回归锚）
    ...

def test_next_close_still_rejected():
    with pytest.raises(NotImplementedError):
        ...  # NEXT_CLOSE 路径
```

- [x] **Step 2-4: 红 → 实现 → 绿（含既有 M8 全量回归）**

Run: `cd platform && .venv/bin/python -m pytest tests/test_backtest_window_runtime.py tests/test_backtest_runtime.py tests/test_backtest_orders.py tests/test_backtest_fills.py tests/test_backtest_overnight.py tests/test_execution_accounting.py tests/test_execution_valuation.py -q`

- [x] **Step 5: 提交**

```bash
git add platform/src/factorlab/app/backtest/backtest.py platform/src/factorlab/app/backtest/orders.py platform/tests/test_backtest_window_runtime.py
git commit -m "feat(platform): run_backtest 支持 NEXT_WINDOW（窗口执行 + 窗口末估值）"
```

---

### Task 6: 持久化扩展（窗口元数据 + 成交明细）

**Files:**
- Modify: `platform/src/factorlab/adapters/execution_store.py`
- Test: `platform/tests/test_execution_store_window.py`

**Interfaces:**
- `save_backtest_result`/`load_backtest_result` 扩展：
  - `manifest.execution_spec` 增加 `execution_timing` 与 `minute_window`（序列化 spec 的原始字段）；
  - 新增 artifact：`window_fills.parquet`（列同 Task 4 `detail`），仅 `NEXT_WINDOW` 运行时产出；
  - **向后兼容**：旧版本（无窗口字段）加载不受影响；`NEXT_WINDOW` 产物缺 `window_fills.parquet` → fail closed（`ValueError`，说明期望路径）；格式版本号递增（沿用现有版本常量机制，具体常量名以实现为准，禁止发明第二套）。

- [x] **Step 1: 写失败测试**：round-trip（写入 NEXT_WINDOW 结果 → 加载 → `window_fills` 与 spec 字段逐值一致）；旧版本 fixture 加载；坏产物（删明细文件）→ ValueError。
- [x] **Step 2-4: 红 → 实现 → 绿**

Run: `cd platform && .venv/bin/python -m pytest tests/test_execution_store_window.py tests/test_backtest_persistence.py -q`

- [x] **Step 5: 提交**

```bash
git add platform/src/factorlab/adapters/execution_store.py platform/tests/test_execution_store_window.py
git commit -m "feat(platform): 执行产物持久化扩展（窗口元数据 + 成交明细，向后兼容）"
```

---

### Task 7: 端到端验收（CH integration）

**Files:**
- Test: `platform/tests/test_minute_execution_e2e.py`（`@pytest.mark.integration`）
- 证据：`governance/evidence/verification/R22/minute-execution/`（命令 + 原始输出 + 门结果）

- [x] **Step 1: 写集成测试**

```python
import pytest
pytestmark = pytest.mark.integration

def test_window_execution_on_ch_small_universe():
    """20 只股票 × 2024 年 1 月：run_backtest(NEXT_WINDOW [0,30] vwap 参与率 0.1)
    跑通；随机抽 1 只股票，用 SQL 从 CH 复算窗口 VWAP 与成交价逐值对拍。"""
    ...

def test_sealed_limit_case_on_ch():
    """挑一个真实一字涨停日：断言 BUY 不成交（或部分成交后未成完）。"""
    ...
```

- [x] **Step 2: 跑集成 + 手算对拍**

Run: `cd platform && FACTORLAB_DATA_BACKEND=ch .venv/bin/python -m pytest tests/test_minute_execution_e2e.py -q`

- [x] **Step 3: 全门回归**

Run: `make gates && cd platform && .venv/bin/python -m pytest -q`
Expected: gates 全绿；≥ 2570 passed / 13 skipped

- [x] **Step 4: 证据与提交**

```bash
# governance/evidence/verification/R22/minute-execution/：命令、原始输出、门结果
# 按目录公约分别提交 platform/ 与 governance/ 改动
git add platform/tests/test_minute_execution_e2e.py
git add governance/evidence/verification/R22/minute-execution
git commit -m "test(platform): R22 分钟执行端到端验收（CH 小样本 + 手算对拍 + 全门）"
```

---

## Self-Review（本计划对 spec 的覆盖）

| Spec 要求 | 对应 Task |
|---|---|
| §2 配置面（窗口/价格口径/分批/参与率/触发/兜底） | Task 1/2 |
| §2 真实约束（一字板/停牌/参与率/T+1/成本） | Task 2/4/5 |
| §3 M8 接口（timing/spec/fills/marks/store/分钟读取） | Task 1/3/4/5/6 |
| §4 A股细节（240 网格/session/涨跌停/raw） | Task 2/3 |
| §5 验收 1-7 | Task 1/5/7 |
| §6 风险（分块、规划价口径） | Task 3/5（分块由按日读取保证） |

## 未竟/后置
- 量能触发（V2）、分钟 NAV（V2）、盘中临停精确规则（另立）——见 design.md §6。
