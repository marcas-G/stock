# 锁箱纪律（Rolling Lockbox Discipline）设计与规范

> 日期：2026-09-21 · 状态：设计定稿待审 · 关联：`interface.md` §1/§9、`factor-mining-playbook.md` §4.1、
> Skill `factor-mine` §7/§8、`research_tidy.py`、`ledger.sqlite`（试验账本）
> 决策（用户裁定）：单一全局锁箱 · 滚动 12 个月 · 只管以后 · 硬门+自动登记 · 只限终评。

## 1. 背景与问题

现状盘点（22 处缺口，摘要）：

- 挖矿 spec 的 `date` 窗口可选（缺省全历史）；评估/分层回测按 spec 窗口整段跑，产物**不记录样本区间**，
  平台没有任何 train/test 概念（`core/spec.py:58-68,106`、`app/run.py:496-499,639-640`）。
- `_oos2026`（2026-01-01~09-10，86 个 spec）绕过 lint/索引/档案门；同一 2026 窗口被
  M14→M15→…→rescan 反复观测（364 试验、219 条含 OOS），无"用后作废"机制。
- 参考库条目直接以 OOS t 作入库理由（`factor/_reference.yaml` 多条 reason）。
- `strict=True`（正式 OOS 仅 PASS）已定义但无调用方（`adapters/read/health.py:219,259-262`）。
- 统计/模型侧：`lab/ledger.py` 无窗口字段；walk-forward 无 embargo；
  同一测试段可无限重跑选模型（`lab/autoencoder42/walkforward.py:28-43,63-66`）。
- 无任何门/测试约束"样本窗口声明"。

结论：**测试集可被无阻碍、可重复地用于调参**，任何"样本外"结论都不可信。本设计建立一条
可机检、带配额的锁箱纪律。

## 2. 目标与非目标

**目标**

1. 全库单一时间锁箱：窗口滚动可预测，窗口内数据=未观测测试集。
2. 一切触碰锁箱的评估**自动登记**（append-only，不可否认）；**终评**（入库/结论）额外受
   唯一性与配额约束，硬门拒跑。
3. 产物与档案携带样本声明（`sample_role/window_id/access_id`），可追溯到具体访问记录。
4. 机制可测、可审计、负向自检（造假必被门抓到）。

**非目标**

- 不回溯历史访问、不重构既有 `_oos2026` 与参考库条目（"只管以后"）。
- 不引入 walk-forward/多重检验重定标机制（保留现状，未来另议）。
- 不改 IS 内挖矿体验：`date.end ≤ window_start` 的 spec 照常直跑。

## 3. 术语

| 术语 | 定义 |
|---|---|
| IS（内样本） | 数据日期 `< window_start` 的全部数据；自由挖矿/调参/选型 |
| 锁箱（OOS） | 数据日期 `∈ [window_start, window_end]`（window_end=最新数据日）；**边界含 window_start** |
| 探索评估（exploration） | 任何在含锁箱窗口上产生可见指标的评估；不限额，必登记 |
| 终评（final） | 作为入库/结论证据的评估（admit/ref add/策略与合并固化/档案冻结/experiments 结论）；每候选每窗一次 + 每窗 M 总额 |
| 候选指纹 | `sha256(canonical({kind, artifact, params, window_id}))`；改参=新候选 |
| window_id | 最近一次 roll 的季度号（如 `2026Q3`）；配额与唯一性以它分桶 |

## 4. 窗口与状态

**窗口算法（单点真相：`platform/src/factorlab/core/lockbox.py`）**

- `roll(as_of)`：取 `as_of` 之前**最近一个完整日历季末** `Qe`；`window_id = f"{Qe.year}Q{Qe.quarter}"`；
  `window_start = 首个交易日 ≥ (Qe − 1 年 + 1 日)`（交易日历取数）；`window_end = 最新数据日`（≤ today）。
  示例：2026-09-21 首次 roll → `window_id=2026Q2`、`window_start=2025-07-01`；2026-10-01 再 roll →
  `2026Q3`、`window_start=2025-10-01`（2025Q3 解封入 IS）。
- **幂等**：同一 `window_id` 重复 roll 返回既有窗口、不变更任何行；拒绝窗口倒退（`LOCKBOX_ROLL_BACKWARD`）。
- `window_end` 随新数据自然生长（每次读取时 = 最新数据日）；跨季度未 roll 时，
  `enforce` 抛 `LOCKBOX_WINDOW_STALE`（提示先 `lockbox roll`），防止静默跨季。
- 数据源：交易日历（`trading_calendar`）+ health/daily 最新分区；离线测试注入固定日历与 data_end。

**状态表（`$QR/data/ledger.sqlite`，WAL；不新增根文件）**

