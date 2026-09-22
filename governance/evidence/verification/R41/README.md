# R41：工作流完善——参考库体检/自动补算（ref-sync）证据

用户裁定：入参考库应自动跑出数据；缺 spec → fail-fast + 显式豁免。

## 实现

- `research/tools/xscore/pipeline/data_prep.py`：默认前置 ref-sync——缺 `_5y` 产物的参考库成员
  自动补算（复用/生成 `experiments/r37_5y/<name>_5y.yaml` → `flab factor run --no-backtest`
  + `--lockbox final --lockbox-reason ref-autocompute:<name>`；env `FACTORLAB_ST_DEGRADE=allow`
  / `FACTORLAB_MINUTE_UNCOVERED=drop`）；有补算即自动重建面板；缺 spec → fail-fast（面板不重建），
  `--allow-missing-members` 豁免并写 `data/cache/_ref_sync_excluded.json`；`--no-ref-sync` 关闭。
- `flows.py::_resolve_groups` fail-fast：未知名/参考库成员缺面板列 → 明确报错（消灭静默丢弃）。

## 真实体检演习（2026-09-22）

命令与输出（原始）：

```
$ platform/.venv/bin/python research/tools/xscore/pipeline/data_prep.py --only panel --force
[ref-sync] 体检失败，未重建面板：
  - industry_lag_beta_1m（minute）：spec 缺失：/data/students/gaolei/quantresearch/factor/**/industry_lag_beta_1m.yaml
  → 补 spec/修复后重跑；或 --allow-missing-members 显式豁免
（退出码 2）
```

结论：真实首例（参考库今日新增 `industry_lag_beta_1m`，`factor/` 下无 spec、无产物）被正确 fail-fast
列出；面板未重建（未产生不一致面板）。该成员缺 spec 属团队侧补录事项（issue 已开）。

## 测试

- `research/tools/xscore/tests/test_ref_sync.py`（4 例）：只补缺失员且 argv/`--lockbox final`/env/变体
  5y 日期逐项断言；幂等不重跑；缺 spec 的 fail-fast 与豁免落盘；运行失败进 errors。
- `_resolve_groups` fail-fast 两例（未知名 / 参考库成员缺列）。
- 全量：`platform/.venv/bin/python -m pytest research/tools/xscore/tests -q` → 61 passed。
