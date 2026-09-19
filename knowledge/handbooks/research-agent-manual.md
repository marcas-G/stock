# 研究员一页手册（flab）

> 入口：`flab`（等价 `factorlab research`，固定 ch 后端）。stdout 永远**一个 JSON**，
> 日志走 stderr。自描述唯一权威：`flab describe --json`（命令/参数/默认值/错误码/示例）。
> 结果目录 `runs/platform`（策略 `runs/platform/strategies`）；>200 行自动落
> `runs/research/<cmd>/<ts>/data.parquet`（回 path+head+schema；`--limit/--inline/--out` 覆盖）。
> 重命令 `factor run` / `strategy run` / `study run` 自动过 heavy 闸（2 槽 + 8GB 内存预检 +
> 线程/nice 注入）：闸满 → `BUSY`（加 `--wait` 阻塞），内存不足 → `MEMORY_GUARD`。

## 10 条常用

1. `flab health` —— 连通/内存/磁盘/闸槽/新鲜度一览；任何异常先看它。
2. `flab data daily --codes 600000.SH --start 2026-09-01 --end 2026-09-10` —— 日K（`--view qfq|hfq|pit_qfq` 复权）。
3. `flab data tables` / `flab data status` —— 表清单（行数）× 新鲜度+交易日缺口。
4. `flab factor lint $QUANTRESEARCH_ROOT/factor/<族>/<名>.yaml` —— 秒级静态校验（写完 spec 先跑）。
5. `flab factor run $QUANTRESEARCH_ROOT/factor/<族>/<名>.yaml` —— 计算+评估+分层回测（过闸；返回 IC/十分位/换手/覆盖）。
6. `flab factor admit $QUANTRESEARCH_ROOT/factor/<族>/<名>.yaml` —— 一键入库检验：lint→(缺产物则 run)→参考库 corr+resic→`verdict: 可加入|冗余|重复`。
7. `flab factor resic <name> --against reference` / `flab factor corr <a> <b>` —— 增量信息 / 两两相关。
8. `flab strategy run $QUANTRESEARCH_ROOT/strategy/<名>.yaml` —— 信号→组合→执行回测→持久化（过闸）。
9. `flab report url <因子名>` —— 报告静态 URL（不启服务）；`flab report serve` 启只读 Web。
10. `flab study run <factor.yaml> --strategy $QUANTRESEARCH_ROOT/strategy/<名>.yaml` —— 一条链：因子 run → admit → 策略回测（该因子）→ 报告 URL，返回全部产物路径。

## 其余命令（按组，`flab describe --json` 看全量）

- data：`flab data minute`（仅 ch）、`flab data tick`、`flab data calendar`、
  `flab data daily_basic`、`flab data adj`、`flab data limit`、`flab data stock_basic`、
  `flab data universe`、`flab data moneyflow`、`flab data sector`、`flab data members`、
  `flab data fundamentals`、`flab data schema`。
- factor：`flab factor list`、`flab factor show`、`flab factor export`、`flab factor svd`、
  `flab factor ref list`、`flab factor op list`、`flab factor op doc`、`flab factor catalog`。
- strategy：`flab strategy lint`、`flab strategy list`、`flab strategy show`、
  `flab strategy export`、`flab strategy capacity`、`flab strategy cost`。
- report/study/通用：`flab report list`、`flab report show`、`flab study list`、
  `flab version`、`flab describe`。

## 性能开关（分钟链）

- `flab factor run <spec> --chunk-workers 2` —— chunk 并行（默认 1=顺序；8GB 护栏下上限 2，超预算闸前拒绝）。
- `flab factor run <spec> --profile` —— 分段计时（read_data/bars_read/fold/label/evaluate/persist 墙钟+峰值 RSS）→ stderr 与 `summary.runtime.profile`。
- 读缓存：分钟链 bars_1m 同窗第二次起命中（免 CH 重读）；数据回填/新数据自动失效（源指纹）；`flab factor run <spec> --no-read-cache` 关闭。
- 缓存状态：`flab health` 的 `read_cache` 段（dir/entries/size_bytes/hits/misses/fallbacks）。

## 错误处理

- 错误信封 `{"ok": false, "error": {code, message, hint, log}}`；先用 `hint` 修，再看 `log`。
- 退出码：USAGE=2 LINT=3 MEMORY_GUARD=4 DEAD_SIGNAL=5 RUN_FAILED=6
  STRATEGY_FAILED=7 DATA=8 NOT_FOUND=9 INTERNAL=10 BUSY=11。
- 失败不静默：step 失败保留已完成产物；`flab study list` 可见失败历史。
