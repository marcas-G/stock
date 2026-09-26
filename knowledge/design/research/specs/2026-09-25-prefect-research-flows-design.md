# Prefect 研究流水线拆分设计

日期：2026-09-25
状态：实施规格
范围：FactorLab 因子挖掘、xscore 研究评分、策略 M7/M8 回测及可选 campaign 编排

## 1. 目标

把研究工作拆成三条职责独立的 Prefect flow，并提供一个可选父 flow：

```text
factor-mining → FactorArtifact
                     ↓
xscore → CompositeArtifact
                     ↓
strategy-execution → StrategyBacktestArtifact

research-campaign（可选）：按顺序调用以上三个 flow
```

日常调试可以单独运行一条 flow；正式研究批次通过父 flow 串接。flow 之间传递经过校验的 `ArtifactRef`，不能只传一个裸文件路径或 NPZ 列号。

Prefect 负责编排、重试、缓存和运行记录；计算继续复用 FactorLab、xscore、CompositeArtifact、M7/M8 的现有实现。下游不得重新实现上游职责。

## 2. 当前实现与边界

当前已有一条 `research/tools/xscore/pipeline/flows.py`，负责 data prep、score、研究级 portfolio 和 report。平台已有：

- 因子计算、评估及带内容哈希的 `signal.parquet` / `summary.json`；
- `SignalArtifact` 的标准 schema 和严格 loader；
- `CompositeArtifact` 的 writer/reader；
- 策略 `run_strategy`：信号读取、M7 组合构造、M8 回测与原生持久化。

尚未具备独立的 factor-mining 与 strategy-execution Prefect flow；现有 xscore 还接受裸面板，并会隐式补算因子。本设计将取消 xscore 对因子计算的隐式调用。因子缺失或版本不匹配时，xscore 必须失败并指出缺少的引用。

平台原生文件布局保持兼容：因子继续由 FactorLab loader 校验；策略的 `strategy_manifest.json` 仍记录 M7 产物，M8 `manifest.json` 仍记录回测文件及哈希。Prefect 的发布身份另存为 `flow_manifest.json`，不覆盖平台 manifest。

## 3. 共同数据约定

### 3.1 结果根目录

版本化研究产物的根目录为：

```text
$QUANTRESEARCH_ROOT/results/platform/
```

缺省 `$QUANTRESEARCH_ROOT` 仍为 `/data/students/gaolei/quantresearch`，并遵守已有 `QUANTRESEARCH_ROOT` 环境变量。平台原生结果若写在其他位置，由 ArtifactRef 明确指向实际目录；禁止在不同 flow 中各自猜根目录。

版本化发布目录：

```text
results/platform/<factor>/<version>/
results/platform/composites/<composite>/<version>/
results/platform/strategies/<strategy>/<version>/
```

`version` 是输入内容与运行语义的指纹，不是时间戳、路径、mtime 或手工递增号。同一组输入重跑得到相同版本；公式、实现代码、数据版本、窗口或配置改变时得到新版本。发布目录不可覆盖；需要重跑时产生新版本。Prefect retry 可复用同版本已完成且验证通过的产物。

### 3.2 ArtifactRef

Flow 间传递的最小不可变引用：

```json
{
  "schema_version": 1,
  "artifact_type": "factor_signal|composite_signal|strategy_backtest",
  "artifact_uri": "/.../name/version",
  "artifact_sha256": "<主产物内容 sha256>",
  "manifest_sha256": "<flow_manifest.json 内容 sha256>",
  "name": "name",
  "version": "<内容指纹>",
  "config_sha256": "<运行配置内容 sha256>",
  "data_version": "<数据快照/数据版本标识>",
  "window_id": "<锁箱窗口或 null>",
  "sample_role": "is|mixed|lockbox|unknown",
  "mode": "explore|final",
  "status": "candidate|accepted|rejected|completed|failed",
  "source_artifacts": [],
  "platform_commit": "<git commit>",
  "access_ids": []
}
```

校验要求：

1. URI 必须存在，且不得逃逸配置的结果根目录。
2. 引用中的主文件 SHA-256 必须与磁盘字节一致；manifest 自身也必须存在并完整。
3. `version`、`config_sha256`、`data_version` 和上游引用必须与当前请求相符。
4. 上游状态不能是 `failed` 或 `rejected`；不完整或损坏产物不能被当作缓存未命中后静默覆盖。
5. `sample_role` / `window_id` 必须满足当前 flow 的 mode 和窗口限制。
6. 引用对象不可变；验证通过后下游持有引用值，不重新按名字解析到“最新”目录。

`artifact_sha256` 指主产物文件的真实内容哈希；不能使用当前 xscore 的路径、大小、mtime 签名替代。目录 manifest 的摘要用于绑定版本元数据。SHA-256 使用完整 64 位十六进制。

### 3.3 模式、样本与锁箱

所有 flow 配置必须显式写 `mode: explore|final`，不以环境变量缺失来猜模式。

