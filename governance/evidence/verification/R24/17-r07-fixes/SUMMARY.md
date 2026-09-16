# R24/17-r07-fixes — R07-STRAT-I6 / R07-LINT-I7 修复证据索引

## R07-LINT-I7：lint 静态校验库函数 arity

根因：`OpMeta`/生成表只存窗口位置，无签名参数个数信息；`semantics.infer` 只解析
窗口不校验调用形态 → `ts_cum_count(close, 5)` lint OK、运行期 TypeError。

改动：

- `platform/scripts/gen_op_catalog.py::_arity`：签名 →（必需位置参数数，位置参数
  上限；`*args` → None）；`classify` 写入 meta；`render`/`build_ta_catalog` 9 元组
- `platform/src/factorlab/core/ops/classification.py`：`OpMeta.min_args/max_args`
  （默认 None=未知 → 静态放行）+ 取值校验
- `platform/src/factorlab/core/engine/semantics.py::_Inferrer._check_arity`：
  位置参数超上限 / 位置+keyword 总数低于必需数 → `SemanticError`（函数名/期望/
  实际，行:列）；`**kwargs`、未标注算子放行
- 重生成 `_generated_ta_ops.py`（381 条；`--check` exit 0）

测试：

- `platform/tests/test_op_classification.py::test_opmeta_arity_defaults_unknown /
  test_opmeta_arity_rejects_invalid_range / test_generator_arity_from_signature /
  test_generator_arity_variadic_has_no_max / test_ta_catalog_arity_annotated`
- `platform/tests/test_semantics.py::test_arity_rejects_excess_positional_args /
  test_arity_rejects_missing_required_args / test_arity_accepts_signature_range /
  test_arity_unknown_metadata_passes`
- `platform/tests/test_cli_lint_v2.py::test_lint_rejects_excess_arity /
  test_lint_accepts_valid_arity`

红→绿：

- `lint/01-red-arity-tests.txt`（8 failed：DID NOT RAISE / OpMeta 无字段 / 生成器无 _arity）
- `lint/02-green-arity-tests.txt`（63 passed）
- `lint/03-gen-op-catalog-check.txt`（重生成 + `--check` exit 0）
- `lint/04-make-lint-factors-arity.txt`（173 通过 / 0 失败）
- `lint/05-cli-arity-repro.txt`（真 CLI：`ts_cum_count(close,5)` exit 1；
  运行期同款 TypeError 对照；合法 `ts_cum_count(close)+ts_mean(close,20)` exit 0）

commit：`16fbc84`

残余（不误杀方向）：

- 22 条 `*args` 变参算子 `max_args=None`（不设上限——可变参数是签名事实，非盲区）
- keyword-only 参数不计入 arity（语法上不可按位置传）
- 插件/注册面算子未标注 arity → 放行（本轮范围 = 生成表 polars_ta）

## R07-STRAT-I6：策略 lint + universe_override

根因：(1) `factorlab lint` 只走 `load_spec`，策略 YAML 无校验入口（`--strategy`
exit 2）；(2) `StrategyDoc.universe_override` 仅解析/dry-run 打印，`run_strategy`
运行链不消费（静默）。

改动：

- `platform/src/factorlab/core/strategy/spec_io.py::looks_like_strategy_doc`
  （顶层含 `signal`+`portfolio` = 策略文档形态）
- `platform/src/factorlab/surfaces/cli/main.py::_lint_one` 分派：策略文档走
  `load_strategy_doc` 严格校验；失败（含 V1 `NotImplementedError`）可读报错 + exit 1；
  因子 spec 原路径不变
- `platform/src/factorlab/app/strategy/run.py::_filter_to_universe`：
  `date` 窗口过滤后按 `universe_override`（canonical ts_code）过滤信号帧再进 M7；
  空交集 fail fast（列 override + 窗口内可用 codes）；null = 零行为变化
- `research/tools/strategies/run_strategy.py`：dry-run 明示消费语义

测试：

- `platform/tests/test_cli_lint_strategy.py`（7：合法 strategy exit 0 / 未知键 /
  NEXT_WINDOW 无窗口 / V1 rules / direction 非法 / 因子路径不回归 / 混批汇总）
- `platform/tests/test_run_strategy.py::test_universe_override_filters_signal_before_construction /
  test_universe_override_no_intersection_fails_fast /
  test_universe_override_null_is_zero_behavior_change`（duckdb+ch 双腿）

红→绿：

- `strategy/01-red-strategy-lint-override.txt`（9 failed：策略 YAML 被因子门拒绝；
  override 未过滤 / 不报错）
