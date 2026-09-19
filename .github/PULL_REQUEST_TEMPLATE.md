## 目标
<!-- 一句话：这个 PR 做什么、对应哪个 issue -->

Fixes #

## 范围与树
- [ ] 一次提交一主题：只动 `platform/` / `research/` / `knowledge/` / `governance/` **其中一棵**（跨树搬迁需注明）
- [ ] 未夹带无关改动（`git diff --stat` 自查）

## 证据（必须）
- [ ] 证据落 `governance/evidence/verification/<轮次>/`：命令 + 原始输出 + 门结果
- [ ] 行为/数值变更附对拍或逐值断言（不是"测试通过了"）
- [ ] 涉及入口/装配/数据接口：真跑一次（真 CH 或真 parquet）并贴输出

## 门与测试
- [ ] 本地 `make gates` exit 0（或说明按子集跑的原因）
- [ ] 相关测试命令与结果（写明 passed/skipped）

## 风险与回退
<!-- 破坏性/不可逆操作：先备份，写明退路 -->
