# W5 全史批算与体积收口备忘（2026-09-10）

计划：`/home/gaolei/.claude/plans/crystalline-imagining-crab.md` W5 行 —— "全史批算 13 月
~74.5K code-day → `/data/students/gaolei/stock/lob_fact/`"，验收 = **断点续跑全完成；
月 QA 摘要；总体积 ≤1.5×源（或回退 tier 记录）；失败全分类；内存审计全程在限**。

收尾审计单点：`audit_w5.py`（四问 + 月 QA 段，只读；`--out` 落 JSON 证据）。

---

## 1. 体积预算真相：W4 记的 1.461× 实为 **1.5383×**（算术错，2026-08 月超预算）

W4 备忘（`w4_batch_memo.md` §5）写："月总 8,238.5MB（events 7,104.1 + sweeps 924.6 +
ckpts 209.8）vs 源 5,355.6MB = 1.461× ≤1.5× ✓"。**两处错**：

1. 这些数字的单位是 **MiB**（`du`/字节数 ÷2²⁰），却记成了 MB；
2. 比值算错：8238.5 / 5355.6 = **1.5383**，不是 1.461（1.461 = 按 2¹⁰ 混算得出）。

磁盘内容已核对（`lob_fact` 3 表 202608 月实际字节 vs 两次运行 summary 记录一致，
**0 字节 / 0 行差**）→ 仅记账错，不是数据问题。**结论：2026-08 月真实体积 1.5383× 超
1.5× 预算**，W4 "体积达标" 结论作废（W4e 提交里那条 1.461 需按本条更正）。

### 1b. 修正杠杆 = 纯编码（零语义变化），实测定参

对真实单日文件（20260803，events 41,331,666 行 / sweep 5,234,160 行 / ckpt 5,848,995 行）
流式重写实测（`/tmp/w5_bench2.py`，每行组单 chunk = 生产写盘形状）：

