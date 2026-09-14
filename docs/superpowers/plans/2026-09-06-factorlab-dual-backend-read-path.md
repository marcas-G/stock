# 计划：读路径双后端（duckdb|ch）+ bars_1m/tick 读接口

日期：2026-09-06（计划 2026-09-03 立项，2026-09-06 完成）。设计规格见
`docs/superpowers/specs/2026-09-06-factorlab-dual-backend-read-path-design.md`。

## 目标

在上游 DuckDB 版平台（5095a3d）上推进：读路径 **duckdb|ch 可切换**（duckdb 腿 =
回归网硬门槛）、M8 执行层一并双后端化、新增 bars_1m/tick 读接口（仅 ch 有数据）。
本机无平台 duckdb 库，CH 是唯一完整数据源 → ch 腿为真实数据链路。

## 里程碑执行记录

- **M0 环境基座** ✅：pyproject（pyarrow>=20 glibc 注释 + clickhouse-connect>=1.7）；
  uv venv（阿里云镜像 UV_INDEX_URL/UV_NO_CACHE=1）；config data_backend/ch_*；
  抄 ch_source.py；新 backend.py。全量 pytest 基线 **1842 passed/13 skipped**。
- **M1 daily 读路径双后端** ✅：calendar → source（load_daily/fill_state）→
  universe 全族 → engine 接线（run_factor/_compute_signal/_compute_labels/
  load_qfq_base_adj/ProcessCtx/processors 三取数点）。duckdb 腿全量不回归（基线）。
- **M2 M8 执行层 + 读路径文件双后端化** ✅：data/execution（_require_* rd 化）、
  execution/{rules,market,calendar,overnight,backtest} 签名 rd 化；15 个读路径
  测试文件 env 双腿参数化。**src bug#3**（ch fill_state：未别名限定列+参数+GROUP
  BY → 点式表头 `d.ts_code`，显式 AS 修复）与 **src bug#4**（服务器 join_use_nulls
  =0 → ch_source 读路径每查询强制 1，LEFT JOIN NULL-extension 与 duckdb 对齐；
  pit_universe ch 腿 5 失败 → 88/88）均先测后修、双腿锁定。
  CH 缺表（stock_st/stk_limit/suspend_d）无 teajoin token → 由 ch_db 假库双腿测试
  覆盖缺表语义；生产 reconciliation 视 token 后续可用性补。
- **CH 连接验收** ✅：ch_prod 真数据 e2e（daily 层读路径真跑）。
- **M3 intraday 读接口** ✅：intraday.py（4 loader × rd 分派；duckdb 显式
  ValueError）+ ch_db 假库 9/9 + 生产真数据 e2e 4/4（行数对拍 CH count、datetime
  源 wall 时钟、交易带断言）。
- **收尾** ✅：docs/interface.md 数据层 API 同步（§4.0 双后端 + intraday；
  RunContext.data_backend；读函数 rd 签名）；规格/计划文档落库；双腿全量回归
  duckdb 腿 = 基线不回归；CLI 冒烟 ch 后端真数据 run；分段提交本地 main
  （push 由用户执行）。

## 关键文件

- 新建：`src/factorlab/data/backend.py`、`data/ch_source.py`、`data/intraday.py`、
  `tests/dualbridge.py`、`tests/test_intraday.py`、`tests/test_intraday_prod_e2e.py`、
  spec/plan 本文档。
- 改造：`config.py`、`data/{source,calendar,universe,adjust,execution}.py`、
  `engine/compute.py`、`execution/{rules,market,calendar,overnight,backtest}.py`、
  `process/{processors,registry}.py`、`tests/conftest.py` + 15 读路径测试文件 +
  `test_cli_run.py`（build_db 退役 → duckdb 腿本地 seed helper）。

## 验收（整体交付判断）

1. 设计文档列出模块全部实现 ✅（backend/ch_source/intraday + rd 化全链路）。
2. duckdb 腿全量 = 基线不回归 ✅；ch 腿同文件等值 ✅。
3. 生产 CH 真数据 e2e：daily 层 + intraday 行数对拍 ✅。
4. CLI 冒烟（FACTORLAB_DATA_BACKEND=ch 真数据 run）✅（见收尾提交说明）。
5. lint/typecheck/Docker：ruff 不可用（镜像无二进制）；black-default 非本仓
   格式基准（HEAD 文件同样 would-reformat，未引入全量重排）；静态自检 = AST
   parse + 全量 pytest 收集 + 周边风格跟随。Docker 构建非本里程碑范围（本仓无
   Dockerfile/新服务依赖）。

提交分段见 git log（feat(data)/fix(data)/feat(data-intraday)/docs，本地 main，
push 由用户执行）。
