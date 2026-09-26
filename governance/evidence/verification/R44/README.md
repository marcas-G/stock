# R44 Prefect Research Flows 验证

本轮新增因子挖掘、xscore CompositeArtifact、策略执行和 campaign Prefect flows；同步更新旧 xpipe 边界、runner 安装入口和输入输出手册，并修复旧 ref-sync 的 missing-only 缓存判定。研究员使用手册现位于 `$QUANTRESEARCH_ROOT/knowledge/pipeline-usage.md`，工具仓只保留指针。

本轮测试仅使用临时目录/临时 Parquet；没有连接生产锁箱或运行 `mode: final`，没有写入 `quantresearch/data/`。

## 验证记录

- `flows-tests.log`：research_flows 契约、FactorLab 临时产物、xscore 面板与 CompositeArtifact reader 往返、策略、campaign、部署和文档示例测试。
- `ref-sync-tests.log`：旧 xscore ref-sync 缓存版本指纹、补算、锁箱 manifest 回归。
- `make-test-research.log`：平台工具、研究工具和治理 ops 的仓库验证目标。
- `governance-tests.log`：治理 ops 独立测试。
- `doc-paths-tests.log`：仓库文档路径检查。
- `runtime-checks.log`：research venv 中 Prefect deployment builder、Python 编译、runner shell 语法和 diff whitespace 检查。

真实数据/ClickHouse 端到端 final 流程没有在本轮执行；因此这些证据验证的是临时真实 Parquet、平台 loader/CompositeArtifact reader 与部署构造，不代表生产锁箱回测已运行。

本次运行中，research_flows 为 58 项通过，旧 ref-sync/manifest 回归为 47 项通过，governance/ops 为 220 项通过，文档路径测试为 11 项通过。Prefect deployment builder、Python 编译、runner shell 语法和 `git diff --check` 也通过。

`make test-research` 中 platform/tools 为 921 项通过；research/tools 为 257 项通过、1 项失败。唯一失败是 `research/tools/factor_lib/tests/test_index.py::test_every_yaml_has_mirror_doc_and_name_matches` 的既有档案新鲜度门：多份外部 Factor Spec 档案超过 72 小时宽限。该门没有被放宽，也没有在本轮批量改写档案。