- `explore`：仅允许训练区间（IS）；不得读取或评估测试段，不登记锁箱；产物标为探索候选。
- `final`：配置、上游引用及数据版本冻结；Prefect task 启动正式计算时设置 `FACTORLAB_PIPELINE=1`；由对应平台/锁箱入口登记。每个 flow 的同一 immutable version 只允许一次最终测试。
- final retry 绑定完整的 attempt 身份（输入引用、配置、代码、窗口和样本角色）。若原始锁箱访问仍未关联结果，只能由同一 attempt 复用其 access ID；不同 attempt 和已完成访问均不得重用。已完整写出的原生检查点必须先校验，再继续发布/收尾；有损坏或身份不匹配时 fail closed。
- 发布后的成功 final replay 只复用完整且校验通过的终态产物，不再登记或计算。失败半成品没有有效发布 manifest，不能作为成功 replay 依据；只有 marker 匹配的同一 attempt 才能继续处理。
- `unknown` 样本角色不得在 final 模式发布；若没有锁箱 state，final 必须 fail closed。探索的训练段边界由锁箱窗口/显式训练窗口确定，不可自行推断测试数据可用。
- 锁箱登记权威继续使用 FactorLab `lockbox_store`/已有 flow 接线；不新建第二份台账、不通过结果目录状态冒充登记成功。移除 xscore 现有“manifest 存在即 replay”判定，只有完整终态可 replay。

Factor/composite 的业务状态为 `candidate|accepted|rejected`。普通 flow 运行只能发布 `candidate`；只有现有显式入库/审批过程可将其标为 `accepted`。`rejected` 禁止进入下游。策略回测的 flow 状态为 `completed|failed`。`mode` 与业务状态是两个不同字段。

## 4. Flow 契约

### 4.1 `factor-mining/factor-mining`

配置至少包含假设文本、Factor Spec 路径、训练/测试窗口声明、`mode`、数据版本约束及输出名。假设文本是必填输入并随产物记录。

任务顺序：

```text
登记假设 → lint → 计算 → 单因子评估 → corr/resIC/冗余检查
          → 样本外复证 → 验证产物 → 发布 FactorArtifact
```

正式因子计算由 Prefect task 启动 FactorLab 引擎。`flab factor run` 可作为 task 内部实现调用；研究员从 shell 手工运行的结果不能自动被视为正式 flow 发布结果。

发布目录：

```text
<root>/<factor>/<version>/
  signal.parquet
  labels.parquet
  summary.json                 # FactorLab 原生完成标志和文件校验
  flow_manifest.json           # hypothesis、版本、样本、数据及发布状态
```

Flow manifest 的必需字段包含 `artifact_type=factor_signal`、`name`、`version`、`spec_sha256`、`data_version`、`window_id`、`sample_role`、`platform_commit`、`access_ids`、`mode`、`status`、`signal_sha256`、`config_sha256`、`hypothesis`。

发布前必须用平台 loader 重新加载 signal，并验证标准 `(date, code, signal)` schema、行键唯一、日期窗口、样本角色、spec/hash 一致性、覆盖率及标签口径；失败时不发布 Flow manifest。

FactorLab 的既有 summary 是原生 artifact manifest，不替换成 flow manifest。M2 多输出 Spec 若不能无歧义生成单一 `(date, code, signal)`，本 flow v1 必须显式拒绝并要求指定单输出，不猜测主信号。

`corr/resIC/冗余检查` 和样本外复证的阈值来自配置且写入 flow manifest/report；未配置所需阈值时不自动宣称 accepted。复证输入数据必须在 final 模式下受锁箱保护。
Flow 的 `candidate` 状态是研究复证结论，不会自动写入 D10 参考库；正式写库仍须通过
`flab factor ref add` 的测试段准入判决（`|resIC t|≥3`、`corr_max<0.7`、
`retention≥0.5`）。

### 4.2 `xscore-pipeline/xscore`

输入是 FactorArtifact 引用清单，不再以裸 NPZ 成员下标作为研究来源：

```yaml
mode: explore
inputs:
  - artifact_type: factor_signal
    artifact_ref: /.../factor_a/version
groups:
  daily: [...]
  minute: [...]
models: [M0a, M0b]
walk_forward:
  train_days: 252
  test_days: 63
  step_days: 63
```

任务顺序：

```text
验证全部 FactorArtifact → 组装面板 → 面板质检
→ 分组/模型矩阵 → walk-forward → 候选评估
→ porteval 研究组合评估 → 生成 CompositeArtifact → 报告
```

研究产物：

```text
results/<campaign>/xscore/
  scores/<group>_<model>/
  metrics.json
  portfolio_research.json
  report.md
```

`portfolio_research.json` 只是研究评估，不代表订单或持仓回测。

`explore` 可用多个分组、模型和组合口径进行候选筛选。`final` 只接受一个预先冻结的候选：唯一 group、model、portfolio execution 和 domain；`composite` 必须指向这组唯一配置。final 报告中的 IC 均值和 Newey–West t 值是描述统计，不代表多候选统计比较或多重比较校正后的显著性结论。候选比较应在 explore 训练段完成。

聚合输出：

```text
results/platform/composites/<name>/<version>/
  panel.parquet
  artifact.json
  provenance.json
  flow_manifest.json
```

