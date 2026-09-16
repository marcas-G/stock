# 读路径双后端（duckdb|ch）与 bars_1m/tick 读接口 — 设计规格

日期：2026-09-06。配套计划：`docs/superpowers/plans/2026-09-06-factorlab-dual-backend-read-path.md`。
状态：已实现并双腿全量验收（见文末验证记录）。

## 背景与决策

上游 `marcas-G/quant-platform` main（5095a3d）是 **DuckDB 版**平台（M6–M8 已叠加）；
本机无 factorlab.duckdb 平台库，ClickHouse 是唯一完整数据源（daily 层 18.16M 行 +
trade_cal + stock_basic；tick ~14B + bars_1m 1.85B 行）。

**决策（2026-09-03）**：读路径 **duckdb|ch 双后端可切换**，不做纯 CH 迁移——
duckdb 腿即改造前行为的回归网（硬门槛：duckdb 腿全部旧测试保持通过）；
M8 执行层一并双后端化；新增 bars_1m/tick 读接口（仅 ch 有数据）。

## 架构：三层读路径

```
读函数(公开 API 单写：共享 polars/校验 ~90% + 一行 _IMPL[rd.backend].xxx)
  → Rd 句柄层 factorlab/data/backend.py：执行 + 目录探测 + 连接语义
  → 编译函数对（数据模块内 _xxx_duckdb / _xxx_ch）：SQL 文本 + 参数 + 1-3 行 decode
```

读函数体内无 if/else。职责边界：

- **句柄层**只收编：目录探测（`tables()/columns()`：duckdb information_schema ↔ ch
  system.tables/columns）、连接 pragma（duckdb 打开即 SET memory_limit/threads，
  收敛历史 8 处逐函数 SET）、连接错误语义（duckdb 文件缺失 → `FileNotFoundError`；
  CH 不可达 → `RuntimeError`）。不做 SQL 方言翻译。
- **SQL 级方言差异全在编译函数对**（参数形态随腿：duckdb 位置 `?`，ch 命名
  `%(name)s`，`query_df/query_rows` 对两种形态透明）。
- `Rd` 基类可 `isinstance` 判型（M8 链的类型门收这里；`isinstance(Path)` 旧门已废）。
- `open_read(data_backend=None, db_path=None)`：None → `settings.data_backend`；
  duckdb 用 db_path（默认 platform_db），ch 忽略 db_path。

### ch 方言要点（活体 CH 26.3.22.7 实测，各要点有双腿测试锁定）

1. `argMax(x, d)` 原生跳过 NULL 行 = duckdb last-FILTER（单键排序即可；全 NULL 组 → NULL 一致）。
2. daily code 过滤用两层 IN：`d.ts_code IN (SELECT ts_code FROM {db}.stock_basic
   WHERE symbol IN (...))`——前提 code 恒来自 stock_basic；孤儿 daily 行 ch 不命中
   （duckdb 前缀匹配命中），编译函数 docstring 标注。
3. date×code 骨架：`arrayJoin(arrayMap(x -> toDate(x), %(dates)s))` CROSS JOIN
   `arrayJoin(%(codes)s)`（arrayJoin 不能作 FROM 层 table function，26.3 UNKNOWN_FUNCTION）。
4. 空表 `min(Date)` = 1970-01-01 哨兵 → 调用点判空（duckdb 侧 min 即 NULL）。
5. Date 列 decode 为 'YYYYMMDD' 字符串/pl.Date，保持下游管线不变；Date 列做
   toYYYYMMDD 格式化必须输给 Date 类型（`toString(toYYYYMMDD(b.list_date))`）。
6. DateTime64(3) 值按**源 wall 时钟 epoch** 写入（无偏移）；arrow 读回被标注服务器
   会话时区（+08：墙钟 +8h、epoch 不变）→ `convert_time_zone("UTC")` 取回 epoch 的
   UTC 墙钟表达（= 源 wall），再 `replace_time_zone(None)` 剥 → naive ms。
7. **join_use_nulls 强制 = 1**（见下）。
8. **未别名限定列 + 参数绑定 + GROUP BY → CH 返回点式表头 `d.ts_code`**（duckdb
   去前缀）——三条件同时才触发（实测二分）→ 必须显式 `AS ts_code`。
9. 命名参数 %-绑定与 SQL 字面冲突：`formatDateTime('%Y%m%d')` 不可用
   （unsupported format character）→ toYYYYMMDD 族。

### join_use_nulls=1（客户端读路径强制）

CH 服务器（26.3）默认 `join_use_nulls=0`：LEFT JOIN 未匹配行填**类型默认值**
（String→`''`、Date→1970-01-01）而非 NULL——与 duckdb 恒 NULL-extension 不一致。
PIT 骨架 `is_st = s.ts_code IS NOT NULL`、未匹配 code 的 `b.ts_code/list_date`
NULL 形态都因此失真（ch 腿 5 失败，单根因，88/88 修复验证）。

