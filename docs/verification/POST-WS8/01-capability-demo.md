# POST-WS8 能力实演（2026-09-14）

目的：回答"现在整个项目能做哪些事"——**用真实数据跑通全链**，不以设计文档代结论
（手册纪律 Evidence over Claim）。全部命令在 `projects/quant-platform-main`
（平台 venv，`FACTORLAB_DATA_BACKEND=ch` 真 ClickHouse）或研究 worktree 实跑。

## 1. 日频因子链（真 CH，端到端）

三个因子 spec（/tmp/demo/）实跑 `factorlab run --chunk-days 60`：

| 因子 | 公式 | n_weeks | ic_mean | 十分位 spread |
|---|---|---|---|---|
| momentum_demo | `ts_mean(close,120)/ts_delay(close,120)-1`（winsorize+standardize） | 25 | +0.0347 | −0.0192 |
| reversal_demo | 反转类 | 46 | +0.0144 | −0.0070 |
| vol_demo | 波动类 | 33 | −0.0001 | −0.0127 |

产出：`signal.parquet / labels.parquet / panel.parquet / weekly.parquet / summary.json`
（schema v 契约由 `adapters/parquet_artifacts` 守）。覆盖面：universe 5535 只、
2025-07-01~2026-06-30、rows=1,314,795。

## 2. 因子库级分析（≥2 因子，真跑）

```
$ FACTORLAB_RESULTS_DIR=/tmp/demo/results factorlab list
vol_demo      | dir=-1 | ic=-0.000131 | spread=-0.012654
reversal_demo | dir=-1 | ic= 0.014438 | spread=-0.006978
momentum_demo | dir= 1 | ic= 0.034697 | spread=-0.019240

$ factorlab corr momentum_demo reversal_demo vol_demo
              rank_corr  pearson  n_weeks
mom × rev        0.1100  -0.0502      242
mom × vol        0.1976  -0.2624      242
rev × vol        0.2532   0.0568      242

$ factorlab svd
PC1 奇异值 1.516 累计 50.5% | PC2 0.994 累计 83.7% | PC3 0.490 100%
PC1: momentum_demo(−0.70), vol_demo(+0.70), reversal_demo(+0.11)

$ factorlab resic momentum_demo reversal_demo vol_demo
组联合回归 R² = 0.0375（25 周，周均样本 5106）
momentum_demo resIC=0.0312 (t=1.93) | reversal_demo resIC=−0.0162 (t=−0.75)
vol_demo resIC=0.0088 (t=0.27)
```

`factorlab show momentum_demo`：spec 全文 + IC/IR/t_stat/sign_consistent +
十分位 spread + 月/季换手 + 覆盖统计。

## 3. Web 可视化面

`factorlab serve`（只读 results_dir）实跑 + curl 抽查：
`GET /` → HTTP 200（列出 3 因子）；`GET /factor/momentum_demo` → HTTP 200
（IC 曲线 Plotly 图数据在页内，52 周 x 轴）。

## 4. 分钟面（bars_1m）× 本地事实库 逐值对拍

```
$ run_1m_feature.py check-day 2024-01-15     # 平台引擎(CH 生产库) × 本地 parquet 工具路径
local 5249 | engine 5249 | 交 5249 | 仅local 0 | 仅engine 0
vwap30_bias:      交集 5249 行  max|Δ|=0.000e+00
open30_amt_share: 交集 5249 行  max|Δ|=0.000e+00
check-day 2024-01-15 PASSED（引擎 × 本地 5249 行逐值一致）  总耗时 19.1s  peakRSS=1.61GB
```

分钟算子族（`factorlab op list` 中的 `im_` 7 个 / `day_` 6 个）走同一 spec 机制。

## 5. 算子面与插件面

`factorlab op list` = **55 个算子**（ts 23 / ta 10 / cs 7 / im 7 / day 6 / gp 2）；
`op add/remove` 走 AST 白名单扫描的插件机制（禁 eval/exec/open/os/sys/subprocess）。
`catalog dump/docs` 提供机器可读目录（含 `registry_inventory` 全 55 算子 + `closed_gates`
错误手册）——**本次修复前 `op list` 打印 `[]`，与此指引自相矛盾**。