聚合输出必须通过 CompositeArtifact adapter，成为平台可读取的 `SignalArtifact` 同形信号；不能把 xscore 内部 `signal.npz` 交给策略层。provenance 记录有序 FactorArtifact 引用、group/model、训练窗口、walk-forward 参数、聚合方向、面板 SHA、xscore 配置和代码/数据版本、样本角色及锁箱访问 ID。

所有 FactorArtifact 引用先通过完整性及窗口校验。面板组装必须验证成员列与引用一一对应、date/code 类型与唯一性、缺失率/覆盖率阈值、标签窗口和样本角色。FactorSpec/公式或代码变更必须改变因子版本和 xscore cache key。data prep 只构建缓存，不再计算或补算因子。

### 4.3 `strategy-execution/strategy-execution`

输入：

```yaml
mode: explore
signal_ref: /.../composites/name/version
strategy_spec: /.../strategy/name.yaml
execution: {}                 # 若已写入 StrategyDoc，则不可重复声明冲突参数
```

输入可以是 FactorArtifact 或 CompositeArtifact。必须先验证不可变引用及产物，再解析 Strategy Spec、构造 M7 目标组合；不得先按裸名称调用 `run_strategy` 并绕开版本校验。

任务顺序：

```text
校验 signal ref → 校验 StrategyDoc → M7 构造目标组合
→ M8 回测 → 加载并复核回测产物 → 写策略报告 → 发布 StrategyBacktestArtifact
```

输出目录：

```text
results/platform/strategies/<strategy>/<version>/
  strategy_manifest.json       # M7 原生 manifest
  target_portfolio.parquet
  rebalance_schedule.parquet
  artifacts/                   # M8 orders/fills/accounting/positions 等
  state/final_state.parquet
  nav/nav_series.parquet
  manifest.json                # M8 原生 backtest manifest
  report.md
  flow_manifest.json           # 上游引用、mode、状态和版本身份
```

保留 M7/M8 已锁定的文件布局，以免破坏现有 loader 和校验。策略 Flow manifest 记录 signal artifact 及摘要、Strategy Spec 与执行配置 hash、数据/样本/锁箱信息、平台 commit、状态和回测 manifest 摘要。

M8 当前只持久化其已有支持的执行 timing；配置中不支持的 timing 必须在任务开始时拒绝，不得静默降级。报告只能读取 final mode 的 final 产物作为最终结论。

### 4.4 可选 `research-campaign/research-campaign`

父流程只负责编排与引用传递：

```text
调用 factor-mining → 验证并收取 FactorArtifact
→ 调用 xscore → 验证并收取 CompositeArtifact
→ 调用 strategy-execution → 验证并收取 StrategyBacktestArtifact
→ 写 campaign manifest/report
```

不复制三条子 flow 的计算逻辑。前一阶段失败或引用校验失败时停止，不启动下一阶段。campaign manifest 保存三个 immutable ArtifactRef 及最终状态。

## 5. 缓存和失败语义

- 缓存身份覆盖 Spec/config 内容、实现代码指纹、FactorLab/平台版本、数据版本、样本窗口、上游 ArtifactRef、所有模型/执行参数。
- 命中缓存前必须校验所有文件内容 SHA 和 manifest 交叉字段；无效/半成品产物必须报错或进入新版本路径，不能只看文件存在。
- 失败状态可以记录在 Prefect run 日志或独立诊断记录；失败目录不得存在可被下游接受的 `flow_manifest.json`。
- 发布 manifest 最后写。覆盖已有不可变版本目录应 fail fast。
- 三条 flow 的任务按可重试边界划分；final 锁箱登记在重型计算前做一次，后续 retry 只能读取同 attempt 的未完成登记，或校验并复用该 attempt 的完整检查点；不得为同一 attempt 再登记一次，也不得复用其他 attempt 的访问。

## 6. 实施阶段与验收

按以下顺序实施，逐阶段红→绿：

1. **共同产物契约**：ArtifactRef schema、SHA-256、版本指纹、窗口/mode/status 校验、原子 Flow manifest writer/reader。
2. **Factor flow**：输入 lint/假设/运行、版本命中而非 missing-only、signal loader 复验、评估和发布。
3. **xscore 改造**：输入 ArtifactRef 清单、禁止隐式补算、版本化缓存、CompositeArtifact 适配和研究报告。
4. **Strategy flow**：先校验 ArtifactRef，再执行 M7/M8；保留平台原生落盘并发布 Flow manifest/report。
5. **父 flow 与 deployments**：顺序传递引用、失败短路、注册三个建议 deployment 和可选 campaign deployment。
6. **文档/真链路验收**：更新 Prefect runbook；各单 flow 使用小型/沙箱数据跑一次；数据相关验收走真 CH 或真实 parquet，遵守重任务闸和证据留存规则。

验收必须证明：Spec/代码/数据/窗口变更导致新版本；任意输入文件篡改被拒；半成品不能 replay；xscore 不启动因子计算；CompositeArtifact 可由平台 reader 加载；strategy M7/M8 原生 loaders round-trip；父流程顺序正确且失败短路；explore 不碰测试段；final 只登记一次且 retry 不重复登记。
