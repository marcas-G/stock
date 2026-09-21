# xscore 流水线（Prefect 3）

`数据/面板 → score(分组×模型) → portfolio(执行口径×域) → report` 的文件级 DAG。

## 运行

```bash
# 接入 UI（可选；先起 server）
bash governance/ops/install_prefect_server.sh      # UI: http://127.0.0.1:4200
# 跑配置
research/tools/xscore/pipeline/run.sh research/tools/xscore/pipeline/configs/m0-split.yaml
```

- 缓存：任务键 = 面板指纹 + 分组列 + 模型 + 折 + 代码指纹；输入不变秒级跳过（30 天有效）
- 计算 step 在 **platform venv** 子进程执行（`score_once.py` / `portfolio_once.py`），编排环境（`research/.venv`）只装 Prefect
- 产物：`<out>/scores/<group>_<model>/{signal.npz,metrics.json,manifest.json}`、
  `<out>/scores/.../portfolio_<exec>_<domain>.json(.manifest.json)`、`<out>/REPORT.md`
- 并发：`--max-workers`（默认 2，尊重内存）；重任务仍建议外层 `governance/ops/heavy.sh`

## 配置字段

见 `configs/m0-split.yaml`：`panel` / `out` / `folds` / `subsample` / `groups`（`"*"` 或
`{source: reference, scale: daily|minute}` 或显式成员名列表）/ `models`（M0a..M4）/
`portfolio`（`exec: open|close`、`domains: all|Q1Q3`、`every/q/fee_bps`）。

## 依赖

`research/.venv`（uv 建）：`prefect>=3,<4` + numpy + pyyaml；计算依赖全部在 platform venv。

## portfolio 段默认（porteval V1）

`exec: open`（T+1 开盘）、`limit_policy: block`（涨跌停冻结）、`min_adv: 0`（默认不过滤；
生产建议 2e7）、`aum` 缺省=容量关闭。组合层实现与参数见
`knowledge/design/research/specs/2026-09-21-porteval-design.md`。