```sql
CREATE TABLE IF NOT EXISTS lockbox_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  window_id TEXT NOT NULL,
  window_start TEXT NOT NULL,          -- ISO date
  quota_final INTEGER NOT NULL DEFAULT 20,
  rolled_at TEXT NOT NULL              -- ISO8601 UTC
);
```

## 5. 登记（append-only）

```sql
CREATE TABLE IF NOT EXISTS lockbox_access (
  access_id TEXT PRIMARY KEY,          -- ULID
  ts_utc TEXT NOT NULL,
  window_id TEXT NOT NULL,
  window_start TEXT NOT NULL,
  window_end TEXT NOT NULL,            -- 登记当刻评估面板的结束日
  kind TEXT NOT NULL CHECK (kind IN ('exploration','final')),
  fingerprint TEXT NOT NULL,
  artifact TEXT NOT NULL,              -- spec/doc 相对研究产物区路径（或规范化的绝对路径）
  params TEXT NOT NULL DEFAULT '{}',   -- canonical JSON
  command TEXT NOT NULL,               -- 触发命令（argv，逗号分隔）
  result_ref TEXT,                     -- 产物/summary 路径（失败为 NULL）
  reason TEXT NOT NULL,                -- 必填，非空
  actor TEXT NOT NULL,                 -- uid@hostname
  tool TEXT NOT NULL                   -- factorlab 版本/子命令或 lab 脚本名
);
CREATE INDEX IF NOT EXISTS idx_lockbox_win_kind_fp
  ON lockbox_access(window_id, kind, fingerprint);
```

- 只增不改不删；平台与 `lab/lockbox.py` 是唯一写入方（禁手改，同 ledger 纪律）。
- 登记发生在**评估启动时**（拿到窗口后、跑数据前），结果路径在结束回填（同连接更新
  `result_ref`，仅此一列允许回填）。评估被拒（参数/门错误）不写登记。

## 6. 配额与终评

- **唯一性**：`kind='final'` 且 `(window_id, fingerprint)` 已存在 → `LOCKBOX_FINAL_DUPLICATE` 拒跑。
- **总配额**：`COUNT(*) WHERE window_id=? AND kind='final' >= quota_final`（缺省 20，可改 state）
  → `LOCKBOX_QUOTA_EXCEEDED` 拒跑。
- 探索无配额；失败重跑（同一 fingerprint）在 exploration 无限、在 final 同样占用唯一性。
- `admit` / `ref add` / 策略与合并固化 / experiments 结论：必须存在对应的 `final` 登记，
  缺失 → `LOCKBOX_FINAL_REQUIRED`。

## 7. 平台接线（硬门入口）

CLI 参数（Typer，run 族命令统一）：

```
--lockbox [exploration|final]   仅当评估窗口与锁箱相交时必需；缺失 → LOCKBOX_INTENT_REQUIRED
--lockbox-reason TEXT           与 --lockbox 同用、必填非空 → 否则 LOCKBOX_REASON_REQUIRED
```

覆盖命令：`factorlab research factor run`、`factor compose`、`factorlab research strategy run`、
`factorlab research factor admit`、`factorlab research factor ref add`、backtest 入口
（`strategy run` 内部的执行回测不单独出参，随 host 命令）。

**终评映射**：`factor run`/`compose`/`strategy run` 的评估可为 exploration 或 final（由 `--lockbox` 指定）；
而**固化路径**——`admit`/`ref add`、composite/strategy 产物 manifest 落盘、档案冻结——若其引用的评估
窗口含锁箱，则必须存在对应 `final` 登记（缺失 → `LOCKBOX_FINAL_REQUIRED`），否则该产物不得被后续
结论引用（manifest 记 `sample_role` 并校验 access kind）。

判定：`sample_role = is | mixed | lockbox`（面板区间与 `(window_start, window_end]` 的关系；
`mixed`=跨边界）。`is` → 不要求 flag、不登记；其余 → 硬门。

产物元数据（向后兼容追加）：

- `summary.sample = {role, window_id, access_id, window_start, window_end}`；
- 评估段（`evaluation`）与分层回测块记 `date_start/date_end`（补现状缺口）；
- 勘误（实现裁定 2026-09-21）：固化门仅 admit/ref add；composite/strategy 产物以
  `sample.conclusion_eligible=false` 标注非结论证据；
- 档案/manifest 生成器从 summary/登记取字段，不新增人工填报。

## 8. 研究侧（quantresearch）

- `lab/lockbox.py`（薄封装平台内核）：`window()` / `status()` / `register(...)` / `fingerprint(...)`，
  供 scratch、experiments、notebook 使用；不含独立 SQL。
- `results/<campaign>/manifest.json`（R37 约定；campaign 级）必填：`window_id`、
  `sample_role`、`access_ids`（list）、`platform_commit`；`research_tidy.py` 升级为 error
  级（缺 = error；`--allow-missing-manifest` 仍只降 manifest 存在性一类）。