- `strategy/02-green-strategy-lint-override.txt`（31 passed）
- `strategy/02b-both-backends.txt`（override 过滤用例 duckdb/ch 双腿 PASSED）
- `strategy/03-cli-lint-strategy-demo.txt`（真 CLI：合法 OK；V1/NEXT_WINDOW/未知键
  可读报错 exit 1；因子未知算子指引不回归）
- `strategy/05-research-cli-tests.txt`（research strategies 40 passed）
- `strategy/06-make-lint-factors-after-strategy-dispatch.txt`（173/173 不回归）
- `strategy/04-dry-run-override.txt`（dry-run 明示过滤语义，不触数据面）

commits：`56f5f9e`（platform）、`9e1fdaf`（research）

残余/语义边界：

- override 精确匹配 canonical ts_code（如 `000001.SZ`）；不做 6 位代码/后缀推断
  （写错形态 → 空交集报错并提示可用 codes 与 canonical 形态）
- 部分命中 = 合法子集；未命中的 override codes 不额外告警（空交集才 fail fast）
- 策略 lint 走形态识别（`signal`+`portfolio` 同时存在）；畸形 YAML 落回因子路径
  报错（不吞原错误）
- `pending-items` 的 R07 §4.7 backlog（Plan 2/3、分钟 V2）已由并行 agent 登记
  （#23/#24）；策略 lint 本轮落地，无需再登记

## R07-MIG-I1 / R07-MIG-I2 / R07-GATE-I3：迁移/门/坐标（`mig/`）

- **R07-MIG-I1 门红复发**：`extcnt.md` 旧坐标（`platform/results` → `runs/platform`）、
  重生成 `knowledge/index/factors.md`、extsum snapshot。commit `5919337`。
  循环 2/3：挖矿新增 `turnrank_top10.md` 缺 snapshot → 补齐（commit `ad43a87`）。
- **R07-MIG-I2 模板根因 + 存量清理**：`_template.md` 结果根 → `runs/platform/<name>/`、
  `docs/factors/` → `knowledge/dossiers/factors/`；tracked 且干净档案 **156 份 163 处**
  机械替换（脚本 `mig/bulk_replace_oldcoord.py`，跳过在途 0）；具体指针口径修前
  164 行/156 文件 → 修后 0；reviewer 反引号口径 164 行/158 文件 → 修后 1 行（README
  历史事实）。残余登记 `pending-items`（原 #23，因并行登记 #23-26 改 **#27**）并在门内
  行级豁免。commits `79bbc79`（workspace）、`458030b`（research 连带：strategies 工具
  `--panel` 缺省、README 数据源/文档指针）。
- **R07-GATE-I3 判据加固**：tracked+untracked 统一扫描（本机 git 2.17 无
  `git grep --untracked`，用 `git ls-files -o --exclude-standard` + grep 等价；GNU
  grep 3.1 `-P` 单模式限制 → 裸 results/ 单条 PCRE 分路）；裸 `results/` PCRE 负向后顾
  （排除 `platform/results`、`runs/results`、`test_results`；`<name>` 占位/纯文本不算）；
  补 `research/tools/lib/`、`docs/{factors,strategies}/`；3 README + strategies 两文件
  整文件豁免 → 行级。TDD：`mig/05`（RED：同一注入 0 捕获）→ `mig/06`（GREEN：
  untracked/tracked A/B 均捕获；误报探针 `runs/results`/`test_results`/`results_dir`/
  `<name>` 0 命中）→ `mig/07`（实现 diff + 清理探针后残余）。commit `83bf9a2`。
  生效即时验证：本门当场上报修复者 skill 草稿里的 `platform/results` 活字面量 1 处（已改写）。
- **最终门**（`mig/08-final-gates.txt`）：结构门唯一红 = G-LEGACY 挖矿 **untracked 在途
  5 处**（`intraday/*`、`symrun_r30_*`、`max_effect_20d_zmax` 的旧落点；按纪律不碰/不
  提交，untracked 扫描正是 R07-GATE-I3 要求的"真实存活"捕获）；其余全绿（G-IMPORTS/
  G-INDEX/G-ANNOTATE/G-REVIEWS/G-LINT/G-TOPO/dataiface）；`check_reviews.py` exit 0。
- **skill 纪律**：`.claude/skills/factor-mine/SKILL.md` §8 入库增"提交前跑
  `gates.sh --structure`（含旧坐标/索引/snapshot），门未绿不得 commit"
  （commit `ffa78ef`）。
