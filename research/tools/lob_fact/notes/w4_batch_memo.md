# W4 批算与试点月备忘（2026-09-10）

试点月 2026-08 全量（v2 配置；15 trading dates × 300 codes）。门/体积/RSS/重跑比对
结果见 §5 验收表（run 完成后填）。本节定标与机制证据已冻结。

## 1. 体积预算解冻（W4 实测推翻 W2 模型占位，勿当"违反预算"复述）

W2 冻结的体积模型（w2_engine_measure.md L22）是 **dict 内存行宽上界占位**：
`estMB = rows×96 + sweeps×88`，且原文明确"parquet 整数列物化实值 W4 试点月实测，
预算 ≤1.5×源" —— 1.5× 从不是已校准事实：模型 96B/行若按事件行数全量套用
= 41.3M×96 ≈ 4GB/日 ≈ 11×源/日，占位数字从不支持 1.5× 落地，W4 实测才是契约。

**实测（20260803 events，41331666 行）**：
- 首版逐 code 行组流式写（每 code-day 一组，~140K 行/组）：**499.6MB = 12.1B/行**
  （499.6MB 由"逐 code 组复写"实验精确复现 —— 非读取差异）
- 行组几何才是主杠杆：**1,048,576 行组 = 436.1MB（−12.7%）**（zstd 上下文/dict
  摊销；组界不必对齐 code）
- 压缩级 1→3：再 −2.8%（合计 −14.9%，体积主因是行组）
- 实测否定项：int32 化全 7 整列 **反增**（12.9B/行 —— zstd 对 int64 流建模更优、
  int32 打断跨值冗余）；delta 编码 int 列零增益（zstd 已近熵限）；两所全深度行数
  级结构（events ≈ 1.0× 输入消息行）不可削

**修正落地（commit 5baf7c5，TDD 红→绿 123 全绿）**：`_TableStream` 缓冲语义 —
帧缓冲至 `ROW_GROUP_ROWS=1,048,576` 行整倍逐组落（组界=行数整倍、与帧界无关 →
单 writer 重跑字节全等不变量保持），余数回存/收尾尾组；`ZSTD_LEVEL=3`。内存上界
≈ 阈值 + 单帧。

**试点月前 2 日实测（v2）**：events 424.1 MiB / 433.9 MiB（20260803/04）vs 旧
499.0/506.3 MiB → **−15.0% / −14.3%**；rows 52.52M / 52.41M 与 v1 逐日全同
（引擎语义未动）。月投影 ≈ 8.0GB vs 源 5,355.6MB → **≈1.49×，命中 ≤1.5× 预算**
（精确值见 §5；20260805/06 起后续日期继续同口径）。

## 1b. worker 数修订（实测定标取代 W2 估算）

W2 估算 '≤1.5-2.5GB/worker → 6-8 worker'（w3 memo L141）被实测推翻：真日
worker RSS 平台 7.5-10.9GB、**单 worker 峰 12.4GB**（重日 65.9M 行 date 切片）。
2 worker 同刻和峰 **22.7GB** ≤ 24GB normal ✓；3 worker 投影 >32GB hard ✗ →
**W5 冻结 2 worker/月，月间串行**（并行两个月 = 45GB ✗）。

## 2. 日门双阶化（GATE_FLOOR=0.90，4d7e0c3 已记）与 δ 带计数

真实数据 m1a_presence 呈现 fast-name 系统性 δ 滞后（0.913-0.970，与 W1 校准集外
0.9862-0.9999 同因）：per-day 池化 0.97 门在真日上误杀合法日。门重构为两阶：
GATE_PRES=0.97 承担池化存现门（sz/sh/per_day 池）+ GATE_FLOOR=0.90 日级硬底线
（presence < 0.90 = 真缺档/坏数据 FAIL）；[0.90, 0.97) → ok + band 标记 + note
m1a_delta_band（δ 滞后分类，非失败）。

## 3. 性能定标（v2 实测）

