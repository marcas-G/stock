# 总状态表（STATUS）—— 评审轮次 × 方案族 × 完成度

更新：2026-09-19 ｜ 维护：reviewer（本表为**总入口**；状态词：✅ 已交付/已终审；🔶 已交付待收尾/待复查；⏳ 计划就绪待执行；❌ 未计划）

## 一、评审轮次（R01-R09）

| 轮 | 主题 | 结论/产出 | 闭环状态 | 报告 |
|---|---|---|---|---|
| R01 | 严格 review（首轮） | 13C+50I | ✅ 全行闭环（R02 复查 + R21/R22 修复） | `r01-.../report.md` |
| R02 | R01 修复复查 + 新发现 | 56 verified / 7 例外；新增 2C+9I | ✅ 闭环 | `r02-.../report.md` |
| R03 | 挖矿发现 | I/M 若干（分钟覆盖、CA Gate、arity 等） | ✅ 闭环（R22/R03 批次） | `r03-mining-2026-09-16/report.md` |
| R04 | 效率/整洁提案 | 5 路实测；P1-P5 快速项 + DataGate/仓库整理提案 | ✅ 快速项已做（R23）；结构计划=R24 ✅ | `r04-.../report.md` |
| R05 | 使用验证 | C1 内存事故（已修+对抗复查）；I1/I2/I4 | ✅ 闭环（R23/R24） | `r05-.../report.md` |
| R06 | 迁移后复查 | 无 C；2 新发现 + 10 M | ✅ 闭环 | `r06-.../report.md` |
| R07 | 缺口专项 | 8 条 I（门红/模板/判据/数据/契约/口子缺口） | 🔶 8 行 **fixed-claimed，待我复查回填** | `r07-.../report.md` + `improvement-plan.md`（Plan G） |
| R08 | 指标全量核对 | 数值层全过；2 新发现（历史产物口径/退市 adj） | 🔶 2 行 **open**（D8 退市 adj 已见 sidecar 落地痕迹，待复查回填） | `r08-.../report.md` |
| R09 | 分钟链性能评审 | 性能问题清单（折日瓶颈/病态形态/内存） | 🔶 3 行 **fixed-claimed（R31/R31.2），待我复查** | `r09-.../report.md` |

台账：`findings.md` = 101 verified + 11 fixed-claimed + 2 open（fixed-claimed = R07×8 + R09×3，全部待 reviewer 回填）。

## 二、方案族与里程碑

| # | 方案 | 范围 | 状态 | 证据/入口 | 剩余 |
|---|---|---|---|---|---|
| 0 | 平台基座 M1-M8 + 1m funnel | 引擎/评估/M7/M8/Web/分钟链 | ✅ 已交付 | `knowledge/design/platform/**`（历史计划） | — |
| 1 | 开放算子 Plan 1 | 拆白名单/语义推断/零迁移 | ✅（R22） | `2026-09-15-open-operators/` | **Plan 2/3 未计划**（conformance/算子档案/by=截面表达） |
| 2 | 分钟级执行 | NEXT_WINDOW（窗口/触发/参与率/持久化） | ✅（R22） | `2026-09-15-minute-execution/` | V2 项（量能触发/分钟 NAV/临停库）后置 |
| 3 | 策略配置化 Plan S | YAML 六层 + 运行器 + 档案/索引 + L5 max_hold | ✅ 已终审（R27/R28） | `2026-09-16-strategy-decomposition/` | stop_loss/take_profit 平台化后置 |
| 4 | 目录重整 R24 + 工具迁移 | knowledge/governance/runs + platform/tools | ✅（R24） | `r04-.../structure-plan.md`、`tools-migration-plan.md` | — |
| 5 | R04 快速项 P1-P5 | lint 552s→4s 等 | ✅（R23） | `r04-.../report.md` | — |
| 6 | Plan G 缺口整改 | 门回绿/判据/契约/数据三件/口子 | ✅（R29） | `r07-.../improvement-plan.md` | CA 连续回测工作流（R07-DATA-I8 已落地） |
| 7 | eval 指标 v2（D1-D12 + E1-E4） | spread v2/方向胜率/dead-signal/逐日默认/参考库/E1-E4 | ✅（R30，团队全域落地） | `2026-09-16-factorlab-eval-metrics-v2*` | minor calibration；E5 多重检验（设计不做） |
| 8 | 研究 API（flab） | data/factor/strategy 门面 + guard | ✅ 已实现 | `2026-09-18-research-api.md`；`platform/src/factorlab/research/` | — |
| 9 | **Plan DQ 数据质量** | M1（daily 纵切）→ M1.5（18 日/adj/1.25M）→ M1.5c（delta 门控） | ✅ + 🔶 | `2026-09-18-data-quality-pipeline*`；R32/R33 | **M2**（minutes 三角验证+校准）；**M3 已完成收窄口径**（2026-09-20：BJ/前1996 出范围；残余 55→19 修复+36 披露） |
| 10 | **Plan CX Composite** | C1 契约/运行器 → C4 组合接入 → C2 生态/hash → C3 档案索引门 → C4b 加权扩容 | ✅ + 🔶 | `2026-09-19-composite-alpha-aggregation*`；R34 | Ridge/PLS/PCA 待装库；A2 lock_hash 刷新；spec/plan 入 git 版本化 |
| 11 | Plan T 模拟盘（同花顺网页接口） | paper_broker（盘后算单→次日模拟下单→对账） | ⏳ 计划就绪 | `2026-09-17-ths-simulated-api*` | 未开工（等决策） |
| 12 | tick / LOB fact | LOB 事实库 + 面板 | ⏸ **暂停（用户 2026-09-19：tick 相关全部暂停处理，暂不考虑）** | `2026-09-09-tick-lob-fact-design.md` | 门红 14 处（2 文件）随暂停保留为**已知红**，恢复时再处置 |

