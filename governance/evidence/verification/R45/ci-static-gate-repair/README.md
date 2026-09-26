# R45 PR #42 静态门修复

## 失败原因

GitHub Actions run `36217411978`（2026-09-26，PR head `9559c7b`）的 pytest 组全部通过，`gates-offline` 因三处结构违规返回 1：

- 新增 `research_flows/contracts.py` 与既有 `platform/tools/ashare_ingest/contracts.py` 同名，平台脚本的裸导入被判为跨工具依赖。
- Prefect xscore flow 直接导入旧 `xlib`，跨越了独立工具目录。
- xscore flow 为统计覆盖率直接调用 `polars.read_parquet`，绕开了 FactorLab 的产物 loader。

原始 run 的其余检查结果：platform `3035 passed`，platform/tools `899 passed`，research/tools `240 passed`，governance/ops `219 passed`。失败来自治理门，不是这些测试失败。

## 修复

- 将 flow 规则模块改名为 `flow_contracts.py`，保留平台已有的 `contracts.py`。
- 把 xscore 锁箱与 manifest 共享实现放进 `research/tools/lib/xscore_lockbox.py`；旧 xscore `xlib` 保留兼容导出，Prefect flow 改为直接使用共享库。
- 覆盖率统计改为通过 `factorlab.adapters.parquet_artifacts.load_signal_artifact()` 读取已校验的 signal frame，再按原有 `(date, code)` 并集口径计算。
- 回归测试确认 loader 从 Prefect flow 调用、覆盖率口径不变、锁箱行为保持兼容。

## 验证

运行 `verify.sh` 可复现本轮检查。2026-09-26 本地输出：

- research flows 与 xscore manifest 回归：`85 passed`。
- G-TOPO：0 处违规。
- G-READ：0 处违规。
- 旧 xscore `score_once.py --help` 在未设置 `PYTHONPATH` 时可启动，确认共享库导入路径正常。
- Python 编译与 `git diff --check` 通过。

面板集成测试使用临时 FactorLab parquet 产物和本机 xscore 面板适配器；没有读取 ClickHouse 或登记生产锁箱。
