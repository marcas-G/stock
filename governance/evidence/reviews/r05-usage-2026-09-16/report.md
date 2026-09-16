# R05 使用验证报告（2026-09-16）

- **目的**：使用开发团队的新工作（开放算子、ST 降级开关、分钟链改动）做真实 CLI 验证，找出问题（非执行方角色）
- **方法**：真实 `factorlab lint/run`（CH 后端）；全部证据为实测命令与输出
- **快照**：HEAD 约 `45fcd0e`（团队并发提交，R23/R04 快速项进行中）

## 验证结论（正/反）

| 项 | 结论 | 证据 |
|---|---|---|
| ST 降级开关 `FACTORLAB_ST_DEGRADE=allow` | ✅ 正常：原库 spec（带 exclude_st）直跑成功，summary 含 `st_degrade: True` | `max_effect_20d: n_weeks=178`；`summary.st_degrade=True` |
| 开放算子真实可用面 | ✅ `ts_arg_max(close,20) + ts_corr(close,volume,20)` 端到端跑通 | 6 个月窗 `n_weeks=20, ic=+0.0235`（R03 前两者写不出来） |
| `BBANDS` 结构化返回 | ✅/⚠️ 无 process 时 artifact 边界报错**清晰**：`signal 列 dtype 必须为 numeric，实际 Struct({upperband,...})`；带 process 链时错误晦涩（Struct+clip 天书） | `cap_bb` / `cap_real3` 输出 |
| 我的计划文档举例 `ts_quantile` | ❌ 不存在于 polars_ta 0.5.17 → **已给 design/plan 加勘误**（团队 R22 已记录偏差并替换） | `ImportError`；`R22/open-operators-summary.md` §偏差 1 |

## 新发现

**R05-I1 分类表未标注"返回形态"（struct/多列）**
- `BBANDS(close,20)` 过 lint（分类为 ts/arg:1），但返回 `Struct(upperband/middleband/lowerband)`；
- 无 process：artifact 边界清晰拒绝（好）；**带 process（winsorize 等常规链）：报错为 Struct 上的 clip/quantile 天书**（用户难以定位"这是多列返回"）；
- 建议：分类/conformance 增加返回形态标注（scalar/multi/struct），lint 对已知 struct 函数给提示；catalog 注明字段访问方式。
- 证据：`cap_bb`（清晰版）、`cap_real3`（晦涩版）。

**R05-I2 spec 顶层未知字段静默忽略（含 op_meta 被吞）**
- 实测 `bogus_field: 123` 过 lint（无任何提示）；同理 `op_meta:` 被静默忽略；
- 而引擎报错文案明确引导"未知算子请补 op_meta"（semantics.py:217-218）——**指引了尚未实现的机制（Plan 2），且用户照做后无声失败**；
- 建议：spec strict（extra=forbid）或未知字段 warn；报错文案注明"op_meta 暂未支持/上市时间"，避免误导。
- 证据：`cap_bogus.yaml` OK；`cap_opmeta.yaml` 报未知算子；`semantics.py:217` 的指引文本。

**R05-M1 新解锁面不可发现（已知，团队口径）**
- `factorlab op list` 仍为注册面视图（57）；`docs/catalog.md` 仅含 `ts_corr`，没有 512 条分类面的检索入口；
- 团队已在 R22 summary §偏差 5 记账（归 Plan 2：op list/catalog 同源）；本条登记跟踪，不重复计责。
- 影响：用户/AI 无法从 CLI/文档发现"现在能写什么"，只能读生成表源码。

## 勘误（reviewer 自身）

- R01 时代口头结论"ts_quantile 库里明明有"有误——库中只有 `cs_quantile`/`cs_quantile_zscore`（截面），无时序 quantile；
- `docs/reviews/2026-09-15-open-operators/{design,plan}.md` 已加勘误节（不改历史正文）。

## 附：实测命令

```bash
# ST 降级
FACTORLAB_ST_DEGRADE=allow FACTORLAB_DATA_BACKEND=ch factorlab run research/factor/volatility/max_effect_20d.yaml
# 开放算子
factorlab run /tmp/opencode/mine/cap_real4.yaml      # ts_arg_max+ts_corr → n_weeks=20
factorlab run /tmp/opencode/mine/cap_bb.yaml         # BBANDS → Struct dtype 拒绝
factorlab run /tmp/opencode/mine/cap_opmeta.yaml     # op_meta 被吞 → 未知算子
factorlab lint /tmp/opencode/mine/cap_bogus.yaml     # bogus 字段静默通过
```

---

## 事故记录：3 年分钟链触发主机内存耗尽（2026-09-16）

**时间线**：

1. 10:00 启动 2023–2025 分钟链 run（`--chunk-days 20`，池 4852 只）——**主机内存耗尽、SSH 卡死**（用户报告并断开连接）；
2. 恢复后检查：进程 `37236` 处于 **D 状态、日志 0 字节**；ClickHouse 一度无响应（进程未死，过载恢复）；
3. `kill -TERM` 终止进程；确认 CH 恢复（`SELECT 1`=1；daily=18,124,805 行完整）；无其他残留进程。

