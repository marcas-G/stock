# 分钟级执行（日频信号 + 分钟成交）设计

日期：2026-09-15
状态：设计已确认（2026-09-15 用户问答）；**V1 已实施并于 R22 验收**，见
[`R22 分钟执行证据`](../../../../governance/evidence/verification/R22/minute-execution/SUMMARY.md)；
原实施清单见同目录 `plan.md`。
关系：M8 执行层扩展；与 [`开放算子方案`](../2026-09-15-open-operators/) 相互独立

> 需求原话："要支持分钟级别的执行啊"；边界确认："日频信号 + 分钟执行"；配置要求："1. 支持多种配置方法
> 2. 支持多种配置 3. 尽量模拟真实"；V1 范围："V1 加价格触发"。

---

## 0. 需求与决策记录

| # | 决策 | 出处 |
|---|------|------|
| 0.1 | **日频信号 + 分钟执行**：信号仍按 EOD 产出（现有因子链不变）；成交挪到次日分钟窗口内完成 | 用户选择 |
| 0.2 | **执行时点/成交价/约束全部可配置**（多种配置面） | 用户原话 |
| 0.3 | **尽量模拟真实**：参与率上限、一字板不成交、停牌跳过、触发未达不成交 | 用户原话 |
| 0.4 | **V1 含价格触发**（限价单语义：触发才成交）；量能触发归 V2 | 用户选择 |
| 0.5 | 平台现状确认：M8 仅 `NEXT_OPEN`，分钟数据（bars_1m）已有（18.5 亿行 / 2020-01~2026-08） | R01 复核 |

## 1. 目标与非目标

### 目标
1. `ExecutionSpec` 可配置：执行窗口、价格口径、分批、参与率、触发、兜底；
2. 成交仿真：窗口 VWAP / 指定口径 / 限价触发 / 部分成交顺延 / 封板与停牌拦截；
3. **向后兼容**：默认 `NEXT_OPEN` 行为逐值不变（现有回测回归全绿）；
4. 全链路：日频信号 artifact → M7 组合 → 分钟执行 M8 → 持久化（含分钟成交明细）。

### 非目标
- `NEXT_CLOSE`（仍不做，7 处显式拒绝保持）；
- 分钟级**信号**（信号仍日频 EOD；分钟信号链另议）；
- tick 级执行（lob/tick 数据不参与本方案）；
- 盘中临停完整规则库（V1 仅按分钟缺行跳过）；
- 分钟级 NAV 曲线（NAV 仍日频采样；日内回撤不在范围）。

---

## 2. 配置面（执行 spec）

M7/M8 无 CLI，配置落在 Python `ExecutionSpec`（pydantic，`extra="forbid"`）：

```python
class MinuteWindowSpec(BaseModel):
    start: int                     # minute_index 闭区间起（0=09:25 开盘集合竞价，239=15:00 收盘集合竞价）
    end: int                       # 闭区间止
    price_basis: Literal["vwap", "open", "close", "twap", "mid"] = "vwap"
    slices: list[SliceSpec] | None = None      # 分批；缺省 = 单笔跑完 [start,end]
    participation: float = 0.10    # 单分钟成交 ≤ 该分钟实际成交量 × participation（0<r<=1）
    trigger: TriggerSpec | None = None          # 价格触发；None = 必成交（窗口内按参与率成交）
    fallback: Literal["none", "close"] = "none" # 窗口结束未成/未触发：none=不成交；close=按最后成交切片末分钟收盘价兜底

class SliceSpec(BaseModel):
    start: int; end: int; weight: float         # 子窗口 + 目标量权重（Σweight = 1）

class TriggerSpec(BaseModel):
    mode: Literal["limit", "vwap_offset"]
    ref: Literal["pre_close", "window_open", "window_vwap"] = "pre_close"   # limit 模式的基准
    offset_bps: float = 0.0        # 买：limit = ref×(1+offset_bps/1e4)；卖：limit = ref×(1-offset_bps/1e4)
```

语义（买卖对称，`side` 决定方向）：
- **none 触发**：从切片窗口起逐分钟成交，直到目标量达成或窗口结束；
- **limit 触发**：`limit_price` 静态基准；`BUY: minute.low ≤ limit → 成交`，`SELL: minute.high ≥ limit → 成交`；
- **vwap_offset 触发**：`limit` 随"当日窗口累计 VWAP"滚动更新（更接近执行算法）；
- 触发成交价：`BUY = min(limit, minute.open)`；`SELL = max(limit, minute.open)`（限价或更好）；
- 非触发成交价：按 `price_basis` 计算（vwap = Σamount/Σvolume，均取"实际发生成交的分钟"；open/close/twap/mid 同理）；
- **未触发/未成完**：窗口结束按 `fallback` 处理（默认 `none` = 如实不成交并记录）。

**真实约束（V1 必做）**：

| 约束 | 规则 |
|---|---|
| 参与率 | 单分钟成交 ≤ `participation × minute.volume`；超出部分顺延下一分钟；窗口结束仍未成完按 fallback |
| 一字板 | `open==high==low==stk_limit`：涨停（BUY 拦截）、跌停（SELL 拦截）；触板后打开（high≠low）的分钟允许 |
| 停牌 | 执行日 daily 缺行 → 沿用现有 WS4 停牌语义（持仓冻结 / 目标行跳过）；分钟缺行 → 该分钟跳过 |
| T+1 / 资金 | 不变（SELL 先于 BUY、卖出扣 sellable、买入当日不可卖、现金严格 ≥0） |
| CA Gate | 不变（decision→execution 跨日，窗口在执行日开盘后，除权事件窗口同 NEXT_OPEN） |
| 成本 | 复用 `ExecutionCostSpec`；滑点叠加在成交价上 |

