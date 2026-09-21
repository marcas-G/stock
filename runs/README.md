# runs/（R37 起：兼容软链壳）

运行产物**物理落点已归位研究产物区**（2026-09-21，用户裁定"所有产物文件都放 quantresearch"）：

- `runs/platform` → `$QUANTRESEARCH_ROOT/results/platform`（软链）
- `runs/research` → `$QUANTRESEARCH_ROOT/results/research`（软链）

平台默认解析（`factorlab.config.settings.results_dir`，`default_results_dir()`）：
研究产物区存在 → `<QUANTRESEARCH_ROOT>/results/platform`；否则回退本目录（GitHub-hosted CI）。
env `FACTORLAB_RESULTS_DIR` 优先级最高。旧文档/证据中的 `runs/platform/...` 引用经软链继续可解析。