| 表 | 现盘 (RG1M/zstd3) | RG1M /**zstd9** | 增益 | RG4M/zstd9 |
|---|---|---|---|---|
| lob_events | 424.11 MiB (10.76 B/行) | 403.5 MiB | **−4.9%** | 无额外增益 |
| lob_sweep_meta | 46.84 MiB (9.38 B/行) | 45.4 MiB | **−3.1%** | 无额外增益 |
| lob_checkpoints | 11.95 MiB (2.14 B/行) | 11.2 MiB | **−5.9%** | 无额外增益 |
| （对照）zstd 2 | | +0.7% vs zstd3 | | |
| （对照）zstd 19 | | 仅再 −1~2%，写盘耗时 ×10 | | 弃 |

**定参：三表 RG 1M + zstd 9**（几何不变 → 与 W4d 的"组界 = 行数整倍"字节确定性不冲突）。

**在跑批中的实证（2026-09-10 13:34 实测）**——逐日文件 bytes/rows 全月均值，2025-08 全月
（补丁前出生，lv3）vs 2025-09 首批（补丁后新进程，lv9）：

| 表 | 202508 lv3 | 202509 lv9 | Δ |
|---|---|---|---|
| lob_events | 11.288 B/row | 10.644 B/row | **−5.7%** |
| lob_sweep_meta | 9.339 | 8.956 | −4.1% |
| lob_checkpoints | 2.205 | 2.174 | −1.4% |

跨月内容分布不同，此为"补丁确已生效于在跑批次"的定量佐证；**精确杠杆以 §2 重打包的
同文件前后字节为准**（同文件同内容，无跨月混杂）。

202608 月预算复算：7,104.1×0.951 + 924.6×0.969 + 209.8×0.941 = 6,756.0 + 896.0 + 197.4
= **7,849.4 MiB / 5,355.6 MiB = 1.4657× ≤ 1.5 ✓**（余量 2.3%）。

`run_lob_batch.py` 的 `ZSTD_LEVEL` 已 3→9（提交 `da18731`）；驱动脚本逐月起**新进程**，
故 2025-09 及以后的月份（批算进行中）直接出生在新几何上；2025-08 与 2026-08 两月
（跑在改动前 / 含 2026-08-07 重跑）由 §2 的重打包统一收口。

---

## 2. 体积修正落地：`compact_lob.py`（内容摘要 + 原子替换 + 单写者）

- **判据不是字节而是内容**：逐文件算"行数 + 批级 sha256 链"（Arrow IPC 规范化，去
  dictionary/去 metadata → 与行组几何、压缩级**无关**）；重写件摘要 == 原件摘要才
  `os.replace`，否则 `CompactError` 且原文件与 tmp 都不动。
- **判据实现要点（真实误报 → 修复，提交 `cff8a6a`）**：`lob_events` 20260803 首轮
  重打包被**正确拒绝**（行数全等、digest 不等）→ 逐列/逐批定位到 `id` 列 **692 个
  null 槽的底层残留**（源文件 = 243,683，重写件 = 0）。Arrow 语义上 null 槽的底层值
  **未定义**，读回随编码器/路径变 → 原始 IPC 字节不可作等价判据。修法：
  `_canon_batch` = IPC(逻辑值，null 槽填类型零值) **⊕** IPC(null 掩码 int8) ——
  既免疫未定义残留，又保持"null ≠ 零值"（掩码变则判据变）。复验：events
  424.1 → 403.5 MiB（−4.9%）、三表摘要逐文件全等、exit 0。
- **单写者纪律**：与批算共享 `_batch/.lock` 的独占 flock；批算持锁期间调用 → 退出码 3。
- **重写路径确定性实证**：同一源文件用两种读入策略（按 rgr 大小读批 / 262K 批累加后
  `combine_chunks`）重写 → **逐字节相同**（两次独立运行）。
- **重写件 vs 生产写盘件的字节差**（同 rgr/同 level，内容摘要全等）：

  | 表 | 生产件 | 零偏移重写 | Δ |
  |---|---|---|---|
  | lob_sweep_meta | 49,112,951 B | 49,112,047 B | −904 B（−0.002%） |
  | lob_checkpoints | 12,527,148 B | 12,525,114 B | −2,034 B（−0.02%） |
  | lob_events | 444,709,080 B | 443,747,546 B | −961,534 B（−0.23%） |

  逐列定位：events 的差**全部集中在单调 `seq` 列**（disk 55.387 → 重写 54.546 MiB，
  −1.5%），其余 12 列 |Δ| ≤ 0.05 MiB。原因 = 编码器页/字典边界对"写入视图偏移"敏感
  （生产写盘的行组是**引擎逐 code 帧缓冲内的偏移视图**，其偏移模式取决于帧界，不可能
  由内容反推；实证：零偏移视图 −0.002%、大表偏移视图 +0.003% 分别落在原文件两侧）。
  **故交付树的复现性判据 = 内容摘要（编码无关）+ 行数，而非逐字节全等**；批内重跑的
  字节全等性由 202608 两次运行 `parity.ok=True / mismatch_dates=[]` 单独证明（均在
  重打包前）。

---

## 3. 日结账修订：硬失败日不吞、也不死锁（W5 实证）

W4 口径下 `done` 只收"整日全 code-day 过门"的日期 → 确定性门拒日（如
`301308.SZ@20260807` presence=0.89981 < 0.90 地板）**永远不进 done** → 该月永不完成
（重跑字节级相同，纯烧机时）。W5 修订三态（`day_outcome` / `apply_day_result`）：

| 日结 | 条件 | 落账 |
|---|---|---|
| `ok` | 全 code-day 过门 | 进 `done` |
| `hard` | 数据**已落盘**，存在已分类 code-day 门拒 | 进 `done` + `hard_days` 留痕（day/codes/reasons），交审计按 ≤0.1% 阈值裁决 |
| `error` | 结构性（守卫漂移 / 无 manifest code / 异常 / 无落盘载荷） | **不进** `done`，下轮重试 |

`plan_n` 恒为完整月度计划数（不因过滤缩水）；`month_complete` = 计划日全 done ∧ 无
`error` ∧ 月门 ok。202608 月实测：`hard 1/4500 = 0.0222% ≤ 0.1%`（唯一 hard =
`301308.SZ@20260807 reasons=['m1a_presence']`），分类全量 = `{'clean': 4073,
'delta_band': 426, 'vacuous': 0, 'hard': 1}`（无未归类桶）。

### 3b. 该 hard 例的定量根因 = δ 滞后定律（不是引擎缺陷，是方法边界）

`301308.SZ@20260807`：611,971 事件/日 ≈ **425 事件/s**（超活跃）。该日锚定水平缺失
时，其随后 add 的 Δt 中位数 ≈ 700ms、p90 ≈ 1,330ms，**> 固定 500ms 吸收窗** →
约 10% 锚定水平落在窗外（presence 0.89981 = 窗内可得性）。对照证据：① 缺失价
**100%** 在当日有该精确价格的 add 消息（无结构性类别）；② 索引分布偏向最好档、且
**双方对称**；③ 健康日 70/4,731 缺失全部在索引 0 且 ≤500ms 窗口内（p50 80ms）；
④ 约 70–80% 的缺失在 1–3 个锚点内自愈。→ 归因 = 高消息速率下的 δ 增长，非重建错误
（W6 M7 同位门反向印证：盘口因子两路独立实现全等）。

---

## 4. 月 QA 摘要（审计工具 `month_qa_rows`）

逐月一行：月门文件存在性 / 代码日 / 锚定 / vacuous / δ 带 / 池化 SZ·SH 命中 / done·plan /
hard 日数 / 最近一次 run 的 parity / error 数。缺件显式 `None + qa_present=False`
（不静默补零）。示例（202608，收口前的当时值）：

```
202608: cd=4499 锚定=4499 vac=0 band=426 SZ=0.98602 SH=0.99175 done=14/15 hard=0 parity=True err=1
```

**202508（首个断点续跑月，2026-09-10 13:14 收月）**：

```
202508: cd=4101 锚定=4101 vac=0 band=316 SZ=0.98904 SH=0.99066 done=14/14 hard=0 parity=None err=0
```

**已收月逐行（2026-09-11 01:41 预跑审计真实输出，`/tmp/w5_audit_pre.json`；批算仍在
跑 → 该次 `ok=False` 属预期，仅取 `[月 QA]`/`[体积]`/`[失败]` 三段的已完成部分）**：

```
202508: cd=4101 锚定=4101 vac=0 band=316 SZ=0.98904 SH=0.99066 done=14/14 hard=0 parity=None err=0
202509: cd=6445 锚定=6445 vac=0 band=590 SZ=0.98808 SH=0.99064 done=22/22 hard=1 parity=None err=0
202510: cd=5017 锚定=5017 vac=0 band=371 SZ=0.98888 SH=0.99084 done=17/17 hard=1 parity=None err=0
202511: cd=5920 锚定=5920 vac=0 band=388 SZ=0.99023 SH=0.99098 done=20/20 hard=0 parity=None err=0
202512: cd=6824 锚定=6824 vac=0 band=476 SZ=0.99021 SH=0.99093 done=23/23 hard=0 parity=None err=0
202608: cd=4499 锚定=4499 vac=0 band=426 SZ=0.98602 SH=0.99175 done=14/15 hard=0 parity=True err=1
```

同次 `[失败]` = hard 3/32,809 = **0.0091%**（阈 ≤0.1%），三条全为 `301308.SZ` 的
`m1a_presence`（20250922 / 20251030 / 20260807，§3b δ 滞后定律同因）；分类分布
`{clean: 30,239, delta_band: 2,567, vacuous: 0, hard: 3}` —— **无未归类桶**。

`month_gate_202508.json` 的 `n_code_day/n_anchored/per_day(全 True)/SZ/SH` 与 driver 日志
逐字一致；该 run 单跑全 14 日，`parity=None` = 本月无更早 run 可比（parity 仅 `--force`
重跑月产生，语义非"未校验"）。该月单 run 跑完 14 日，**未**触及跨 run 月门聚合；
该路径（月收尾扫描 `runs/*/day_rows.jsonl` 全量再按月过滤，见 `run_lob_batch.py`
L975-985）目前仅有代码级依据，其真实多 run 首次演练 = 202608 的 20260807 补跑
（收口时实证并回填）。

---

## 5. 内存审计（用户硬约束，2026-09-11 翻倍：常态 ≤48GB / 总驻留 ≤64GB；原 24/32GB）

来源 = 各 run 的 `rss_audit.csv`（30s 采样，tag ∈ worker|parent）。全史批算分两段：

**第一段（2 worker：W4 试点 + 2025-08 … 2026-01 部分；旧门 24/32GB）** —— 8 runs /
2,425 时间点 / 16 worker 进程（截至 2026-09-11 01:30 实测）：

| 指标 | 平均 | 中位 | 峰 | 门 | 判 |
|---|---|---|---|---|---|
| 单 worker | 8.50 GB | 8.40 | 12.8 GB（单采样） | — | — |
| 同刻 worker 和 | 17.06 GB | 17.32 | 23.92 GB | ≤24 GB | PASS |
| 同刻全进程和 | 17.21 GB | 17.47 | 24.07 GB | ≤32 GB | PASS（硬） |

2 worker 是 24GB 常态门的**上限解**（3 worker 投影 27–38GB 破门）→ 该段按 2 worker/月
串行、nice 19。

**第二段（4 worker：2026-01 从头重跑起；新门 48/64GB）**：用户 2026-09-11 决策内存
预算翻倍 → `--workers 4`（40 核机、负载 ~11；4 × ~8.5GB ≈ 34GB 常态、投影峰
~51GB ≤64GB 硬门）。**额度翻倍 ≠ 实测翻倍**——4-worker 段实测峰由收口审计回填。
配套：派发低水位 `LOW_WATER_KB` 8→16GB（4 worker 同刻在飞时单 date 切片峰 ~12.8GB，
旧水位会"在飞 × 新增"过冲），冻结测试
`tests/test_run_lob_batch.py::test_resource_gates_frozen_for_doubled_budget`。

**已知卫生问题（已清理，收口复查）**：观察到 14 个 `PPID=1` 的空闲 spawn worker 泄留
（各 0.1–0.2GB，来自已结束 run 的 executor 重建/终止路径；不持锁不干活），已按 PID
清理；收口时 `ps -eo pid,ppid,cmd | awk '$2==1'` 复查。

---

## 6. 收口 runbook 与验收表

**可执行形态 = `tools/lob_fact/w5_closure.sh`**（a–e 加前置守卫：driver 日志无
ALL DONE / 存在 `run_lob_batch` 进程 / 磁盘守护暂停中 → 拒绝执行（exit 2/3/4，无副作用）；
日志 `/tmp/w5_closure.log`；已用真实环境演练守卫路径 exit 2）。

**收口顺序（严格；a/b/c 必须等 driver 全退 —— 锁单写者纪律，compact 前先确认无
`run_lob_batch` 进程）**

```bash
cd /data/students/gaolei/stock/quant-platform-research/tools/lob_fact
PY=/data/students/gaolei/anaconda3/envs/emb/bin/python
# a. 202608 补跑 (todo={20260807} → hard 入账 done 15/15; 跨 run 月门聚合首次真实演练)
nice -n 19 $PY run_lob_batch.py --month 202608 --workers 1
# b. 断点证据 (应打印 plan=15 done=15 todo=0)
$PY run_lob_batch.py --month 202608 --dry-run
# c. 体积收口 202508+202608 → zstd9 (dry-run 预核: 87 文件 / 14,720.2 MiB —— 2026-09-10
#    22:13 与 2026-09-11 01:47 两次独立预核逐字一致; 单写者守卫已在真实持锁状态下实测:
#    批算持锁时调用 → 打印拒绝 + **exit 3**, 不触碰任何文件)
nice -n 19 $PY compact_lob.py --months 202508,202608 --rgr 1048576 --level 9 \
    --lock /data/students/gaolei/stock/lob_fact/_batch/.lock --out /tmp/w5_compact.json
