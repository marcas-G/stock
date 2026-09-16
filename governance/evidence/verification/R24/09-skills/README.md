# R24 Task 9：仓外技能同步记录

**改动对象（仓外）**：`/data/students/gaolei/.claude/skills/factorlab-{dsl,data,ch-pipeline,backtest,evaluate}/SKILL.md`
**改动内容**：旧路径 → R24 新路径：
- `quant-platform-main/docs/interface.md`（含旧克隆绝对路径）→ `knowledge/contracts/interface.md`
- `docs/catalog.md` / `quant-platform-main/docs/catalog.md` → `knowledge/contracts/catalog.md`
- `docs/data-ops-playbook.md` → `knowledge/contracts/data-ops-playbook.md`
- `docs/superpowers/specs/`（含旧克隆路径）→ `knowledge/design/platform/specs/`

**验证**：`grep -n "platform/docs\|docs/interface\|docs/catalog\|research/docs\|quant-platform-main\|docs/data-ops\|docs/superpowers" <5 个 SKILL.md>`
→ 0 命中（rc=1）。
**diff**：本目录 `skills-diff.patch`（4 个文件；ch-pipeline 无旧路径、未改）。
**说明**：技能为仓外文件，随仓提交无法覆盖；本记录为验收证据。
