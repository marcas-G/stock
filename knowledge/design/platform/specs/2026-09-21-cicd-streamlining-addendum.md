# CI/CD 精简增补设计（presubmit/postsubmit + build-once）

- 状态：待实施
- 日期：2026-09-21
- 需求源：用户——"三道验证太难维护"、"成熟的 CI 应该怎么跑"；采纳成熟模型：
  **presubmit（云端快检）+ postsubmit（本机深检，夜间/手动）+ build once / promote**。
- 关联：挖矿服务设计 `2026-09-21-factorlab-service-design.md` §8（build-once/stable tag）。

## 1. 目标与非目标

**目标**
1. 验证体系从"2 workflow + 1 runner + 7 处重复钉版 + 7 条硬编码 deselect"降为
   **1 入口（双档位）+ 1 workflow + 1 timer**；
2. 命令单一真相源：workflow / timer / 人都只调 `governance/ops/verify.sh --profile fast|deep`；
3. 排除项从 YAML 硬编码迁到 **pytest markers**（带原因；改名不漂移）；
4. 删除公开仓的自托管 runner（GitHub 官方安全建议），夜间深检改宿主 systemd timer，
   失败自动开 issue。

**非目标**：K8s/多环境流水线、flake 自动隔离（先人工，marker 已留扩展）、
镜像构建流水线（属挖矿服务 spec §8，另行实施）。

## 2. 验证体系（目标形态）

| 档位 | 位置 | 触发 | 内容 | 预算 |
|---|---|---|---|---|
| **fast** | GitHub-hosted runner | 每 push/PR | 结构门离线子集（拓扑/导入/契约/文档路径；产物区相关显式 SKIP）+ platform 全量 pytest（无 CH）+ tools/research（排除 markers）+ governance | <10 min |
| **deep** | 本机（宿主 repo 树） | 夜间 03:00 timer + 手动 | **先跑 fast 全部** + 完整 `make gates`（含产品区门 G-INDEX/ANNOTATE/REVIEWS）+ CH 集成 + 带数据 tools/research | <45 min |
| **本地** | 开发者 | 随手 | `make gates`（18s，工作区态） | 18s |

## 3. markers（替代 deselect；名字冻结）

| marker | 含义 | fast | deep |
|---|---|---|---|
| `needs_ch` | 需要真 ClickHouse | 排除 | **跑** |
| `data_on_disk` | 依赖宿主在盘数据（data/runs 产物） | 排除 | **跑** |
| `host_root` | 断言工作区==宿主根（CI checkout 语义不成立） | 排除 | 跑（deep 在宿主树） |
| `tick_paused` | tick 全线暂停（issue #15；恢复时删） | 排除 | 排除 |
| `inflight` | 挖矿在途已知红（档案时效门；has owner+过期） | 排除 | 排除 |
| `known_red` | 预存红（有 issue 号与原因；修复后删标记） | 排除 | 排除 |

- 声明处：`platform/pyproject.toml` 与 `research/pyproject.toml` 的
  `[tool.pytest.ini_options].markers`（含说明文本）；
- 每个被标记测试**必须在 marker 文本/注释里带原因与关联 issue**；新增排除=新增 marker
  注释（代码内、可 review），不再改 YAML。

## 4. 单一入口

```
governance/ops/verify.sh --profile fast|deep [--root <dir>]
  fast: bash governance/ops/gates.sh --offline   # 云端安全子集（新增 --offline 模式）
        platform venv 建置：governance/ops/ci_env.sh（唯一钉版点）
        pytest platform -m "<fast expr>" ; pytest platform/tools research/tools governance/ops -m "<fast expr>"
  deep: verify.sh --profile fast（在宿主树上，data 标记转为跑）
        完整 make gates ; CH 集成腿 ; 带数据 tools/research ; 研究产物门
退出码：0 全过；1 有失败；2 环境不满足（如 deep 在本机但 CH 不通）
```
- Makefile：`verify-fast` / `verify-deep` 薄壳；
- `ci_env.sh`：venv + 钉版（httpx==0.28.1 / polars==1.44.1 / typer/rich/click/pytest-timeout）
  唯一出处；cloud 与 deep 复用。
- marker 表达式（冻结）：
  - fast：`not needs_ch and not data_on_disk and not host_root and not tick_paused and not inflight and not known_red`
  - deep：`not tick_paused and not inflight and not known_red`

## 5. 删除自托管 runner + 夜间 timer

- 删除 `.github/workflows/selfhosted-verify.yml`；停止/禁用 `actions-runner.service`；
  经 API 注销 runner；runner 目录原地归档（不删数据），README 记"如需恢复"。
- 新增 `factorlab-nightly-verify.service/.timer`（systemd **user**；03:00 CST）：
  - service 执行 `governance/ops/nightly-verify.sh` → `verify.sh --profile deep`
    （经 `governance/ops/heavy.sh`，nice；日志 `$QUANTRESEARCH_ROOT/results/platform/.nightly/<ts>.log`）；
  - 失败 → `governance/ops/nightly_notify.py`：按 marker `<!-- nightly:<date> -->`
    幂等创建/追加 GitHub issue（labels `kind:process`,`status:open`；正文含失败步骤+日志尾+路径）；
  - 成功 → 仅更新 `.nightly/last.json`（不打扰）。

## 6. 验收标准

1. `verify-fast` 在**干净临时 clone**（无产物区/无 CH）退出 0，≤10 分钟；
2. `verify-deep` 在宿主退出 0（tick_paused/inflight/known_red 精确排除，各 marker 均可在测试清单中列出）；
3. timer 实跑一次成功写日志；**故障注入**（`NIGHTLY_FORCE_FAIL=1`）产生 issue（随后关闭测试 issue）；
4. 新 `ci.yml` 在 push 后单 job 绿；`workflow_dispatch` 可手动重跑；
5. runner 已注销（API `actions/runners` 为空）、旧 workflow 已删；
6. `make gates` 全绿；被 marker 排除的测试在 deep/本地可单独指定运行（不丢可跑性）。

## 7. 风险

- marker 误用可掩盖真问题 → 每个 marker 必须带 issue 号，新增需 review（门：gates 增
  检查"marker 必须带原因文本"？本期以 review 纪律执行，门后续加）。
- deep 在宿主树跑（含未提交改动）→ 结果不代表"已推送态"；fast 仍负责干净 checkout 语义。
- 夜间占用本机资源 → heavy.sh 限流 + 03:00 窗口。
