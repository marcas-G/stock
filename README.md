# stock — 个人量化研究工作区

数据资产、代码项目与工程文档的统一工作区（2026-09-12 按 V-Model 手册
（`docs/递归式需求驱动系统工程开发手册：NASA Systems Engineering × V-Model.md`）
完成清理重构）。

## 根目录导览（固定 7 项，见 `docs/directory-conventions.md`）

| 条目 | 是什么 |
|---|---|
| `data/` | 全部数据资产（raw / fact / panel / calib / ref 五类）——定位入口：`docs/data-map.md` |
| `projects/` | 全部代码项目（platform 双 worktree、quant_core_shim、ashare_alpha3） |
| `docs/` | 工作区工程文档（P0-P8、数据地图、目录公约、追踪矩阵、验证证据） |
| `_archive/` | 30 天可逆归档区（2026-10-12 到期，政策见 `docs/archive-policy.md`） |
| `README.md` | 本文件 |
| `.gitignore` | 根仓库白名单（只跟踪文档，其余全忽略） |
| `.git/` | 工作区文档版本库（只含 docs/；数据与代码各自的 git 在各自目录） |

## 三条工具链（新路径）

```bash
# 1) 平台（factorlab）：worktree 在 projects/ 下，editable 安装在 emb 环境
cd projects/quant-platform-main && /data/students/gaolei/anaconda3/envs/emb/bin/python -m pytest tests/ -q

# 2) 研究工具（lob_fact 等）：路径单点在 tools/lob_fact/config.py
cd projects/quant-platform-research/tools/lob_fact
/data/students/gaolei/anaconda3/envs/emb/bin/python -m pytest tests/ -q      # 183 tests

# 3) ClickHouse 灌入/对账（127.0.0.1:8123, db=factorlab）
cd projects/quant-platform-research/tools/ch_ingest
/data/students/gaolei/anaconda3/envs/emb/bin/python reconcile.py             # 只读对账（全一致才 exit 0）
```

## 文档索引

- `docs/workspace-p0p8.md` — 工作区 P0-P8 系统工程文档（需求、逻辑、架构、验证、使命判定）
- `docs/data-map.md` — 数据地图（每个数据单元的位置/生产者/血缘/消费者）
- `docs/directory-conventions.md` — 目录公约（data/ 五类判据、projects/ 归属、根收敛承诺）
- `docs/traceability-matrix.md` — 需求→逻辑功能→架构元素→验证证据 追踪矩阵
- `docs/archive-policy.md` — 归档政策（30 天 TTL、恢复程序、到期清理）
- `docs/pending-items.md` — 未决事项登记（不在清理范围、需后续排期）
- `docs/remote-cleanup-checklist.md` — 远端分支清理与首次 push 清单（用户执行）
- `docs/verification/` — S1-S5 各阶段验证证据（命令输出原文）

## 数据速览（详见 data-map.md）

| 资产 | 位置 | 规模 |
|---|---|---|
| 逐笔事实库 | `data/fact/tick_fact/` | 82G（orders/trades/snapshots/cancels） |
| 订单簿重建 | `data/fact/lob_fact/` | 113G（events/sweep_meta/checkpoints，250/250 交易日） |
| 分钟事实库 | `data/fact/bars_1m/` | 19G（80 个月） |
| 日线事实 | `data/fact/daily_fact/` | 435M（18.16M 行） |
| 原始下载 | `data/raw/quark_downloaded/` | 111G（74,630 zip） |
| ClickHouse | `127.0.0.1:8123` db=factorlab | tick_orders 5.81B 行等 8 表 |
