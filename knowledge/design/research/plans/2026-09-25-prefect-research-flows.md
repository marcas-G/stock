# Prefect 研究流水线实施计划

设计依据：[Prefect 研究流水线拆分设计](../specs/2026-09-25-prefect-research-flows-design.md)。

## 目标

建立独立的 factor-mining、xscore、strategy-execution Prefect flow，并提供可选 research-campaign 父 flow。Flow 之间以通过 SHA、manifest、窗口、样本和状态校验的 immutable `ArtifactRef` 交接。

## 文件边界

- `research/tools/research_flows/`：共享 ArtifactRef、manifest、版本身份与 flow 模块。
- `research/tools/xscore/pipeline/`：保留 xscore 计算步骤；移除它对因子计算的隐式补算，并增加 FactorArtifact 输入适配。
- `knowledge/design/research/`：本设计、实施计划和 runbook 指针。
- `governance/evidence/verification/R44/`：测试、门及真链路验证原始证据。

## 任务

### T1：共享产物身份与验证

- 定义不可变 `ArtifactRef` 和严格 JSON schema。
- 定义 SHA-256 文件/manifest 摘要、内容版本指纹、Atomic `flow_manifest.json` 写入与读取。
- 验证存在性、hash、status、mode、sample_role、window_id、config/version 一致性。
- 测试正常引用、变更/篡改、半成品、失败/拒绝状态、窗口和样本不匹配。

完成标准：共同契约测试通过；不依赖 CH；manifest 写入中断不会产生可加载发布物。

### T2：factor-mining flow

- 使用 Prefect tasks 编排 hypothesis、lint、FactorLab 运行、评估/冗余/OOS 检查和发布。
- 正式计算只从 Prefect task 启动；重型计算遵守 `heavy.sh`。
- 以内容指纹控制复用，修复 `compute_missing_members()` 只看 signal 是否存在的问题。
- 用平台 signal loader 复验产物，并写 versioned FactorArtifact 和 flow manifest。

完成标准：spec/代码/数据/窗口变化各自触发新版本；旧或半成品 signal 不会命中；正常探索不能读取测试段。

### T3：xscore flow

- 输入改为 FactorArtifact 清单，验证每一项后再组装面板。
- xscore 的 data prep 不得隐式调用 `factorlab factor run`。
- cache key 绑定上游引用内容、配置、数据、代码和样本。
- xscore score/研究组合评估仍与 M8 策略回测明确分开。
- 用平台 CompositeArtifact 格式发布聚合信号。

完成标准：旧裸面板不能作为正式输入；任一坏引用被拒；CompositeArtifact 被平台 reader 成功加载。

### T4：strategy-execution flow

- 输入 FactorArtifact/CompositeArtifact immutable ref 和 StrategyDoc。
- 先验证引用，再调用现有 M7/M8 链；执行层不接触 xscore 内部 NPZ。
- 保留 M7/M8 原生目录/manifest，另写 flow manifest 和 report。
- 明确 `mode` 与 final 锁箱入口映射，完成产物可安全 replay。

完成标准：上游篡改和 spec 冲突在 M7/M8 开始前失败；M7/M8 loader round-trip；产物报告可从回测文件复算。

### T5：部署与 campaign

- 注册 `factor-mining/factor-mining`、`xscore-pipeline/xscore`、
  `strategy-execution/strategy-execution` deployments。
- 可选注册 `research-campaign/research-campaign`，父 flow 仅顺序调用子 flow 并传 ref。
- 任一 flow 失败时后续阶段不运行；成功 campaign manifest 记录三份引用。

完成标准：部署名/参数正确；父 flow 的顺序、引用传递、失败短路测试通过。

### T6：运行手册与验收证据

- 更新流水线 README、Makefile/运行入口与安装说明。
- 运行针对性测试、研究侧测试和所需常驻门。
- 每条 flow 做一次允许的数据规模的真实端到端验证；不使用大型默认矩阵做 smoke。
- 保存命令、原始输出、门结果及数据前后基线到 `governance/evidence/verification/R44/`。

完成标准：`make test-research` 与相关门通过；真实验证证明流程实际启动、产物可被下游 reader 加载；证据目录可复现。

## 开发顺序

每个任务先新增针对契约的失败测试，再实现最小代码、跑相关测试并做审查。没有真实 CH/parquet 的单元测试不得伪装成链路验收。正式 final 模式的真链路测试必须使用隔离的测试台账/可控测试窗口，不消费生产锁箱机会。
