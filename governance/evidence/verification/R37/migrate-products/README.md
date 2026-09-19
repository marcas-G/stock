# R37 Phase 2 证据：研究产物迁出主仓（QUANTRESEARCH_ROOT）

日期：2026-09-20（02:04–02:45）· 分支 `restructure/monorepo` · 本轮 commits：
`471ba8a`（refactor 迁移+接线）、`869d12b`（ci 拆分）、`ccf0ca4`（docs 同步）、
本证据提交（chore(evidence)）。

## 结论

- **迁移完整守恒**：534 个文件（factor 272 · strategy 3 · composites 9 · dossiers 247 ·
  index 3）全量 sha256 前后一致（`01-after/conservation.txt`，missing=0/mismatch=0/extra=0）。
- **无在途文件被跳过**：迁移前 lsof 无写打开、无 mtime<5min 文件；挖矿 M15 循环
  （`volume_autocorr/gallais_noise/amihud_drift`）在迁移前已结束（`00-before/inflight-check.txt`）。
- **根单点接线**：`QUANTRESEARCH_ROOT`（缺省 `/data/students/gaolei/quantresearch`）——
  平台 `factorlab.config.settings.research_root`；工具
  `research/tools/factor_lib/quantresearch_paths.py`（不依赖 factorlab）。
- **门全绿（make gates rc=0）**：G-INDEX×3 一致、G-LINT 245/0、G-ANNOTATE 231 份、
  G-REVIEWS 绿（历史坐标豁免产物区）；预存红仅剩 2 条（见「未竟」），非迁移引入。
- **平台全量 3841 passed / 18 skipped / 0 failed**（`01-after/platform-full.txt`）。
- **真跑从产物区读 spec**：`factorlab run $QUANTRESEARCH_ROOT/factor/misc/closing_strength_20d.yaml`
  rc=0，IC mean=0.01378、n_weeks=846（真 CH；`01-after/real-run.log`）。

## 迁移映射

| 旧（主仓） | 新（产物区） |
|---|---|
| `research/factor/` | `factor/` |
| `research/strategy/` | `strategy/` |
| `research/composites/`（spec+实现，扩展项） | `composites/`（entrypoint → `composites.implementations.*`） |
| `knowledge/dossiers/` | `dossiers/` |
| `knowledge/index/` | `index/`（`make index` 已在产物区重生成） |

> `research/composites/` 不在任务书 A 的枚举内，但与 `dossiers/composites` 成对门/索引
> 强耦合（entrypoint 相对 spec 祖先根解析）；不迁则单根不成立。故随迁并改
> entrypoint（6 spec + 3 实现），平台 `test_composite_ecosystem` 真跑通过。

## 门与测试（命令 → 输出文件）

| 检查 | 结果 | 证据 |
|---|---|---|
| `make index-check`（三索引 byte-equality + 成对门） | 全过 | `01-after/product-gates.txt` |
| `make lint-factors`（对产物区） | 245 通过 / 0 失败 | 同上 |
| 档案 snapshot（R21 脚本，按 root 扫描） | 231 份齐备 | 同上 |
| `research_tidy.py` | 1 error（预存 `results` 根裸文件 bw.log）+ 1 warning（lab/__pycache__） | 同上 |
| `make gates`（全套结构/数据接口门） | rc=0 全绿 | `01-after/make-gates.txt` |
| 平台全量 `pytest -q` | 3841 passed / 18 skipped / 0 failed | `01-after/platform-full.txt` |
| `make test-research` | platform/tools 879 passed；research/tools 94 passed / 3 failed（均为预存，见未竟）；governance/ops 114 passed | `01-after/test-research.txt` |
| 真跑 factorlab run（产物区 spec） | rc=0；IC/n_weeks 真值 | `01-after/real-run.log` |
| hosted CI 本地模拟（root 缺失） | lint/索引/annotate 显式 SKIP；real-tree 测试 skip、机制测试 18 passed | `01-after/ci-local-sim.txt` |

## CI 拆分

- `.github/workflows/ci.yml`：hosted 无产物区（env 指不存在路径）→
  G-LINT/G-INDEX/G-ANNOTATE 显式 SKIP；real-tree 测试按 root 缺失 skip（新增 skipif）。
- `.github/workflows/selfhosted-verify.yml`：新增「研究产物门」步骤（对
  `QUANTRESEARCH_ROOT` 跑 `make lint-factors` + `make index-check` + 档案快照 + tidy；
  tidy 遗留按 `::warning` 报告不 fake 绿）。

## 未竟 / 遗留（均预存，非 R37 引入）

1. **镜像时效门 1 红**：`momentum_20d/turnrank_top2|top5` 于 2026-09-16 19:06 提交后
   未补档案 → 超 72h STALE（迁移前 git 口径即红；迁移后产物区按 mtime 同判）。
   与 CI 现有 deselect 一致；档案补齐后删 deselect。
2. **strategies 2 红**：`test_run_strategy_cli` 两条 integration 被 2025-03 分区
   health=UNKNOWN 拒（数据面现状，CI 已 deselect）。
3. `research_tidy` 1 error：产物区 `results/` 根裸文件 `bw.log`（2026-09-19 遗留）。
4. 产物区 `lab/__pycache__` warning（另一会话产生，可随时删）。
5. `research/tools/factor_lib/gen_minute_pool.py` 为挖矿未跟踪工具（迁移前后均
   untracked），未随本轮提交。
6. 挖矿在途：M15 试运行（3 个 intraday spec）以 `--accept-quality ...UNKNOWN` 跑，
   末条 amihud_drift 因读取门拒 UNKNOWN 退出 rc=8；产物区迁移未触碰其结果日志。

## 复现

```bash
QR=${QUANTRESEARCH_ROOT:-/data/students/gaolei/quantresearch}
make lint-factors && make index-check
platform/.venv/bin/python governance/evidence/verification/R21/EVID/annotate_factor_archives.py --check
platform/.venv/bin/python governance/ops/research_tidy.py
make gates
FACTORLAB_DATA_BACKEND=ch FACTORLAB_ST_DEGRADE=allow \
  governance/ops/heavy.sh platform/.venv/bin/factorlab run \
  "$QR/factor/misc/closing_strength_20d.yaml" --no-backtest
```

hosted CI（无产物区）的守卫模拟见 `01-after/ci-local-sim.txt`：设置
`QUANTRESEARCH_ROOT=<不存在路径>` 后 lint/索引/annotate 显式 SKIP，real-tree 测试 skip。