- 引擎 20260803 eng=825.9s / 20260804 eng=767.8s（单 worker 全 300 codes；
  v1 对照 850.1/812.8 —— 同日噪声带 ±5%）
- 全 date wall：1129s / 1056s（含读入、排序、门、写盘、审计）
- 两 worker RSS 平台 ~7.5GB（重构后），峰值 ~8.1GB（含写缓冲）；月 run 全程
  30s 采样 rss_audit.csv（§5 汇总）

## 4. STALL 看门狗

STALL_S=2400（900s 为 extract_sz_cancels 镜像值，会杀合法长 date —— 20260803
smoke 单日 >900s 实证；完整真实日期 19-25 min wall）。

## 5. 验收表（run 20260910_054209_7521；v2 配置）

- [x] 15/15 dates 处理；**4,499/4,500 code-days ok**；band 日 426 (9.5%)；vacuous 0；
      唯一失败 301308.SZ@20260807 presence 0.89981（floor 0.90 边际，差 0.0002）——
      已分类：极端活跃创业板名的 δ-lag 尾。证据：M4 conservation PASS / orders 0 /
      counters_equal / guard 全净（非坏数据非引擎错）；presence 跨 5 日平滑下降
      0.9205→0.9118→0.9042→0.9094→0.8998 随事件量 468K→612K 单调 — 同因量级
      变化，非日特异；band=True 前 4 日（旧 0.97 单阶门此名 5 日全 FAIL —— 双阶
      门正确吸收 4/5）。失败率 1/4,500 = 0.022% ≤ 0.1% 规格
- [x] 体积：月总 8,238.5MB（events 7,104.1 + sweeps 924.6 + ckpts 209.8）vs 源
      5,355.6MB = **1.461× ≤ 1.5× ✓**（v2 行组+ zstd3；文件行数 == day_rows 记录
      全表一致，无重复写；events 10.1B/行 / sweeps 8.9B/行 / ckpts 2.2B/行）
      —— 后续可调项：sweeps 逐 code 组 (v1 ~3.9B/行) vs 1M 组 (8.9B/行)，W5 可选
      每表独立 row_group_rows 再省 ~600MB/月
- [x] 字节级重跑比对（--force 二跑 20260910_082834_33436 vs 20260910_054209_7521）：
      **parity.ok=True，mismatch_dates=[]**（15 dates × 3 tables 逐 sha256 全等 —
      1M 行组缓冲写不破坏确定性不变量）；重跑日行/门结果与首跑逐日全同
      （含 20260807 同一失败复现 → 确定性非偶然）
- [x] RSS：30s×970 采样，单 worker 峰 12.4GB / 双 worker 同刻和峰 **22.7GB**
      ≤ 24GB normal ✓（≤32GB hard 余量大）
- [x] cross_check_w3 vs W3 校准 JSON：000021/000155/600184 @20260803 全 OK
      （m4 PASS；presence 0.99322/0.99926/0.99632）→ PASS
- [x] 引擎语义未动：v2 每日期 rows 与 v1 逐日全同（52.52M/52.41M @0803/04 等）；
      月总行 901.8M（events 704.4M + sweeps 103.6M + ckpts 93.8M）

### 分类失败明细（规格"已知失败 <0.1% 有分类原因"）
| code-day | presence | 分类 |
|---|---|---|
| 301308.SZ@20260807 | 0.89981 | δ-lag 深度尾部 (活跃名极值日; M4/guard 净, 5 日趋势平滑) |

### 性能（v2 实测，2 worker nice 19）
- 引擎 15 dates 767.8-945.5s（eng 中位 ~880s）；date 吞吐含读/写全链
- 全月 wall ≈ 2.75h（08:27 完成）

## 6. 复现
```
nice -n 19 python run_lob_batch.py --month 202608 --workers 2   # 首跑
nice -n 19 python run_lob_batch.py --month 202608 --workers 2 --force  # 重跑比对
python cross_check_w3.py 202608
```