# d. 终审四问 (exit 0 = 全 PASS)
$PY audit_w5.py --out /tmp/w5_audit.json
```

**补跑前置（2026-09-11 已固化）**：20260807 三表在 lv9 重写**前**的内容摘要已存
`/tmp/w5_20260807_pre.json`（`compact_lob.file_digest`：与编码无关的 sha256 链）：

| 表 | 行数 | lv3 字节 | digest 前缀 |
|---|---|---|---|
| lob_events | 49,222,853 | 527,062,390 | `b2a101ca2ad5d01c…` |
| lob_sweep_meta | 6,026,583 | 58,289,020 | `80c4da7a9e0c5460…` |
| lob_checkpoints | 6,218,148 | 15,112,211 | `95a2be5d43d407e3…` |

**该补跑的 parity 语义（勿误读）**：补跑是 202608 月的第二个 run，`prev` = W4 的
`20260910_082834_33436`，20260807 三表将被 lv9 重写 → **跨压缩级字节不可比 →
`parity.ok=False` 是预期的**（不是数据回归）。真实等价判据 = 上表内容摘要重算全等
（编码无关；收口时回填）——W4 的 "字节级重跑全等" 证明限定在 lv3 同编码内，仍成立。

**驱动脚本（月间串行；原件 `/tmp/w5_run_months.sh` 易失，此处为权威副本 —— worker 数
分两段：2025-08…2025-12 为 `--workers 2`（旧 24/32GB 门），2026-01 起 `--workers 4`
（2026-09-11 翻倍门）；逐月起新进程 → 补丁/参数变更只需发生在月界）**

```bash
#!/bin/bash
cd /data/students/gaolei/stock/quant-platform-research/tools/lob_fact
PY=/data/students/gaolei/anaconda3/envs/emb/bin/python
for m in 202508 202509 202510 202511 202512 202601 202602 202603 202604 202605 202606 202607; do
  echo "=== $(date '+%F %T') month $m start"
  nice -n 19 $PY run_lob_batch.py --month $m --workers 4 >> /tmp/w5_batch_all.log 2>&1
  echo "=== $(date '+%F %T') month $m exit=$?"
  sleep 20
done
echo "=== ALL DONE $(date '+%F %T')"
```

驱动自身 stdout → `/tmp/w5_driver.log`（`month $m exit=$?` 行；批算子进程输出 →
`/tmp/w5_batch_all.log`）——**等待 ALL DONE 以 driver 日志为准，勿只看批算日志**。

**验收表（收口后回填）**

| 项 | 证据 | 结果 |
|---|---|---|
| 断点续跑全完成 | `audit_w5 --out` 完整性段（逐月 done/plan） | 待回填 |
| 月 QA 摘要 | 同报告 `[月 QA]` 段（13 月逐行） | 待回填 |
| 总体积 ≤1.5×源 | 同报告 `[体积]` 段（逐月比；202608 收口前后对比） | 待回填 |
| 失败全分类 | 同报告 `[失败]` 段（clean/delta_band/vacuous/hard + 逐条 hard） | 待回填 |
| 内存全程在限 | 同报告 `[内存]` 段（三指标 vs 24/32GB 门） | 待回填 |
| 复现命令 | 本 runbook a-d + `/tmp/w5_audit.json` | — |
| 提交号 | research / main 两侧 | 待回填 |
