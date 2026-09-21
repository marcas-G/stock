# CI/CD 精简实施计划（presubmit/postsubmit + build-once）

> **For agentic workers:** 用 superpowers:subagent-driven-development 逐任务执行；步骤 `- [ ]` 跟踪。
> 规格：`knowledge/design/platform/specs/2026-09-21-cicd-streamlining-addendum.md`（全权口径）。
> 纪律：TDD（改排除方式前先证明"排除集等价"）；一次提交一主题；命令真相源=verify.sh。

> **实施状态（2026-09-21 收口）**：T1-T4 完成（`5884724` markers / `3904f79` verify.sh+ci.yml /
> `ab71eb6` runner 退役+timer / `8e7b3ab` 深检红修复）；fast 干净 clone **6m31s 绿**、deep 宿主
> **24m26s 绿**、push 后 CI 单 job **success**（run 35563129875）、runner 已注销、timer 已 enable；
> nightly 补丁（去 heavy.sh，nice+内存预检）；证据 `governance/evidence/verification/R38/`。
> 遗留：产物区 tidy 预存 16 errors（报告口径，待整改后升硬门）；R31-CODEGEN (#26)/#27/#28 等业务 issue 照旧。

**Goal:** 验证体系降为"1 入口双档位 + 1 workflow + 1 timer"：fast（云端无数据）/ deep（宿主有数据），
runner 删除，markers 取代硬编码 deselect。

**Tech Stack:** bash、pytest markers、systemd user units、GitHub Actions、GitHub REST（token 在
`~/.config/factorlab/github_token`）。

## Global Constraints

- 不改任何被排除测试的**行为**，只改"排除机制"；排除集合必须与现状逐条等价（fast）或按规格放宽（deep）。
- marker 名冻结：`needs_ch / data_on_disk / host_root / tick_paused / inflight / known_red`（见规格 §3）。
- 每次提交后 `make gates` 必须绿。
- 自托管 runner 删除前先备份其目录路径记录；公开仓安全项（ruleset/secret scanning）不动。

---

### Task 1: pytest markers + 测试标注（fast 排除集等价）

**Files:** Modify `platform/pyproject.toml`、`research/pyproject.toml`（markers 声明）；
标注测试（现状 deselect 对应）：
- `platform/tests/test_factio.py::test_paths_roots_exist_or_skip` → `data_on_disk`
- `platform/tools/lob_fact/tests/test_config_paths.py::test_root_exists_on_disk` → `data_on_disk`
- `platform/tools/ch_ingest/tests/test_ch_data_semantics.py::test_reconcile_daily_covers_derived_tables` → `data_on_disk`
- `platform/tools/pan_update/tests/test_config.py::test_pan_update_and_ingest_share_data_roots` → `host_root`
- `platform/tools/lob_fact/tests/test_run_lob_batch.py::test_main_cli_e2e_mini_month` → `tick_paused`（原因：issue #15）
- `research/tools/factor_lib/tests/test_index.py::test_every_yaml_has_mirror_doc_and_name_matches` → `inflight`（挖矿在途；owner=挖矿）
- `research/tools/strategies/tests/test_run_strategy_cli.py::test_real_run_clean_window_2025_03`、`::test_real_run_max_hold_excludes_stale_and_renormalizes` → `known_red`（DQ health 对 2025-03 历史分区 UNKNOWN 拒单，issue #25 已修范围收窄，这两条待团队按新 data_version 复跑后删标记）
- CH 集成相关（deep 要跑的）→ `needs_ch`（对现有 `pytest.mark.integration` 不强制替换；仅给 fast 需排除但无 integration 标记的用例补标）

**验收（命令+期望）：**
- [ ] `pytest platform -q -m "<fast>" --collect-only | tail -1` 与旧 `--deselect` 集**逐条等价**（写对照脚本 `governance/ops/tests/test_markers_equivalence.sh`，对每个被 deselect 的 nodeid 断言：出现在 marker 排除集、且不在 fast 收集结果中）；
- [ ] `pytest platform -q -m "<fast>"` 与 旧命令（含 deselect）**通过数一致**（记录前后 count）；
- [ ] deep 表达式下，`data_on_disk`/`needs_ch`/`host_root` 用例被收集（`--collect-only` 列出）。

**Interfaces（Produces）:** marker 名与表达式（规格 §3/§4），Task 2 直接引用。

- [ ] 提交：`test(markers): 以 markers 取代硬编码 deselect（fast/deep 双档位等价）+ 原因/issue 关联`

### Task 2: `verify.sh` + `ci_env.sh` + Makefile（单一入口）

**Files:** Create `governance/ops/verify.sh`、`governance/ops/ci_env.sh`；Modify `Makefile`（`verify-fast`/`verify-deep`）。

**Interfaces（Consumes: T1 markers）:**
```bash
governance/ops/verify.sh --profile fast|deep [--root DIR]   # 0 过 / 1 失败 / 2 环境不满足
governance/ops/ci_env.sh [--dir DIR]                        # 幂等建 venv + 钉版（唯一出处）
```
- fast：`gates.sh --offline`（Task 3 增）+ `ci_env.sh` + 三组 pytest（fast 表达式）；
- deep：在 `--root`（默认宿主 repo）树上先跑 fast 的**门部分**，再 `make gates` 全量 + CH 集成腿
  （`FACTORLAB_DATA_BACKEND=ch FACTORLAB_ST_DEGRADE=allow FACTORLAB_MINUTE_UNCOVERED=drop pytest -m integration`）
  + 带数据 tools/research（deep 表达式）+ 研究产物门（lint/index/dossier/tidy）；
- 每一步输出 `[verify] step=... rc=...`；失败不早退（收集全部再汇总，便于夜间 issue）。

- [ ] 写 `verify.sh` + `ci_env.sh`；`make verify-fast`/`verify-deep` 薄壳；
- [ ] fast 在**干净 clone**（`git clone --depth 1 . /tmp/opencode/verify-clone && cd 那里 && verify.sh --profile fast`）退出 0；
- [ ] deep 在宿主退出 0（记录总耗时）；
- [ ] 提交：`feat(ci): 单一入口 verify.sh（fast/deep）+ ci_env 钉版单页 + make 薄壳`

### Task 3: `gates.sh --offline` + `ci.yml` 重写（薄壳）

**Files:** Modify `governance/ops/gates.sh`（新增 `--offline` 模式：跳过需要产物区/台账的 G-INDEX-产品/G-ANNOTATE/G-REVIEWS，其余照跑）；Rewrite `.github/workflows/ci.yml`：
一个 job（`verify-fast`）三步：checkout(fetch-depth 0) → `ci_env.sh` → `verify.sh --profile fast`；
失败注解逻辑保留（从 verify 输出提取 FAILED 行发 `::error::`）。
- [ ] 本地：`gates.sh --offline` 在干净 clone 绿；`gates.sh`（全量）在宿主仍绿；
- [ ] push 后 workflow 单 job 绿（含 annotations）；旧三 job 逻辑中的"排除清单注释"删除（迁 markers）；
- [ ] 提交：`ci: ci.yml 薄壳化（调用 verify-fast）+ gates --offline；删除重复钉版/排除清单`

### Task 4: 删除自托管 runner + 夜间 timer + 失败通知

**Files:** Delete `.github/workflows/selfhosted-verify.yml`；Create `governance/ops/nightly-verify.sh`、
`governance/ops/nightly_notify.py`、`~/.config/systemd/user/factorlab-nightly-verify.{service,timer}`（安装脚本
`governance/ops/install_nightly_verify.sh`）；Modify `governance/evidence/reviews/README.md`（验证体系一节）。

- [x] 注销 runner：`GET actions/runners` → `DELETE /actions/runners/<id>`；`systemctl --user disable --now actions-runner`；
      备份目录路径写入 README（恢复指引）；（2026-09-21：runner id=21 已注销，API total_count=0；服务 disabled/inactive；
      目录原地归档；证据 `/tmp/opencode/nightly-verify/`）
- [x] `nightly_notify.py`：marker `<!-- nightly:YYYY-MM-DD -->` 幂等创建 issue（labels kind:process,status:open；
      正文=失败步骤+日志尾 30 行+日志路径+`make verify-deep` 复现命令）；**用 `--force-fail` 注入测试**：手工建一条
      测试 issue 后关闭（记录 URL 于证据）；（2026-09-21：故障注入创建 #32 并关闭；二次注入验证幂等=追加评论；
      单测 15 条 `governance/ops/tests/test_nightly_{notify,verify_sh}.py`）
- [x] 安装并 `systemctl --user start factorlab-nightly-verify.service` 实跑成功（日志落
      `$QUANTRESEARCH_ROOT/results/platform/.nightly/`，记 `last.json`）；（2026-09-21：`NIGHTLY_PROFILE=fast` 经
      systemd 服务实跑 rc=0，7m23s，日志 `20260921-113251.log` + `last.json`；timer 已 enable，下次 2026-09-22 03:00）
- [x] 提交：`ci: 自托管 runner 退役——夜间深检改宿主 timer + 失败自动开 issue`

### Task 5: 验收与收口（控制器）

- [ ] fast：干净 clone 绿 + 上传到 GitHub（push 后 CI 绿）；
- [ ] deep：宿主实跑绿（含 CH 集成腿、研究产物门），耗时与排除清单记录在 `governance/evidence/verification/R38/`；
- [ ] 故障注入：`NIGHTLY_FORCE_FAIL=1` 走通 issue 创建（随后 close）；
- [ ] `make gates` 绿；runner 列表为空；旧 workflow 不存在；
- [ ] 更新 `STATUS.md`（验证体系一页）；提交证据。

## Self-Review

- 覆盖：规格 §2→T2/T3，§3→T1，§4→T2，§5→T4，§6→T5。无占位符。
- 风险：marker 表达式的等价性是 T1 的硬验收（防"排除扩大/缩小"）；deep 的 `known_red` 两条必须能在
  marker 文本中看到 issue 号与"删标记条件"。
