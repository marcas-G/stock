# 开放算子详细方案（2026-09-15）

**这是什么**：FactorLab「拆除算子白名单、库函数全开放 + 语义推断保证」的详细设计与实施计划。
**给谁**：查阅开放算子设计、Plan 1 实施记录与后续缺口；评审流程见
[`governance/evidence/reviews/README.md`](../../../../governance/evidence/reviews/README.md)。
**状态**：设计已评审；Plan 1 的 9 个任务已于 R22 实施并验收。
结果索引：[R22 开放算子证据](../../../../governance/evidence/verification/R22/open-operators-summary.md)。
Plan 2/3 尚未排期，分别跟踪在 [GitHub Issue #10](https://github.com/marcas-G/stock/issues/10)
和 [GitHub Issue #11](https://github.com/marcas-G/stock/issues/11)。

## 文件

| 文件 | 内容 |
|---|---|
| `design.md` | 设计规格：开放面 / 算子生命周期 / 截面表达（建议）/ 保证体系 / 差距 G1-G9 / 验收标准 + §13 Spike 实测 |
| `plan.md` | Plan 1 历史实施清单：9 个任务；完成状态与偏差以 R22 证据索引为准 |
| `evidence/` | 三个验证 spike 的可复跑脚本（算子可用率 / polars 方法面 / 截断重放） |

## 关键结论（先读这个）

1. **现状**：库里已装 440 个函数（419 唯一名，polars_ta 三库），平台只放出 55 个，约 400 个写不出来（写成公式即报"未知算子"）。
2. **Spike 实测**：
   - 算子真实可用率预计 **350~400**（粗模板冒烟 81%，失败多为模板参数类型问题）；
   - polars `Expr` 223 个公开方法：151 逐元素 / 37 窗口 / **35 个需人工判定**（默认拒绝 + 指引）；
   - **截断重放不能当因子级保证**——每个截断点只覆盖其前 k 天（k=泄漏跨度），远离截断点的局部泄漏抓不到；已修正为「算子准入密集探测 + 因子级冒烟」，主保证靠静态推断。
3. **三条硬约束**：core 纯净（分类表必须是纯数据模块）；152 spec 零迁移；未来函数门全形态。
4. **执行注意**：工作区有 R21 并发改动；本计划所有提交按路径精确 `git add`，只动计划声明的文件。

## 实施顺序（Plan 1）

```
Task 1 OpMeta 分类模型 → Task 2 polars_ta 全量分类表 → Task 3 polars 方法分类表
→ Task 4 统一语义推断 pass → Task 5 未来门统一 → Task 6 窗口/分块统一
→ Task 7 拆除注册闸门 + 分区绑定 → Task 8 lint 静态管线 → Task 9 零迁移回归
```

## 后续计划（Plan 2/3，另立）

- **Plan 2：算子生命周期** —— 命名算子文件、conformance 套件（含小样本密集截断）、算子档案、插件元数据；
- **Plan 3：截面表达** —— `by=` 分组机制 + agg/rank/clip/cut/dist/proj/mask 原语 + 数据可用性检查。
