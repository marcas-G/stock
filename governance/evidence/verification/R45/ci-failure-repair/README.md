# R45 PR #42 CI 修复验证

本轮修复 PR #42 在 GitHub Actions `verify --profile fast` 中暴露的环境依赖：

- xscore flow 不再手工把 `platform/src` 插入 `sys.path`，使用平台 venv 的 editable FactorLab 安装。
- ref-sync 从 `FACTORLAB_STOCK_ROOT` 或当前 checkout 推导 stock 根目录。
- 仓外 `quantresearch` 面板适配器不可用时，相关集成测试显式 skip；本机有适配器时仍运行真实面板和 CompositeArtifact 往返测试。
- cache 路径测试使用与被测模块相同的 `QUANTRESEARCH_ROOT`。

## 验证结果

- `ci-equivalent-tests.log`：无 quantresearch 产物区环境下运行架构门、research flows、ref-sync 与 manifest 测试；106 passed、3 skipped。
- `local-panel-integration.log`：使用本机 quantresearch 面板适配器运行 xscore flow 测试；10 passed。
- `static-checks.log`：修改文件 Python 编译和工作区 diff 检查通过。

GitHub 原始失败来自 PR head `8d50b628c5ef9ec473ab1cff2f64565269885fce` 的 run `36185905112`。该 run 因本机绝对路径、缺失仓外面板适配器和平台路径注入架构门失败；不是合并冲突。PR 更新后的 GitHub Actions 结果需以新的 run 为准。
