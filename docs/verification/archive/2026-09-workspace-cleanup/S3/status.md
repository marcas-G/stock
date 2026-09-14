# S3 收口状态（2026-09-12）

## 结果：PASS —— PENDING_POINTER_FIX 窗口已关闭

S2 声明的指针悬空窗口在本阶段结束时关闭：路径全量重指完成、grep 门活跃死链 = 0、
真实读路径冒烟 PASS。

## 1. worktree 迁移（DER-001 程序，git 2.17.1）

- ① `git worktree move` research → `projects/quant-platform-research` ✓
- ② 手工 `mv` main → `projects/quant-platform-main`（2.17 拒绝 move 主工作树）✓
- ③ 手工编辑 `projects/quant-platform-research/.git` gitdir 行（唯一绝对指针）✓
- ④ 验证门四查：worktree list 双新路径 / 双 status 干净 / HEAD=87b960c(main)、446b5dc(research) ✓
- 指针快照原文见 01-pointer-snapshot.log（回滚依据）
- **未运行过 `git worktree prune`**（悬空窗口纪律）

## 2. 项目落位

- `projects/ashare_alpha3`、`projects/quant_core_shim`（emb `pip install -e` 完成：
  `import quant_core` OK，路径 = projects/quant_core_shim）
- `tools/ch_ingest`、`converters/`、`quark_download/` 迁入 research worktree `tools/`
  并提交 research 分支（69be9f3）；ch_ingest 为唯一权威副本（另副本随备份克隆归档）

## 3. 路径重指（每处均验证）

| 位置 | 方式 | 证据 |
|---|---|---|
| lob_fact/config.py | 单点派生 STOCK_ROOT/DATA_ROOT + 四根 | test_config_paths.py 红→绿（5 passed） |
| lob_fact 其余（compact_lob / verify_cancels_sample / audit_w5 / w5_closure.sh / extract_sz_cancels / notes 诊断×8） | config 单点 / 相对定位 converters / 新绝对路径 | py_compile + 04-repoint 日志 |
| ch_ingest 3 文件（ingest_daily / ingest_common / reconcile） | 新绝对路径 | py_compile + grep 门 |
| converters 2 文件（5+3 处常量） | 新绝对路径（daily 拆分精确映射） | py_compile + grep 门 |
| 1m_features/run_1m_feature.py | 新绝对路径（默认值+docstring） | py_compile + grep 门 |
| ashare_alpha3（config.yaml / config.example.yaml / scripts×6 / .venv/bin 生成脚本） | sed 机械替换（非 git，改动留档） | py_compile + grep 门 |

## 4. 验证门

- **py_compile**：research tools 全部 + ashare scripts 全部 通过（05-verify.log）
- **lob_fact 测试套件**：`183 passed`（含新增 test_config_paths.py 5 项）
- **grep 门**：全工作区 py/yaml/sh 旧路径活跃死链 = **0**（豁免：git 历史 md 文档清单见下）
- **真实读冒烟**（06-smoke-and-shim.log）：四根存在 + lob_events 抽样读 + quark zip 可开
  （3 member 齐）+ tick_fact 月目录 + calib 抽样 → ALL PASS

## 5. 备份克隆抢救（REQ-WS-007）

- fsck exit=0；`git fetch <backup> main:refs/heads/archive/local-backup-20260903`
- 验证：`cat-file -t e66b349` = commit ✓；4 提交全在（e66b349/83e457e/418b7c5/a4efabd）
- **该本地存档分支永不 push**（记入 remote-cleanup-checklist.md）
- 备份目录已归档 `_archive/2026-09-12-S3/quant-platform-main.local-backup-20260903`（975M）

## 6. 豁免清单（路径不变但属历史记录，非死链）

- `_archive/**`（归档区，含 tick_rescue 脚本与备份克隆）
- 历史 md 文档中的旧路径引用：`projects/quant-platform-research/tools/lob_fact/notes/*.md`
  （W1-W6 战役备忘）、`docs/superpowers/**` 历史 spec/plan、ashare `MIGRATION_GAP.md` 等
- 规则：新写的文档一律用新路径；历史文档保持原文不改（记录当时事实）

## 回滚

- worktree：逆序 mv + 指针快照原文恢复（01-pointer-snapshot.log）
- 路径重指：git revert（research 分支）或按本文件表格逐项回改（ashare 非 git）
- 备份克隆：从其归档目录逆 mv 回根