---

## 3. 与现有 M8 的接口

| 现有组件 | 改动 |
|---|---|
| `core/domain/timing.py:32 ExecutionTiming` | 增 `NEXT_WINDOW`（`NEXT_CLOSE` 仍拒绝） |
| `core/execution/spec.py ExecutionSpec` | 增 `timing: ExecutionTiming = NEXT_OPEN` + `minute_window: MinuteWindowSpec | None`；校验：`NEXT_WINDOW` 必带窗口、`NEXT_OPEN` 禁止带窗口 |
| `app/backtest/backtest.py:145 run_backtest` | 增 `NEXT_WINDOW` 分支；`MarksPolicy` 增 `WINDOW_END_BASED`（窗口末分钟收盘 mark；停牌冻结沿用）；`NEXT_OPEN` 路径零改动 |
| `app/backtest/orders.py` | 规划参考价：`NEXT_WINDOW` 用窗口首分钟开盘价（失败即 fail，不发明价格）；其余逻辑复用 |
| `core/execution/fillability.py` | 新增分钟判定路径（纯函数）；日级证据（stk_limit/停牌）仍为前置闸门 |
| `app/backtest/fills.py` | 新增 `realize_window_fills`（产出既有 `FillBatch` + 未成交明细）；`NEXT_OPEN` 版本不动；成本计算共用 |
| `adapters/read/market_open.py:192` | 不变（日级证据）；新增读取层不改变其契约 |
| `adapters/intraday.py:202 load_bars_1m_codes` | 复用；新增 `load_execution_window` 包装（240 网格完整性与 session_type 校验；duckdb 后端 fail fast） |
| `adapters/execution_store.py` | schema version + 窗口元数据与逐笔成交分钟字段；round-trip 与旧版本拒绝 |
| 数据前提 | 仅 CH 提供分钟（`FACTORLAB_DATA_BACKEND=ch`）；分钟为 raw 价（与 M8 raw 执行价一致） |

**新纯计算模块**：`core/execution/minute_window.py` —— 窗口切片（含跨午休按 `minute_index` 顺序）、价格口径、累计 VWAP、触发器、参与率迭代与顺延、封板/缺行判定。全部纯函数、无 IO（core 纯净门约束）。

---

## 4. A股现实细节（以数据契约为准）

- `bars_1m` 契约（`adapters/intraday.py` docstring）：每 `(code, 交易日)` 恰 **240 行**固定网格；`minute_index` 0=09:25 开盘集合竞价、239=15:00 收盘集合竞价；`session_type` 0/1/2；OHLC raw、`amount=元`、`volume=股`。**索引→时钟映射一律以该契约为准，禁止自造。**
- 窗口跨午休：按 `minute_index` 顺序自然衔接（11:30 后直接 13:01），不需要时钟换算。
- 涨跌停价：来自 daily `stk_limit`（现有 `load_market_open_frame`）；分钟封板判定用 `open==high==low==limit`。
- 停牌：全市场日频缺行（现有语义）；星/创盘中临停 V1 仅表现为分钟缺行（跳过），不做专门规则库。
- 复权：信号为 qfq 口径、执行与 NAV 用 raw 价（与现有 M8 一致）；CA 处理沿用 CA Gate。

---

## 5. 验收标准

1. `NEXT_OPEN` 现有行为**逐值不变**（现有 M8 全量测试 + 抽样回测回归）；
2. 配置校验完备：非法窗口/切片权重和≠1/参与率越界/NEXT_WINDOW 缺窗口等全部 fail fast；
3. 成交仿真对拍（小样本手算）：VWAP、限价触发价、参与率顺延、部分成交、fallback 各一例逐值一致；
4. 真实约束：一字涨停不买 / 一字跌停不卖 / 停牌日不成交 / 无触发不成交；
5. 持久化 round-trip（含分钟字段）；旧 schema 版本明确拒绝；
6. `make gates` 全绿；平台全量 pytest 不回退（基线 2570 passed / 13 skipped）；
7. 端到端：CH 上小样本（如 20 只 × 1 个月）跑通 `run_factor → construct_target_portfolio → run_backtest(NEXT_WINDOW)` 并留证。

---

## 6. 风险与开放项

| # | 项 | 说明 |
|---|---|---|
| 6.1 | 分钟数据量 | 全市场窗口读取按日分块（30 分钟窗口 ≈ 15 万行/日）；全历史全市场回测耗时需实测 |
| 6.2 | 规划参考价语义 | 现 NEXT_OPEN 用执行日 daily open 作规划 equity（历史约定）；NEXT_WINDOW 改用窗口首分钟开盘价——需在文档写明口径差异 |
| 6.3 | 量能触发 | V2 议题（与量价异动研究挂钩），本方案预留 `trigger.mode` 扩展位 |
| 6.4 | 分钟 NAV | V2 议题（日内采样、日内回撤） |
| 6.5 | 盘中临停 | V1 只按缺行跳过；如需精确规则（临停起止）另立 |
