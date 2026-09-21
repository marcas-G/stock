# porteval（组合评估器）设计文档

日期：2026-09-21 ｜ 状态：**已拍板**（参数面 V1 冻结）
定位：**分数 → 组合 → 评估** 的研究级组合层。上游 = xscore（截面分数）；下游 = 报告/决策。
生产执行细节（排队/部分成交/融券/税/冲击模型）不在此层，属平台 M8。

## 冻结决策

- **仅多头**（`side=long` 锁死；多空仅作研究对照时可另开分支）
- **默认成交口径 `exec=open`**（T 信号 → T+1 开盘价成交；`close` 仅作乐观对照）
- **容量默认关闭**（`aum=None`；显式给 AUM 才启用参与率上限）
- 研究级近似：不含滑点/冲击/排队；涨跌停用"禁买禁卖+冻结"近似

## V1 参数表（唯一权威）

| 段 | 参数 | 默认 | 说明 |
|---|---|---|---|
| 域 | `mv_scope` | `all` | `all`/`Q1Q3`/`Q1Q2`（total_mv 五分位） |
| 域 | `min_adv` | 2e7 元 | 20 日均额下限（0=不过滤） |
| 域 | `adv_window` | 20 | ADV 窗口（交易日） |
| 域 | `suspend_policy` | `skip` | 停牌跳过（顺延不做） |
| 组合 | `selection` | `top_quantile` | 或 `top_n` |
| 组合 | `q` | 0.1 | 多头分位（selection=top_quantile） |
| 组合 | `top_n` | — | 固定只数（selection=top_n） |
| 组合 | `weighting` | `equal` | equal（mv/score 后续） |
| 调仓 | `every` | 5 | 调仓频率（交易日） |
| 调仓 | `exec_mode` | `open` | open=T+1 开盘 / close=T 日收盘 |
| 调仓 | `limit_policy` | `block` | 涨停禁买/跌停禁卖+冻结；`ignore` 关闭 |
| 成本 | `fee_bps` | 7 | 单边费率（按 Σ|Δw| 计） |
| 容量 | `aum` | None | 组合规模；None=容量关闭 |
| 容量 | `participation` | 0.05 | 单票单日参与率上限（ADV×参与率/AUM） |
| 容量 | `cap_action` | `truncate` | 截断不重分配（redistribute 后续） |
| 评估 | `benchmark` | `domain_equal` | 同域等权；cap_weighted 后续 |
| 评估 | `window` | 信号有效日 | 策略与基准同窗比较（锁定） |
| 评估 | `metrics` | 全量 | 年化/波动/Sharpe/MDD/超额/IR/换手/成本/命中/分年 |

## 输入/输出

- 输入：`signal.npz`（xscore 产物）+ 面板/`open_adj`/`mv` 缓存（quantresearch/data/cache）
- 输出：`portfolio.json`（统计+分年）+ `manifest.json`（输入指纹/参数快照/代码指纹/git）

## 明确不做（V1）

ST/次新剔除（缺数据）、行业/风格中性、滑点/冲击、缓冲带/最小持有期、多空、组合优化器
——后续按需立项；生产执行属 M8。
