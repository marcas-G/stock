# Task 4 验收证据摘要（2026-09-21，CI/CD 精简）

commit: ab71eb6 `ci: 自托管 runner 退役——夜间深检改宿主 timer + 失败自动开 issue`（已 push origin/restructure/monorepo）

## 1. Runner 退役
- API before：`runner-api-before.json`（total_count=1, id=21 gpu-server-1 online）
- `systemctl --user disable --now actions-runner`：`runner-disable.txt`（disabled/inactive）
- API DELETE：HTTP 204（`runner-delete-http.txt`）
- API after / final：total_count=0（`runner-api-after.json` / `runner-api-final.json`）
- 远端 default branch（restructure/monorepo）workflow 文件 404（已删）
- 目录原地归档：/data/students/gaolei/actions-runner（.runner disableUpdate=true）

## 2. 夜间 timer
- `timer-install.txt`：install → daemon-reload + enable --now
- `timer-state.txt`：enabled；NEXT Tue 2026-09-22 03:00:00 CST；oneshot service Result=success ExecMainStatus=0

## 3. 轻量实跑（NIGHTLY_PROFILE=fast，经 systemd 服务）
- 第 1 次 rc=1：`light-run-1-failed.log` — 唯一失败 = test_heavy_sh nice 叠加（10+10→19）；
  修复 test_heavy_sh 断言为 >=10（nightly 包装语义），证据 `light-run-systemd.txt`
- 第 2 次 rc=0：7m23s，日志 `light-run-2-ok.log`（6/6 step rc=0），`last.json` rc=0/profile=fast；
  部署证据 `light-run-systemd-2.txt`
- 日志路径：/data/students/gaolei/quantresearch/results/platform/.nightly/20260921-113251.log

## 4. 故障注入 + 幂等
- `NIGHTLY_FORCE_FAIL=1` → 创建测试 issue：https://github.com/marcas-G/stock/issues/32
  （`force-fail-run.txt` / `issue-32-before-close.json`；title/labels/marker/正文均符合）
- API PATCH state=closed + 评论"故障注入测试"（`issue-32-closed.json` / `issue-32-comment.json`）
- 二次注入 → 查 open+closed 命中 #32，追加评论不重复建单（`force-fail-rerun.txt`）
- 第一次真实 fast verify 失败的评论（rc=1，step=pytest-governance-ops）也在 #32（`issue-32-comments.json`）
- issue #32 最终 state=closed（3 comments）

## 5. 测试
- `governance/ops/tests/test_nightly_notify.py` 9 条 + `test_nightly_verify_sh.py` 6 条（本地 HTTP stub，真实请求断言）
- `platform/.venv/bin/python -m pytest governance/ops -q` → 129 passed
- `make gates` rc=0（`make-gates.txt`）