- `dossiers/factors/_template.md` front matter 增：`sample_role`、`window_id`、
  `lockbox_access`（final 访问 id 列表）；已存在档案按 `updated_ts` 从生效日起适用（grandfather）。

## 9. 门与测试

- **G-LOCKBOX**（新增，加入 `gates.sh`）：
  - 离线段：扫档案/manifest 声明字段齐全与格式（与 `window_id` 的季号一致性）；
  - 宿主段（verify-deep）：与 `ledger.sqlite` 交叉核对 `access_id` 存在、kind=final、
    窗口匹配；发现引用不存在/探索 id 冒充 final → 红。
  - 负向自检：伪造 manifest/档案各 1 例必被抓。
- **G-ANNOTATE** 扩展：新/更新档案必须含 `sample_role`（缺 → 红）。
- **平台测试**（TDD，测试先失败）：
  1) 窗口数学（季界/闰年/交易日边界）；2) roll 幂等与拒绝倒退；3) append-only（尝试 UPDATE/DELETE
  非 `result_ref` 列被拒于 API 层）；4) 指纹稳定性与改参变化；5) 唯一性与配额计数；
  6) CLI 硬门：IS 直跑通过 / 相交无 flag 拒跑 / exploration 登记 / final 二次拒；
  7) admit 无 final 登记拒；8) `summary.sample` 与登记一致（存根替换必败）。

## 10. 验收标准（可测）

1. `date.end ≤ window_start` 的 spec `factor run` 无 flag 成功，`lockbox_access` 零新增。
2. 相交 spec 无 flag → exit≠0、错误码 `LOCKBOX_INTENT_REQUIRED`、无产物、无登记。
3. `--lockbox exploration --lockbox-reason …` → 成功 + 1 条 exploration 登记 +
   `summary.sample.role ∈ {mixed, lockbox}`、`access_id` 与登记一致。
4. 同候选 final 二次 → `LOCKBOX_FINAL_DUPLICATE`；窗口内 final 数达 M 后 → `LOCKBOX_QUOTA_EXCEEDED`。
5. `factor admit` / `ref add` 在无 final 登记时拒（退出码与提示明确）。
6. `lockbox roll` 幂等；跨季未 roll 时 run → `LOCKBOX_WINDOW_STALE`。
7. `research_tidy` 对缺字段 manifest 报 error；G-ANNOTATE/G-LOCKBOX 负向自检各命中。
8. 全部平台/治理测试绿；`make gates`、`make verify-fast`、`make verify-deep`（宿主段）绿。
9. 证据：`governance/evidence/verification/R40/`（命令+结果+指路；造假被拒的负例截取）。

## 11. 测试策略

- 断言来源=本章 §10 与 §6/§7 行为条款，不从实现推导。
- 真实度：SQLite 真库（tmp）、真交易日历（含注入固定日历两边都测）、CLI 以子进程跑沙箱 QR；
  只 mock 外部（CH 不参与本功能）。
- 每个测试含"禁止行为"断言（如：IS 运行后登记表行数不变；拒跑后产物目录不存在）。
- 替换存根必败：登记/配额/指纹任一改为硬编码，§10-3/4 必红。

## 12. 文档与运维

- 新增 `factorlab lockbox status|roll`（`--json`；status 另输出 `is_end`=window_start 前一交易日，
  供挖矿 spec 直接使用）；`flab health` 增 `lockbox` 段（window_id/start/end/M/已用 exploration/final/剩余）。
- 季度提醒：user systemd timer（与 nightly 同机制）在季末提醒 `lockbox roll`（不自动改历史，
  避免无人值守下静默解封；roll 仍需一次人工确认）。
- 文档同步：`interface.md` 新增"锁箱与样本声明"章节；`factor-mining-playbook.md` §4.1 与
  Skill `factor-mine` §7/§8 入库判定加"final 登记"要件；`CONVENTIONS.md` 增补 manifest/档案字段；
  本 spec 落 `knowledge/design/platform/specs/`。

## 13. 迁移与"只管以后"

- 生效日：实施完成后首次 `lockbox roll`。存量 spec（175 个 end=2026-07-31）在生效后
  被视为相交 → 需 flag 或改窗；**建议操作**：挖矿模板/playbook 统一改为
  `date.end = window_start 的前一交易日`（`lockbox status --is-end` 输出），存量 spec 由后续
  日常挖矿逐步改写，不强制一次性迁移。
- 存量 `_oos2026`、参考库 OOS 理由、已有档案不标记不补登（用户裁定）。

## 14. 备选与否决记录

- **纯 walk-forward**：与"锁箱一旦观测即污染"矛盾，滚动窗难以全局统一口径 → 否决（保留现状）。
- **无锁箱仅登记**：约束过弱，调参可无限看测试 → 否决。
- **按数据版本划界**（`vscope…`）：版本语义面向对账而非观测纪律，边界不稳定 → 否决。
