# 因子编写与提交手册（从想法到结果）

日期：2026-09-16 ｜ 读者：写因子的用户（人）｜ 一张纸跑通闭环：
**写 spec → 秒级自检 → 跑出结果 → 读结果 → 入库/提交**。

> 权威文档（活文档，细节以它们为准）：
> - 模板与阈值：`knowledge/handbooks/factor-mining-playbook.md`（§2 模板 / §4 评估 / §7 入库）
> - 字段与 CLI：`knowledge/contracts/interface.md` §1-§3；算子/列活文档：`knowledge/contracts/catalog.md`
> - 技能（agent 自动化）：`~/.claude/skills/factorlab-dsl`、`factorlab-evaluate`、`.claude/skills/factor-mine`

## 0. 前置（一次性）

```bash
FLAB=/data/students/gaolei/stock/platform/.venv/bin/factorlab   # 平台 venv（Python 3.13）
export FACTORLAB_DATA_BACKEND=ch      # 当前唯一可用读后端（duckdb 平台库不存在）
```

- **重任务护栏**（全市场/长窗/分钟链必设）：`FACTORLAB_MAX_MEMORY=8GB`（超限干净中止，见 interface §1）；
- **ST 口径**：`universe.rules.exclude_st: true` 需要 `stock_st` 表；CH 无该表时默认 fail fast，
  临时可用 `FACTORLAB_ST_DEGRADE=allow` 降级（结果 = **无 ST 口径**，不可与 ST 过滤结果混比）。

## 1. 写 spec（5-10 分钟）

- 位置：`$QUANTRESEARCH_ROOT/factor/<族>/<名>.yaml`；命名 `<类别>_<逻辑>_<窗口>`（如 `momentum_20d`）；
- 方向写进 spec：`direction: 1`（越大越好）/ `-1`（越小越好）；

最小模板（可直接改）：

```yaml
name: my_factor_20d
category: custom
direction: 1
universe:
  rules: {exclude_st: true, exchanges: ["SSE", "SZSE"]}
date: {start: "2023-01-01", end: "2026-07-31"}
process:
  - winsorize(quantile=0.99)
  - standardize()
formula: |
  from polars_ta.prefix.wq import ts_mean, ts_delay
  signal = ts_mean(close, 20) / ts_delay(close, 20) - 1
```

- 更多模板（动量/波动/量价/技术指标/自定义 def/参数化 `params`）：playbook §2；
- 算子与列可用性：`$FLAB catalog docs` 或 `$FLAB op list --catalog`（**别凭记忆写算子名**）；
- 分区纪律：时间序列算子 `ts_*`、截面 `cs_*`、分组 `gp_*`；负向 shift 会被未来函数门拒绝。

## 2. 秒级自检（不连库）

```bash
$FLAB lint $QUANTRESEARCH_ROOT/factor/<族>/<名>.yaml
# OK <name>   ← 通过；非 0 退出 = 语法/算子/未来函数问题，按报错修
```

## 3. 跑出结果并读它（1 分钟级）

```bash
cd /data/students/gaolei/stock/platform
FACTORLAB_DATA_BACKEND=ch .venv/bin/factorlab run "$QUANTRESEARCH_ROOT/factor/<族>/<名>.yaml"
# 快速版（不做分层回测）：加 --no-backtest；参数变体：--set k=v（results 独立目录）
```

- 产物：`runs/platform/<名>/`（本地，**不入库**）；核心 `summary.json`；
- 读结果：
  - `$FLAB show <名>` — spec 原文 + IC/分层/换手完整摘要；
  - `$FLAB list` — 全库排序浏览；`$FLAB serve` — Web 可视化（IC/净值曲线）；
- 怎么看（IC、spread、分层单调性、阈值、过拟合警示）：playbook §4；联合诊断 `corr`/`resic`/`svd`。

## 4. 入库与提交（"提交"= 两件事）

**a) 研究入库**（每个因子必须有档案，负结论也入库）：

- 档案：`$QUANTRESEARCH_ROOT/dossiers/factors/<族>/<stem>.md`（模板 `$QUANTRESEARCH_ROOT/dossiers/factors/_template.md`）；
  与 spec 同族同短名并行；改 yaml 必须同步档案「实现全文」节；
  参数变体（`--set`）记入主档案「迭代历史」，不单独建档案；
- 索引：`platform/.venv/bin/python research/tools/factor_lib/build_index.py`（`--check` 用于门）；产物 `$QUANTRESEARCH_ROOT/index/factors.md`；
- 常驻门：`make gates`（含索引一致、档案注解、结构/契约门）。

**b) git 提交**（纪律见根 `AGENTS.md`）：

- **R37 起 spec/档案/索引在研究产物区，不是 git 仓**——无主仓提交；改工具/平台才提交主仓；
- 信息：`<type>(<scope>): <做了什么>`（如 `feat(tools): 索引生成器支持产物区根`）；
- **一次提交只动一棵树**（platform / research / knowledge / governance 分权）；
- 提交前 `make gates` 全绿（预存红除外，须注明）；破坏性操作先备份。

## 5. 常见坑（快查）

| 症状 | 处理 |
|---|---|
| `未知算子: xxx` | 查 `$FLAB op list --catalog`；不存在就换算子或 `def` 自定义（playbook §2.5） |
| 报未来函数 / shift 负值 | 用 `ts_delay` 正窗口；`forward_*` 列禁止读 |
| `exclude_st` fail fast | 见 §0：加 `FACTORLAB_ST_DEGRADE=allow` 或补 `stock_st` |
| 连库失败 / 空数据 | 确认 `FACTORLAB_DATA_BACKEND=ch`；数据是否灌到目标日期（`factorlab-ch-pipeline` 技能/data-map） |
| 内存被杀 / 主机卡 | 设 `FACTORLAB_MAX_MEMORY=8GB`；不要与 LLM 服务/多会话并发重任务 |
| 分钟级因子 | 走 `platform/tools/1m_features/` + 分钟覆盖池（缺 (code,day) 用 `FACTORLAB_MINUTE_UNCOVERED=drop`）；不要在日频 spec 里写分钟算子 |
| 结果与旧口径不可比 | 记录快照日期 + 口径开关（ST 降级/后端）；档案里注明 |

## 6. 一页命令速查

```bash
FLAB=/data/students/gaolei/stock/platform/.venv/bin/factorlab
export FACTORLAB_DATA_BACKEND=ch FACTORLAB_MAX_MEMORY=8GB   # 重任务才需要上限

$FLAB lint   <spec.yaml>                 # 秒级自检
$FLAB run    <spec.yaml>                 # 跑 + 评估（--no-backtest / --set k=v）
$FLAB show   <name>                      # 单因子完整摘要
$FLAB list | serve                       # 浏览 / Web
python3 research/tools/factor_lib/build_index.py   # 重建因子索引
make gates                               # 常驻门
```