**环境并发**：`llama-server` 21GB + 6 个 opencode 会话 + 开发团队 `factorlab lint --all` 等——多负载叠加。

**根因（待平台修复）**：`factorlab run` **无内存上限/看门狗**（研究侧批处理工具已有 `_mem_gate`，平台 run 链没有）；
长窗分钟运行即使 `--chunk-days 20` 也缺乏进程级保护；`systemd-run --user` 在本机不可用（无 cgroup 委派）。

**立即缓解（已验证）**：`/tmp/opencode/mine/safe_run.sh`
—— `prlimit --as` 硬上限 + RSS/系统可用内存双看门狗（5s 采样，超限 TERM→KILL）+ 日志：

- 演示 1：AS 上限 1GB 跑 2GB 分配 → `MemoryError` exit=1（进程失败而非主机 OOM）；
- 演示 2：RSS 上限 1GB 跑 3GB 分配 → `WATCHDOG kill: rss > 1GB`，exit=143。

**平台修复建议（R05-C1，P0）——定位：这是平台必须保证的安全，不是使用者问题**：

1. `factorlab run` 默认安全：内置**不可绕过的硬内存护栏**（psutil RSS 上限 + 系统可用内存下限；超限 → 干净中止、
   清理半成品、明确报错），不依赖使用者参数或纪律；
2. 分钟链（及所有长窗链）**默认自动分块**（按可用内存自适应 chunk 大小，而非要求使用者知道该给多大）；
3. **长窗未分块组合直接拒绝**（或要求显式 `--allow-unbounded`）；
4. 文档标注各链内存需求；`config.py` 暴露 `FACTORLAB_MAX_MEMORY` 并成为默认职责；
5. 安全性验收：模拟大窗任务在 16GB 机器上应"报错退出"而不是"拖垮主机"。

外部包装器 `/tmp/opencode/mine/safe_run.sh`（AS 硬上限 + RSS/avail 双看门狗）仅为**过渡缓解**，不作为方案。

> **更新（同日）**：团队已修复 R05-C1（`2d4cbb2`：`app/memory.py` MemoryWatchdog + RLIMIT_AS 硬上限 +
> 自动分块 + 长窗告警/fail-fast + 重任务协议文档 + 39 测试），并修复 R05-I2（spec strict）与 R05-M1
> （`op list --catalog`）。平台安全默认化落地，待下轮 review 复查（含"大窗任务在 16GB 机器上报错退出
> 而非拖垮主机"的验收）。

---

## 内存护栏对抗性复查（2026-09-16，已验证）

| # | 验证项 | 结果 |
|---|---|---|
| ① | **默认自动分块**（无 `--chunk-days`、无护栏环境变量）2024H1 分钟全市场 | 峰值 RSS **~6.1GB**（旧非分块 ~35GB）；`EXIT=0`；IC 与旧基线**逐位一致**（0.07558398337274398） |
| ② | **硬上限干净中止**：`FACTORLAB_MAX_MEMORY=2GB` 跑 3 年 | RSS 2.3GB 时看门狗触发：`错误: 进程内存超限（R05-C1 内存看门狗，已干净中止、未落半成品产物）：RSS 2.3GB >`，exit=1、**无 summary** |
| ③ | **超大块静态拦截**：`--chunk-days 999` | 拒绝（估算 ≈215.3GB > 阈值 32GB），exit=1，指引 `--chunk-days 27 或更小（默认 20）` |
| ④ | 告警带：显式 50 日 | 单元级：`分钟链块峰值估算 ≈ 14.8GB` 警告后放行 |
| ⑤ | 默认路径（自动 20 日/块） | 单元级：零告警 |

**残余建议（非阻塞）**：RSS 看门狗默认关闭（默认保障 = 分钟链自动分块 + 静态块门）；日频长窗仍建议生产
显式设 `FACTORLAB_MAX_MEMORY`。**结论：R05-C1 verified**（台账已记）。

---

## 3 年延窗结果（2023–2025，安全包装器下完成 EXIT=0）

- 运行：`--chunk-days 10` + `FACTORLAB_MINUTE_UNCOVERED=drop`（团队 R03-I6 修复），
  峰值 RSS ~3.3GB（安全包装器监控），历时 ~13 分钟；
- 结果：`n_weeks=171`（口径问题见 R05-I4）、平均股票 4306.8、IC **-0.0061（t=-0.27）**、
  近 26 周 mean **-0.1002（t=-1.51，方向反转）**、D1=0.00213/D10=0.00022、单调 False；
- `minute_uncovered` 审计：drop 14,529 (code,day) / 4,844 只 / 5 个日期（含 2024-07-18 大批 300xxx）——审计生效；
- **研究结论：`intraday_tail_amt_share` 外样本失效**——2024H1 的 IC +0.0756 未在 2023–2025 复现（归零），
  且近 26 周符号反转；原"候选"判定下调（档案已更新）。

**新发现（R05-I4）**：`align_weekly` 按 (code, ISO周) 取各股自己的周内最后交易日，分钟链停牌无补行导致
**同一 ISO 周产生多个评估日期**（154 ISO 周 → 370 日期 → n_weeks=171），t_stat/recent_26w 口径被抬高，
1-2 只股的微小截面混入"周"统计。
