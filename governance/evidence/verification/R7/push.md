# R7 收口日志（2026-09-15）

## 推送
  git push -u origin restructure/monorepo   → [new branch] 6aa82fe（普通 push，无 force）
  默认分支切换：API PATCH default_branch=restructure/monorepo → HTTP 200

## 远端权威复核（git ls-remote）
  refs/heads/main                  ad17f3b  （保留 30 天）
  refs/heads/research              eec9990  （保留 30 天）
  refs/heads/restructure/monorepo  6aa82fe  （默认）
  refs/heads/workspace             a045d7d  （保留 30 天）

## 门（本轮全绿）
  平台全量: 2513 passed / 13 skipped / 0 failed（见 platform-pytest.log）
  研究侧: 203 passed / 2 skipped（emb）；T1: 35 passed（平台 venv）
  结构门: 全绿（G-COPY/G-BOUNDARY/G-LEGACY=0/G-PATHS/G-IMPORTS/G-INDEX/G-VENV）
  真实链路: check-day max|Δ|=0；读侧对照 11/11；金样 pins 14/14
  数据零改动: data/ 当日改动 0 条

## 30 天保留期后（2026-10-15+）的收尾程序
  1) 先删旧 main/research/workspace（本地 git branch -d + 远端 push --delete）
  2) 再把 restructure/monorepo 推成 main（此时 main 已不存在 → 创建而非 force）
  3) 删除 restructure/monorepo 分支名，本地分支改名 main
  顺序硬约束：先删旧 main 再推新 main，避免任何 force push。
