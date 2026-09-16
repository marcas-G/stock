# R30 终评修复波证据（Plan P，2026-09-17）

范围：终评 Important I1/I2/I3 + M8 文档顺手（设计 §4/§9 加注）。平台全量留给 controller。

| 文件 | 内容 |
|---|---|
| `00-mutations.sh` | 突变验证脚本（M1-M6；运行后逐字节还原源码） |
| `00-mutations.txt` | 红→绿原始输出：M1 I1 空源护栏删除 → 2 failed；M2 I2 env 忽略 → 1 failed；M3 I2 数据根第二份拼接 → 1 failed；M4 I3 存根恒 True → 8 failed；M5 I3 去关键列比较 → 2 failed；M6 还原后定向 27 passed、三方源码 cmp 全 OK |
| `02-tools-suite.txt` | `platform/.venv/bin/python -m pytest platform/tools -q` → **530 passed**（52s） |
| `03-gates.txt` | `make gates` → 唯一红 = **G-INDEX**（挖矿在途：untracked `research/factor/{intraday,vol_run_energy,volatility}` 未入索引；非本轮引入，controller/挖矿收口后 `make index`）；其余门全绿（含 G-TOPO/G-READ/G-LINT 196/0/G-REVIEWS） |
| `04-reconcile-new-tables-real.log` | 真 CH：`reconcile.py moneyflow` / `fundamentals` 各 rc=0，行数/日期/空值逐项一致 |
| `05-reconcile-all-real.log` | 真 CH：`reconcile.py`（all，14 表）rc=0，全库一致（33.5s；含 daily 恒等式/派生三表/bars 81 分区/tick 39 分区） |

修复点（commit 见根台账/进度）：

- **I1** `ingest_moneyflow.write`：0 行帧 → `ValueError("moneyflow: 空源拒绝灌入（拒绝清空 CH 表）")`，
  CREATE/TRUNCATE/INSERT 零调用（镜像 fundamentals 护栏）。测试
  `test_ingest_moneyflow.py::test_write_refuses_empty_frame_without_touching_ch`
  `::test_main_empty_root_refuses_and_leaves_ch_untouched`。
- **I2** `pan_update/config.py`：`repo_root()` respect `FACTORLAB_STOCK_ROOT`（与
  `core.factio.paths` 同 env 语义）；新增 `DATA_ROOT/RAW_ROOT/FACT_ROOT`，`Category.local_root`
  改由 `RAW_ROOT` 派生的绝对 Path；`cli.py`（STATE/LOCK/RAW_ROOT/_dest_root）与
  `parse_fundamentals_xlsx.py`（DEFAULT_SRC/FACT）全部走单点。测试
  `test_config.py::test_pan_update_and_ingest_share_data_roots`（in-process 根一致性守卫）
  `::test_data_roots_follow_stock_root_override`（子进程换根，config↔factio 同步迁移）。
- **I3** `ch_ingest/reconcile.py`：新增 `_check_moneyflow`/`_check_fundamentals`（行数/日期
  min-max/天数/关键列空值数/键 uniq/ts_code 异常，失败 exit≠0），`all|moneyflow|fundamentals`
  三入口；README 表与用法、Makefile 帮助同步。测试 `test_reconcile.py` 11 条
  （正常/缺口/空表/关键字段漂移/exit 码 × 两表）。
- **M8** `knowledge/design/workspace/2026-09-16-pan-data-update-design.md` §4 财报自动源=
  周更小 xlsx、§9 表名 `fundamentals`。

复现：

```bash
bash governance/evidence/verification/R30/final-fix/00-mutations.sh
platform/.venv/bin/python -m pytest platform/tools -q
make gates
platform/.venv/bin/python platform/tools/ch_ingest/reconcile.py
```