## 6. 守卫面（当天实跑）

- 平台全量：**2470 passed / 13 skipped**（本机 venv，真 CH 用例不跳过）
- 研究侧 T2（emb 3.11）：**183 passed / 1 skipped**；T1（平台 venv）：**24 passed**
- 架构双门 / 文档路径门 / catalog diff 门：含在平台全量内

## 7. 本次实演抓到并修复的缺陷（3 处，均有 TDD 红绿 + 回归）

| # | 缺陷 | 危害 | 根因类型 | commit |
|---|---|---|---|---|
| 1 | process 处理器注册丢失 | `factorlab run` 直接 KeyError（因子跑不了） | 装配副作用充当隐式契约 | ce3ffcc |
| 2 | process 链 NaN 毒化 | 截面含 1 个 NaN → 整截面 NaN → IC 归零（332 只退市股触发） | 语义前提未写进契约 | ce3ffcc |
| 3 | `op list` 空注册表 | 打印 `[]`，与 catalog 关闸指引自相矛盾（误导写因子的 AI） | 同 1 | 944cfb6 |

共性教训（已写入平台 spec §11）：**"测试全绿" ≠ "链路能跑"**——三类缺陷都在单元
测试盲区（导入顺序掩盖 / NaN 语义 / 入口未装配），**实跑门（真数据 + 全链）应作为
收口标准动作**；下一轮建议补"注册面完整性门"。

## 8. 环境口径注记

- 本机 `free -g` 实测 125G 总 / 98G 可用——文档中"16GB 无页面文件"是**目标机器**约束
  （spec 6.1），本机跑批不受限；分钟面 1 日对拍 peakRSS 仅 1.61GB。
- 平台 duckdb 库仍不存在（pending #1）：`FACTORLAB_DATA_BACKEND=ch` 为当前生产读路径；
  `data rebuild/refresh` 与 `exclude_st` 一类依赖平台库的 universe 规则在 token 恢复前不可用。

## 9. 追加（同日）：插件算子链 + 分区前缀陷阱（commit 96eaa43）

第 4 处装配类缺陷（追问"能自己定义算子吗"时暴露）：
- `factorlab op add` 成功、`op list`/`op doc` 可见，但 **run 链报"未知算子"**
  （`discover_plugins` 只在 op 子命令被调）；补齐后第二层：生成代码以**裸名字**调用，
  插件名不在 exec 作用域 → NameError。修复 = 装配单点补插件发现 + 加载器登记来源模块
  + 引擎注入 import 头（无插件时**零注入**，生成代码逐字节不变）。

**分区前缀陷阱（实测两例，非推测）**：分区与窗口预热都据**名字前缀**识别——
`ts_` → `.over(asset, order_by=date)`；裸名 → 元素级（行序窗口跨 code 块）；
`_ts_window_days`：`ts_op(close,60)` → 60 天 vs `bare_op(close,60)` → **0 天**。
- 证据 ①（假库 9 日）：分区版首窗 `null`（正确）vs 裸名版 `19.33`（窗口吃到另一 code 的行）
- 证据 ②：真 CH 上二者**可能恰好逐位相同**（满载历史把污染行裁掉，实测 sha256 相同一例）
  —— 属加载策略巧合而非保证，故设**注册期硬门**（宁报错不静默），已装违规插件
  discovery 只告警并跳过（不堵死 `op remove` 修复路径）。
- 终态门：平台全量 **2480 passed / 13 skipped**；真 CH `ts_tail_ratio` 因子端到端全通。

**今日缺陷类归纳**：4 处缺陷全部是"**注册/装配靠 import 副作用**"这一条根因的变体
（process 处理器 / CLI 算子表 / 插件算子 ×2），另有 1 处"语义前提未写进契约"（NaN 毒化）
与 1 处"名字前缀决定语义却无门"（分区前缀）。下一轮建议补两条常驻门：
**注册面完整性门**（入口调用后声明依赖的注册表必须非空）与**前缀-语义一致性门**。
