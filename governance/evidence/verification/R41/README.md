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

## 唯一入口（R41 强制）与真实演练

- 公约：`$QR/CONVENTIONS.md` §4（唯一入口=工作流）+ 手册 `$QR/knowledge/pipeline-usage.md`；
  技能/playbook/interface §10 同步；`lab/platform_client.py` 标注退役。
- config 新能力：`factors`（新因子 spec 清单，自动补算 5y + 锁箱 final 登记）、
  `data.members`（面板成员显式清单；自定义成员集写各自 `panel` 路径）；`_resolve_groups` 未知名 fail-fast。
- 示例 config：`research/tools/xscore/pipeline/configs/example-subset.yaml`
  （members=[low_vol_20d, reversal_20d]，factors=[low_vol_20d]）。

**真实演练（2026-09-22，`make xpipe CFG=…/example-subset.yaml`，rc=0）**：

```
factor_task Cached → data_prep 建子面板 panel_subset_demo.npz（+按网格建 4 辅助缓存）
→ 锁箱 final 登记（quota 5/20）→ score pair_M0a → portfolio open/all
   [porteval:pair_M0a|open|all] ann=12.44% excess=-1.66% IR=-0.04 expo=99.9% pos=505
→ REPORT.md + run/campaign manifest（sample_role=mixed、config_path、access_ids）
```

**演练中修复的 4 个真缺陷**（若不做唯一入口演练不会暴露）：
1. flow 顺序：自定义成员集首跑面板不存在 → 改为"面板缺失时先建面板再锁箱登记"（含回归测试）；
2. 辅助缓存网格：`open_adj/mv/limits/amount` 固定按 42 面板建 → 改为按 config 面板派生
   （`*_<suffix>.npz`），`portfolio_task` 透传路径；
3. `adv` 键名：`amount` 缓存字段与 porteval 期望不一致 → 新缓存写 `adv`，porteval 兼容旧键；
4. `locked_up/dn` 布尔固化：float+NaN 在布尔索引下恒真（全单被闸 `blocked_buys=94611`）→
   `_normalize_limits` 固化 bool（NaN→False）；另补 `CODE_FILES` 指纹清单（漏 data_prep/flows → 改代码不失效）。

门：`research_tidy` errors=0；`check_lockbox --root` errors=0；测试 68 + tidy 43 全绿。
