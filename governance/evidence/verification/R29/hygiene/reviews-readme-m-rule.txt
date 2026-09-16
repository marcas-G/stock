== R29 Task6.1: reviews README M 口径（最小 edit）==
--- before (HEAD 基线片段) ---
- **C（Critical）**：错误结果 / 数据损坏 / 未来函数 / 资金安全类，必须修。
- **I（Important）**：正确性风险、契约违反、测试盲区，应修。
- **M（Minor）**：文档、风格、优化项（只登记在各轮 report，不进台账，避免稀释主线）。
--- after ---
- **C（Critical）**：错误结果 / 数据损坏 / 未来函数 / 资金安全类，必须修。
- **I（Important）**：正确性风险、契约违反、测试盲区，应修。
- **M（Minor）**：文档、风格、优化项（**允许 级=M 入台账**——现状 `findings.md` 有 6 行 M；不稀释主线，主线看 C/I）。
--- diff（相对工作区 before，含他人未提交的 Plan G 行不在此片段）---
5c5
< - **M（Minor）**：文档、风格、优化项（只登记在各轮 report，不进台账，避免稀释主线）。
---
> - **M（Minor）**：文档、风格、优化项（**允许 级=M 入台账**——现状 `findings.md` 有 6 行 M；不稀释主线，主线看 C/I）。
--- findings.md M 行计数 ---
6
