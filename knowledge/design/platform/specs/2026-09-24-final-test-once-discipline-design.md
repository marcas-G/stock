# 最终测试只跑一次（Final-Test-Once）纪律设计

> 2026-09-24 · 状态：待评审 · 替代并简化 R40 锁箱纪律（`2026-09-21-lockbox-discipline-design.md`）
> 本文是"重新梳理"后的唯一口径：**数据分两段；探索只准训练段；每个版本最终测试一次；入库只看测试段结果。**

## 1. 为什么改

- R40 把"防测试集调参"做成了整套机制（探索登记/终评唯一/每窗 20 配额/一堆开关与门），
  机制叠得多、问题没先理清；且**入库用的冗余检验与调参用的是同一段数据**——
  在哪儿调好看就在哪儿过，独立性校验挡不住。
- 用户裁定：**最终测试只能跑一次；入库只看测试段数据水平；训练段/测试段各自做参考库冗余检验。**

## 2. 数据两段（沿用现窗口定义，只保留这两段语义）

| 段 | 范围 | 用途 |
|---|---|---|
| 训练段（IS） | 数据起点 ~ `window_start` 前一交易日（`is_end`） | 随便看、随便调；冗余检验用于开发 |
| 测试段 | `window_start` ~ 最新数据日 | **平时看不见**；只有最终测试这一条路能看 |

- `window_start`：滚动 12 个月，季末 `factorlab lockbox roll` 前移（现状不变）。
- 查看：`factorlab lockbox status --json`（`is_end` 可直接写进 spec 的 `date.end`）。

## 3. 规则（核心四条）

1. **探索只准训练段**：任何评估若窗口 `date.end > is_end` → **直接拒绝**（`LOCKBOX_TEST_ONLY_FINAL`），
   不存在"登记一下就能看测试段"。想看测试段 → 走最终测试。
2. **最终测试每个版本一次**：
   - 版本 = spec 内容 + 参数 + 面板指纹 + `window_id`（任一变化=新版本）
   - 同版本第二次最终测试 → 拒绝（`LOCKBOX_FINAL_DUPLICATE`）；改了参数=新版本，可再测一次
   - 最终测试必须经**流水线**（`make xpipe`）或**入库车道**（`admit/ref add`）执行；host 直跑不算
3. **最终测试的产出**（冻结保存，不复算）：
   - 测试段上的评估指标 + **测试段冗余检验**（对照参考库：
     `corr_max / r2_lib / resic_t / retention`）
   - 登记：谁、何时、哪个版本、哪次访问（append-only 账本）
4. **入库只看测试段那份**：`admit / ref add` 必须引用该版本的最终测试登记与测试段冗余结果；
   没有最终测试 → 拒绝入库。训练段的冗余检验仅供开发参考，**不作入库依据**。
   参考库实际写入还须通过 D10 判决：残差 `|t|≥3`、`corr_max<0.7`、
   `retention≥0.5` 才可加入；`ref add` 在备份和写入前复核，手工 entry 参数不能绕过。
   旧冻结件缺少 retention 时，从同一最终测试产物补算，不重跑或重复登记 final。
   此 3.0 操作门槛低于联合 max-T 估计约 3.45，不代表 FWER 已受控。
   > 实现裁定（计划 T4）：无 final 登记时由入库车道**执行**该次最终测试并冻结
   > （而非拒绝）；已有 final 则只读冻结件。

## 4. 不做的事（本次明确砍掉）

- 每窗 20 个终评配额（删除）；配额管理门/管理员开关（删除）
- 探索碰测试段的"登记通行"（改为直接拒绝，故无需登记探索）
- 为"管理申报数"发明的各种开关/标记/门（大部分删除或简化，见 §5）

## 5. 旧机制处置

| 旧机制 | 处置 |
|---|---|
| 测试段窗口（滚动 12 个月 + 季末 roll） | **保留** |
| `lockbox_access` 账本 + 禁删改触发器 | **保留**（记最终测试访问；防手改） |
| 探索登记（exploration） | **删除**（探索碰测试段直接拒） |
| 终评唯一（每候选每窗一次） | **改为**"每版本一次最终测试"（不再按窗重置） |
| 每窗 20 配额 / `--quota-final` / `FACTORLAB_LOCKBOX_ADMIN` | **删除**（CLI 参数与校验一并移除；账本防删改触发器保留） |
| `LOCKBOX_PIPELINE_REQUIRED` 流水线标记（E1/E2） | **保留**（最终测试必须经流水线/入库车道） |
| 裸跑警示（E4） | **保留**（便宜、有用） |
| `FACTORLAB_LOCKBOX=off` / `lockbox_off` 留痕（E5） | **简化**：保留一个总开关（测试/CI 用），去留痕字段 |
| G-LOCKBOX / 档案 `sample_role` 字段 / manifest `access_ids` | **简化**：只需能回答"入库引用的最终测试是否存在/唯一/窗口对得上"；`sample_role` 字段可留但不再是主检查 |
| 数据新鲜度门 | **保留**（可调为提示或拒绝） |
| ref-sync 自动补算、manifest 溯源、config_path 强制 | **保留**（与本次纪律无关的实用件） |

## 6. 验收标准（可测）

1. 窗口 `date.end > is_end` 的探索（无 final 意图）→ 拒绝（稳定错误码），且零产物零登记。
2. 同版本最终测试第二次 → 拒绝；改参数后的新版本 → 可测。
3. 最终测试在流水线中自动登记；产出含测试段指标与测试段冗余检验（与参考库对照）。
4. `admit / ref add` 无最终测试登记 → 拒绝；有 → 引用该登记与其测试段结果（可验证来源）。
5. 训练段冗余检验可随时跑（不要求登记、不消耗任何额度概念）。
6. 配额相关代码路径不存在（`--quota-final` 移除、无 `LOCKBOX_QUOTA_EXCEEDED` 触发入口）。
7. `make gates`、平台测试、`research_tidy` 全绿；文档同步（interface §10、CONVENTIONS §4、pipeline-usage）。

## 7. 影响面与迁移

- 代码：`core/lockbox.py`（错误码/角色语义）、`adapters/lockbox_store.py`（登记/触发器/删配额）、
  `surfaces/cli`（CLI 参数）、`research/factor.py`（admit/ref 校验改引用测试段结果）、
  `pipeline flows/xlib/data_prep`（最终测试登记、探索拒）、测试与门。
- 存量数据：`lockbox_access` 历史行保留（只读）；不再新增 exploration 行。
- 文档：interface §10、CONVENTIONS §4、pipeline-usage、playbook/skill 的入库段。
- 证据：`governance/evidence/verification/R42/`。
