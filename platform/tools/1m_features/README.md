# tools/1m_features — bars_1m 全市场折日特征批算（W7）

研究侧工具：把平台分钟漏斗机制（`factorlab.core.engine.minute`，W1–W6 已实现）用到
真实特征、全市场、全历史（2020-01..2026-08）批算。**批算结果 == 平台引擎结果**
——两者共用同一纯计算入口 `compute_minute_factor_panel`，且经单日交叉对拍闸门
（`check-day`）在真实生产数据上逐值验证（max|Δ|=0.0）。

特征定义见 [features.py](features.py)（vwap30_bias / open30_amt_share 两特征），
口径与平台规格 `docs/superpowers/specs/2026-09-08-factorlab-1m-funnel-design.md`
B2/B6 一致：im_* 窗口 = 分钟槽位、严格日内；折日输出 (date, code) 常数一行；
日级上下文仅经注入列（eod_close/prev_close/day_amt/day_vol/adv20_*），源 =
日频事实库 daily_fact.parquet（与 CH 生产库 daily 同源同值）。

## 数据源（本机事实库，只读）

- bars：`/data/students/gaolei/stock/data/fact/bars_1m/year=YYYY/month=MM/part-000.parquet`
  240 槽/交易日固定网格，raw，amount 元 / volume 股；缺口全在整日层
- daily：`/data/students/gaolei/stock/data/fact/daily_fact/daily_fact.parquet`
  （code 带后缀 '000001.SZ'；注入列按 (code, 交易日) 有行情行序列滚动——停牌日
  自动隔开，adv20 语义与日频 adv20 相同）

路径可用 `--bars-root` / `--daily` 或环境变量 `BARS_1M_ROOT` / `DAILY_FACT` 覆盖。

## 命令

```
python run_1m_feature.py batch [--start 2020-01] [--end 2026-08] [--only YYYY-MM]
python run_1m_feature.py merge
python run_1m_feature.py check-day YYYY-MM-DD
```

### batch（按月流式批算，断点续跑）

- 逐月：读月 bars 帧（单进程 ~1.1GB）→ 注入列（40 日历日左窗）→
  `compute_minute_factor_panel` 折日 → QA（折日行数 == (date, code) 网格数；
  非有限值计数打印，研究侧按规格保留原样）→ 原子写
  `output/month=YYYY-MM/part.parquet`（tmp+rename）→ state.json 记 rows → del+gc。
- 续跑：已完月（state 有 rows 且 part 存在）跳过；失败月记 `{"error": ...}` 并
  继续，全部失败月列于退出报告（退出码非 0）。幂等，可随时中断重跑。
- 实测：单月 ≈21s（2024-01 全量 ~5.2k 代码 × 22 交易日）；峰值 RSS ≈7GB（16GB
  无页面文件机器建议 `POLARS_MAX_THREADS=4`，实测 6.9GB）。

### merge（逐特征单文件）

`output/vwap30_bias.parquet` / `output/open30_amt_share.parquet`：
[date, code, 特征]，按 (date, code) 排序；tmp+rename 原子落盘；state.json 记
merged 标记。幂等（重跑覆盖全史产物）。

## 数据缺口（两事实库池漂移，2025-12 起）

daily_fact.parquet 是**构建时点上市池（5866 code）+ 其后按日追加**的快照：日频
池冻结后新上市 code（IPO / 北交所 920 段 / 退市整理等）只进 bars zip 源，不进
daily 快照池 → bars 有行、日线注入面无行（实测 2026-08 每月 ~10-25 (code,date)
/天，34 唯一 code；2025-12 前零漂移）。

处理政策（对齐平台引擎语义）：无当日日线行的 (code,date) 意味着
eod_close/prev_close/day_amt/adv20 全部未定义，**引擎侧同样 fail-fast（不可
算）**——研究批算把它作为数据面缺口**剔除并计数**（折日输出只含两源一致的键 =
引擎可算键集），逐月打印 `日线池漂移: 剔除 N 个 (code, date)`。缺口随日频事实库
下次全量重建自然消失；若日线重建后重跑受影响月份即恢复完整网格。

### check-day（平台引擎 × 本地 交叉对拍闸门）

单日：本地 parquet 工具路径 × 平台引擎（CH 生产库 `run_factor_minute`，同 spec
同公式同窗口）逐值比较——交集键上两特征 max|Δ| ≤ 1e-6 且双方无 null 才算
PASSED（退出码 0；键差异：引擎按 SSE/SZSE 池，仅 local 侧键打印提示，仅引擎侧
键直接失败——数据面缺口）。需本机 CH 生产库在跑。

实测（2024-01-15）：local 5249 / engine 5249 / 交 5249，两特征 max|Δ| = 0.0。

## 产物与 QA（2026-09-09 全史批算完成）

80 个月（2020-01..2026-08）全部 OK（零失败月；2025-12..2026-08 池漂移月剔除
并计数）。merge 后：
- `output/vwap30_bias.parquet`、`output/open30_amt_share.parquet`
  **7,721,191 行/特征**（= Σ 80 月部分行和；键唯一、按 (date, code) 有序、两
  特征文件键集一致）。按年：2020 903k → 2025 1.29M 递增，2026 845k（至 8 月）
- QA 口径：
  - vwap30_bias 极端死盘（前 30 槽零成交）→ 0/0 → NaN 保留并计数：**1176**；
    null=0；值域 ±17%，均值 ≈ -3e-4
  - open30_amt_share ∈ [0, 1] 无越界；null=**72**（全在 2026-06，11 code——该
    code 当日日线 amount=null 数据洞，day_amt 未定义 → null 保留并计数）
  - 交叉对拍：check-day 2024-01-15 PASSED，且 merged 中该日两特征值逐位复现
    引擎×本地对拍值
- output/ 已 gitignore（产物为事实库派生数据，不入库）

## 与平台的关系

- 平台机制改动永远在 `main`（本工具零平台改动，纯消费者）；
- 本工具/特征定义/README 属研究内容，提交在 `research` 分支。
