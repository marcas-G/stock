# R41：工作流完善——参考库体检/自动补算（ref-sync）证据

> **R42（2026-09-24）supersede 注记**：本文件中的配额（M=20 / `--quota-final` /
> `FACTORLAB_LOCKBOX_ADMIN`）、探索登记、`lockbox_off` 留痕、`--lockbox*` CLI 参数与
> "manifest 复用/配额门"等描述已被 R42 取代或删除——现行口径见
> `knowledge/design/platform/specs/2026-09-24-final-test-once-discipline-design.md`
> 与 `knowledge/contracts/interface.md` §10（每版本最终测试一次；入库只看测试段冻结件）。
> 其余内容（ref-sync / 新鲜度门 / 唯一入口 / 演练记录）仍有效，作为历史证据保留。

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


## 数据新鲜度门（同日新增）

- `data_prep` 前置校验：CH `daily` 最新 vs 独立时钟（期望=前一工作日；`trade_cal` 与数据同源会
  滞后 → 周历近似 `source=weekday-approx`）；`--max-lag-days`（缺省 3 / `data.max_lag_days`）+
  `--allow-stale-data` 显式豁免；超容忍 fail-fast 提示 `make data-update`。
- 真实验收（2026-09-22）：CH 最新 2026-09-17、期望 2026-09-21、滞后 2（weekday-approx）；
  容忍 3 → ok；容忍 0 → `ok=false` 且缺失 `['2026-09-18','2026-09-21']` + 指引。
- 事实：本机 `pan-data-update.timer` 今日 08:11 已跑，但**源盘最新包即至 2026-09-17**（raw zip），
  CH/日历同步停在该日——属源滞后，非链故障；门在容忍内放行（默认 3 交易日），严格档可调 0。


## 唯一入口穿透审计与加固（E1–E5）

审计发现 5 条真实旁路：① host CLI 可裸登记 final；② 内核脚本裸跑（score/portfolio 无门）；
③ 配额自助改；④ lockbox_state 可 SQL 删改；⑤ FACTORLAB_LOCKBOX=off 全局关闸。加固如下：

| 加固 | 实现 | 测试 |
|---|---|---|
| E1 流水线打标 | `flows._run`/flow 进程/`data_prep._member_env` 置 `FACTORLAB_PIPELINE=1` | ref_sync 断言 env |
| E2 final 需标记 | `guard_run` 新 final 无标记 → `LOCKBOX_PIPELINE_REQUIRED`（复用/入库车道不受限） | guard 两例（新增/复用） |
| E3 配额门 + state 防护 | CLI `--quota-final` 需 `FACTORLAB_LOCKBOX_ADMIN=1`；state 禁 DELETE/REPLACE（roll 改 UPDATE） | CLI 拒/许 + state 触发器 |
| E4 裸跑警示 | 5 个脚本 `__main__` 打印"非流水线运行（dev only）" | 源码清单 + porteval 实跑 |
| E5 off 留痕 | xlib off → manifest `lockbox_off=true` | xlib/flows off 测试 |

全量：xscore+governance+platform lockbox+architecture **402 passed**；`make gates`/tidy/check_lockbox 全绿。
残留（需审计，非机械可杜绝）：手写伪造 manifest、绕过平台直读 CH、手改 results。