验证证据轮（一句话）：R21 首轮修复、R22 算子/分钟执行+零迁移、R23 使用验证、R24 目录重整、R27/R28 策略、R29 Plan G、**R30 eval v2**、R31 分钟性能（+R31.2 开关）、**R32/R33 DQ**、**R34 CX**。

## 三、待办分派

### reviewer（我）
1. ~~R07 8 行 + R09 3 行 fixed-claimed 复查回填~~ → ✅ **已完成（R35）**：13 行逐条实测**全部与声称一致** → 11 行 verified；R08 两行加**复查注记**（事实已落地，待团队回填修复说明）
2. ~~R08 2 行 open 跟进~~ → 🔶 注记已加，保持 open 等团队回填（R30 Task11/12 已事实落地）
3. 台账现状：**120 行 · verified 113 · open 7 · fixed-claimed 0 · 复查列覆盖 115/120**；`make gates` exit 0（R31-DQ-I1 已 verified；R36-CI-I1 与 4 条 R31 待团队；R31-API-P1 更名 R31-API-M1——原 ID 末段 `P1` 不合规会被台账门静默漏行，已同步 issue #23）

### 开发团队
1. ~~lob_fact 门红 14 处~~ → **已按 C 处置（2026-09-19）**：`check_dataiface.py` 增 `PAUSED_TREES`（扫描豁免 + 白名单"不腐"校验跳过，输出显式打印暂停提示）；`make gates` 已回 **exit 0 全绿**。恢复 tick 时：移除 `PAUSED_TREES`、按原方案修 14 处、清理/回填 `run_lob_batch` 两条失效登记
2. ~~Plan DQ-M3~~ → ✅ **已完成（2026-09-20，R37 范围收窄）**：BJ/前 1996 按用户裁定出范围免修；scope 内 55 行=19 修复入库+36 隔离披露；全历史发布 7,449（PASS 7,414/DEGRADED 35/FAIL 0）；严格模式回测跑通；#8 已关
3. Plan DQ-M2：minutes 三角验证（分钟↔日线↔腾讯）+ 阈值 calibration + 3 条非阻断小项（delta dedup 边界/§3.3 措辞/水位 ISO 校验）——**minutes 属分钟线，不属 tick，不暂停**
4. open-operators Plan 2/3（如需）与 minute V2（按排期；V2 前置=分钟链性能根因）
5. spec/plan 入 git 版本化（Plan CX 观察项）
6. ~~提交门豁免/CX 注释改动~~ → ✅ 已提交（2026-09-19，含 `.github` 套件；自托管 CI 于 2026-09-21 退役，见 §六）

**已知小项**：
- `governance/ops/check_*.py` 必须以平台 venv（3.13）运行；系统 `python3`(3.10) 无法解析 `delisted_adj_backfill.py` 的 3.12+ f-string（门脚本已在用 venv，仅手动执行时注意）。
- **R35 复查发现**：①`check_reviews.py` 裸 `split("|")` 不识别 `\|` 转义 → 已修为未转义切分+转义还原，并修复台账一行被误还原的竖线（`max\|Δ\|=0`）；②CH `circ_mv > total_mv` 3,914 行（0.023%，头部为 2001 年 `total_mv=0` 老行）——M3 候选核对；③R35 `36-task11-spotcheck.txt` 尾部有未捕获 polars `DuplicateError` traceback（数值有效，证据卫生 M）；④`strategy_artifacts.py:82` LSP 静态告警 `SignalTiming`（团队在途面，运行时导入正常）。
- **未提交改动**：无（2026-09-21 已按主题提交并推送；验证体系见 §六）。

