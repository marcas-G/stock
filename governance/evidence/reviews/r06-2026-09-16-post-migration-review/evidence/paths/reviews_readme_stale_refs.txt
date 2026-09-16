R06-6：governance/evidence/reviews/README.md 过时引用清单（只列出，未改动）
文件：governance/evidence/reviews/README.md
核对时间：2026-09-16 16:4x

1) L9  结构块 `docs/reviews/`（旧根）
   实际：governance/evidence/reviews/；同文件 L20 已有搬迁映射注记，但结构块未同步。
2) L26 序 1「工具迁移…待执行（TM1 可立即）」+ 入口 `r04-efficiency-2026-09-16/tools-migration-plan.md`
   实际：工具迁移已执行（R27，8 工具+lib 已落 platform/tools；git log 71e…/R27 证据存在），状态待改成"已实施"。
   注：该相对路径本身可解析（governance/evidence/reviews/r04-efficiency-2026-09-16/ 存在），
   但前缀坐标依赖 L9 的错误旧根。
3) L28 序 3「目录重整 R24…待执行」+ structure-plan.md
   实际：R24 已完成（本日 14:0x–16:28 多提交），应标已实施。
4) L36 「序 1 的 TM1（Makefile 单解释器）完成后…」——TM1 已完成，条件句已过时。
5) L70 复查命令注释 `make test-research  # T2 (emb) + T1 (平台 venv)`
   实际：R24 单解释器化（platform/.venv 3.13；research/CLAUDE.md "emb 已退役"），
   Makefile test-research 两腿均为平台 venv。注释与实现不符。
6) L63 状态词表/严重度节无路径问题；L69 `make gates` 仍正确。

未过时（核对 OK）：
- L20 搬迁映射注记本身（docs/reviews/ → governance/evidence/reviews/）——映射说明，合法；
- L27/L29/L30 knowledge/design/workspace 路径——已更新；
- L31 `r04-efficiency-2026-09-16/report.md`——相对路径可解析。