**决定**：`ch_source.query_df/query_rows` 统一传 `settings={"join_use_nulls": 1}`
（每查询，客户端侧，不依赖服务器配置；只影响本会话该查询）。修复入 src
（bug#4）而非测试；验证：修复前后 pit_universe ch 腿 5 失败 → 88/88。

## 签名迁移约定

- 公开读函数参数一律 `rd`（无 `Path|Rd` 薄兼容层；`db_path`/连接位置参数废弃）。
- `RunContext` 保留 db_path，新增 `data_backend: Literal["duckdb","ch"]|None=None`
  （None → settings.data_backend）。
- `ProcessCtx.db` 即读句柄（Rd）；processors 三个取数点（fillna industry_mean /
  neutralize industry / neutralize size）拆私有 `_fetch_industry(rd)` /
  `_fetch_mv_slice(rd, ...)`，同样编译函数对组织。
- `settings`：`data_backend`（默认 "duckdb"，`FACTORLAB_DATA_BACKEND` 覆盖）+
  `ch_host/port/user/password/database`（`FACTORLAB_CH_*`）。

## bars_1m/tick 读接口（factorlab/data/intraday.py）

分钟/逐笔数据**仅 ClickHouse**（duckdb 平台文件无 intraday 表）→ duckdb 后端
编译函数不实现，读接口显式 `ValueError("bars_1m/tick 数据仅 ClickHouse 后端提供")`。

- `load_bars_1m / load_tick_trades / load_tick_orders / load_tick_snapshots(rd, code, *, day=None, date_start=None, date_end=None, cols=None)`
- code：6 位纯数字经 `stock_basic.symbol` 唯一解析（未知 → ValueError）或带后缀
  ts_code 直通；输出 code 一律 6 位（与 daily 一致）。
- 时间窗：`day` 快捷（= 闭区间单日）或 `date_start/date_end` 闭区间（单边可开）；
  **完全不限制 → ValueError（防全表扫描）**。
- `cols` 白名单（顺序即输出顺序）；snapshots 默认投影排除 10 档盘口与指数统计列
  （66 列镜像生产 DDL）。
- 排序：bars_1m datetime；tick (time_ms, 序号列)。
- 解码：datetime naive Asia/Shanghai 墙钟 ms；tick 价格 price_x10000 Int32 原样；
  空结果返回同投影空 frame。
- 生产 e2e：行数 = 直连 CH count 对拍 + 交易带/单调性验证（integration 标记，
  无 CH 自动 skip）。

## 测试策略

- `tests/dualbridge.py`：kind 映射表（str/str?/date/f64/f64?/i64/i32/f32/
  datetime(DateTime64(3))/u8/u16/u32/u64）+ 双腿 seeders（**幂等**：DROP TABLE IF
  EXISTS + CREATE；ch 侧 SYNC），测试体只给数据描述。
- conftest：`env`（params=["duckdb","ch"]；duckdb 临时文件每测试新建；ch 临时库
  factorlab_test_<uuid> + monkeypatch ch_database + DROP SYNC teardown；CH 不可达 →
  整腿 skip）；`ch_client`/`ch_db`/`ch_prod` fixtures。
- duckdb 文件语义测试（read_only 冲突、FileNotFoundError、锁）留 duckdb 单腿；
  CLI 测试走 duckdb 腿（platform_db 文件 monkeypatch，双后端化后自建 seed helper）。
- 每源层修复（bug#3/#4 等）先测后修，双腿锁定，禁止"测试迁就实现"。
- 回归硬门槛：duckdb 腿 = 基线 1842 passed/13 skipped；ch 腿同文件等值。

## 验证记录（2026-09-06 收尾）

- M2 十文件双腿全绿（含 pit_universe 88/88、join_use_nulls 修复验证）。
- 已转换文件批量回归：467 passed（9 文件 × 双腿 + intraday 单腿）。
- intraday 生产 e2e：bars_1m 600519.SH 2026-08-21 240 行（首 09:25:00 末 15:00:00）；
  tick 三表行数与 CH count 一致、time_ms 在 09:15–15:05 交易带内。
- 全量双腿 pytest（duckdb 腿=基线不回归；ch 腿等值；integration 真库 e2e）见计划文档
  验收记录。
- lint：ruff 不可用（镜像内无二进制）；black-default 亦非本仓基准（HEAD
  data/source.py 同样 would-reformat，上游数据层含长单行 tuple 风格）→ 未引入
  black 全量重排（41 文件 would-reformat 纯噪音）。静态自检以 AST parse + 全量
  pytest 收集为准；新代码风格跟随周边文件。