### 用户（你）
1. **刷新 Quark cookie**（网盘 sync 412/403，日更全链唯一外部阻断）
2. **Ridge/PLS/PCA 装库决策**（scipy/sklearn：装进 venv / 接受 skip / 独立实验环境——涉及 venv 可复现约束）
3. **Plan T 是否启动**（同花顺模拟盘）

## 四、当前可用能力速查（用户视角）

```bash
# 数据：更新+质检（需 Quark cookie）
make data-update            # sync→build(clean/audit/health)→verify
# 因子
factorlab lint|run <spec>   # 逐日评估默认；评估含 spread v2/方向胜率/IC衰减/参考库对照
factorlab ref list          # 参考库（daily 10 + minute 20）
# 合成
factorlab compose <spec.yaml>
# 策略（引用因子或 composities/、加权/缓冲/执行参数全可配）
flab strategy run <yaml>
```

## 五、GitHub Issue 索引（2026-09-19 seed）

| ID | Issue | 说明 |
|---|---|---|
| R08-MET-I1 | [#5](https://github.com/marcas-G/stock/issues/5) | 历史产物与现行口径不一致（finding, open） |
| R08-DATA-I2 | [#6](https://github.com/marcas-G/stock/issues/6) | 退市股 adj 补灌（finding, open；事实已落地待回填） |
| PLAN-DQ-M2 | [#7](https://github.com/marcas-G/stock/issues/7) | 分钟三角验证 + calibration + 3 小项 |
| ~~PLAN-DQ-M3~~ | [#8](https://github.com/marcas-G/stock/issues/8) ✅ 已关（R37 范围收窄完成） | 历史残余定向修复 |
| PLAN-T | [#9](https://github.com/marcas-G/stock/issues/9) | 同花顺模拟炒股接入 |
| PLAN-OP2 | [#10](https://github.com/marcas-G/stock/issues/10) | 开放算子 Plan 2 |
| PLAN-OP3 | [#11](https://github.com/marcas-G/stock/issues/11) | 开放算子 Plan 3（by= 截面表达） |
| PLAN-MIN-V2 | [#12](https://github.com/marcas-G/stock/issues/12) | 分钟执行 V2 |
| PLAN-CX-DEPS | [#13](https://github.com/marcas-G/stock/issues/13) | Ridge/PLS/PCA 装库决策 |
| PLAN-CX-GIT | [#14](https://github.com/marcas-G/stock/issues/14) | spec/plan 入 git 版本化 |
| PLAN-LOB-PAUSED | [#15](https://github.com/marcas-G/stock/issues/15) | tick 暂停恢复步骤（paused） |
| PLAN-QUARK-COOKIE | [#16](https://github.com/marcas-G/stock/issues/16) | 刷新 Quark cookie（运维） |

> 同步器：`governance/ops/sync_review_issues.py`（dry-run 默认 / `--apply` 执行；幂等）。
> V1 只建不关；台账仍为状态源。团队在 issue 回填修复说明；PR 用 `Fixes #N` 关联。

## 六、验证体系（2026-09-21 起：单入口双档位）

- **presubmit（云端，每次 push/PR）**：`make verify-fast`；`.github/workflows/ci.yml` 单 job
  （干净 checkout、无产物区/无 CH）——结构门离线子集 + platform 全量 + tools/research/governance，
  markers 精确排除；实测 **6m31s 绿**。
- **postsubmit（本机，夜间 03:00 + 手动）**：`make verify-deep`（`governance/ops/verify.sh --profile deep`），
  systemd user `factorlab-nightly-verify.timer` 驱动（**nice + 内存预检，不走 heavy.sh**——R36 教训）；
  失败自动开 issue（marker `<!-- nightly:日期 -->` 幂等）；实测 **24m26s 绿**。
- **本地（随手）**：`make gates`（18s，工作区态）。
- **排除机制 = pytest markers**（`needs_ch / data_on_disk / host_root / tick_paused / inflight / known_red`，
  各带 issue 号与删标记条件）；**禁止再在 YAML 硬编码 `--deselect`**。
- **自托管 runner 已退役**（公开仓安全建议）：`actions-runner.service` disabled、runner 注销、
  旧 `selfhosted-verify.yml` 删除；目录 `/data/students/gaolei/actions-runner` 归档保留
  （恢复指引见 `reviews/README.md`）。
- 证据：`governance/evidence/verification/R38/`（01-markers / 02-runner-retire / 03-verify）。
