# R09-PERF-I1 spike：polars / expr_codegen 能力实测（2026-09-18）

只读实验，脚本与原始输出同目录：`spike_polars.py` + `output.txt`（polars 能力）、
`spike_codegen.py` + `codegen_output.txt`（codegen 行为）、`mutation.txt`（突变检验）。

运行（仓库根）：`platform/.venv/bin/python
governance/evidence/verification/R31/minute-perf/spike/spike_polars.py`（polars
1.44.1）。

## ① group_by.agg 上下文（脚本 ①a）

| 表达式 | 结果 |
|---|---|
| `sum/mean/min/max/first/last(col)` | OK（标量） |
| 算术/比较/条件（`x*2-y/3`、`when(...).sum()`） | OK |
| `filter` | OK |
| `when(col(O) == col(O).max()).then(x).otherwise(None).max()`（旧 day_last 同形） | OK |
| `sort_by(O).first()/last()` | OK 且与 maintain_order 口径逐 bit 一致 |
| `col(x).shift(k)` | **返回 List**（非标量聚合）→ 融合设计必须先把 shift/im_delay 物化为行级列，不能直接塞进 agg |

其余锚点（①b/①c）：`when(max).max` 聚合 == 旧 `over` 版 day_last/day_first
（bit）；sum/mean/max/min `over` == `group_by.agg`（同物理序，bit）。

## ③ 预排序 + over 去 order_by（脚本 ③）

对 rolling_mean/sum/max/min/std/median 与 shift 全族：乱序输入 `.over(P,
order_by=O)` 与预排序后 `.over(P)` 逐 bit 一致（含 null 掩码）——seq_* 物理序
变体成立的前提。

## ② 计时微基准（2.88M 行合成帧）

`over+order_by im4` 0.41s / `over no-order im4` 0.32s / `group_by.agg day4`
0.09s / `+join` 0.13s；`over+order_by day4（旧）` 0.16s。排序是 order_by 族
的主要税负（真实瓶颈在旧路径逐算子重复物化整组序列，见评审 §1①）。

## 关键发现：嵌套 over 的归约计划敏感（决定对拍口径）

`(r * im_delay(r,1)).sum().over(P)`（旧路径把 im_delay 内联进聚合）与
「先物化 `r*delay` 列再 `.sum().over(P)/group_by.agg`」在 f64 合成数据上
差 ~8e-22（≈1 ulp，见脚本/真实对拍）。逐层定位：

- `a_inline`（同层乘积）与 `b_sep`（位移列再乘）逐 bit 相等；
- 差异只出现在**聚合归约**：嵌套 over 在 sum 表达式内 vs 物化列后 sum，
  polars 采用不同归约顺序；
- `group_by.agg(嵌套 over)` 与旧 `(嵌套 over).sum().over(P)` 逐 bit 相等
  （真数据对拍前用合成验证）。

结论：融合路径「共享物化」在语义上等价、数值上仅末位 ulp；当 day_* 参数为
纯逐行表达式（无嵌套 over）时融合与旧路径 **bit-exact**。真数据对拍见
`../after/fold_parity.json`。

## expr_codegen 行为（spike_codegen.py）

- `codegen_exec` 返回帧只保留**非下划线**列（`main()` 末尾
  `select(~cs.starts_with("_"))`）→ 融合临时列命名不得带前导下划线；
- 顶层赋值必须**先定义后使用**（DAG 不重排源序）；
- 公式原文以字符串嵌入生成代码（import 别名生效）；
- 重复 im_* 子表达式由 sympy cse 在单次 codegen 内共享，跨 codegen 调用需
  融合层自行 CSE（`factorlab_cse_*` 临时列）。

## 突变检验（mutation.txt）

| 突变 | 结果 |
|---|---|
| `_aggregate_expr` day_sum 聚合 `+1`（错值） | `test_minute_fold.py` **7 failed**（bit/ulp 对拍必杀） |
| `try_fused → None`（优化器存根） | **2 failed**（路径非存根锁 + 乱序确定性锁） |
| CSE 关闭（重复 im_* 不再共享） | **1 failed**（CSE 只算一次锁） |
| 恢复 | 17 passed |

---

## R09-PERF-P4 spike（2026-09-19）：CH 查询设置 + 突变

- `spike_ch_read.py` + `ch_read_spike.json`：bars_1m 单条批读 SQL（真 CH、4852
  只 × 2024-01-02..01-12）10 变体 × 2 轮——`max_threads` 2..16、
  `max_block_size` 128k..1M 相对默认全在 3.1–3.6s 噪声带（服务器默认
  mt=40/bs=65409）→ 平台默认不变，仅提供 env 旋钮（见
  [`../after-p4/README.md`](../after-p4/README.md) §1）。
- `mutation_p4.sh` + `mutation-p4.txt`：5 处突变（预算门存根 / 并行走顺序 /
  settings 不注入 / 全局客户端回退 / 峰值常量清零）全部被杀；恢复后 52 passed。
