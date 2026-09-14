# bars_1m 漏斗机制（Interface #2）— 实施计划

日期：2026-09-08。配套规格：`docs/superpowers/specs/2026-09-08-factorlab-1m-funnel-design.md`。
状态：已批准开工（用户"开工"放行 + 两语义拍板：TS 窗口=日内窗口 per (code,date)、
交付=机制打通 + 一个真实特征全市场批算）。

## 背景（浓缩，权威见规格文档）

用户 2026-09-07 对齐漏斗架构（dsl-shape §8：层间只传逐日动态池、因子不跨层、
顶层只出信号回测）；日频层已做透。本里程碑把日频机制换数据接口扩到分钟层：
spec 可选 `interface: bars_1m` → 日内窗口算子族（im_*）+ 折日算子族（day_*）
自包含分区表达式（V2 已实测）→ 折日 (date×code) 面板 → 现有评估/artifact 链零放宽
复用。**M1-M8 契约一行不放宽**（frequency 恒 "1d"、SignalMeta EOD、artifact schema
不变）；分钟性只存在于 spec.interface + run_factor_minute 路径。

## WS 分块

| WS | 内容 | 关键文件 | 提交前缀 | 验收点 |
|---|---|---|---|---|
| W1 | 漏斗文档对齐 | 新 spec+plan 2026-09-08；dsl-shape §8 七处互锁改写（层间只传池/因子不跨层）；主 spec :4 状态+§14 行 13+:64 排除句；interface.md :3-11 指针；closeout §11 分钟行 | docs | 全量 pytest 绿；grep 旧耦合措辞消失 |
| W2 | 批读 loader | data/intraday.py `load_bars_1m_codes` + docstring 契约补正（240 网格/三态 session/0 基 index/raw/单位元与股） | feat(data) | ch_db 假库红→绿：批读==单读逐行、空窗空帧、duckdb ValueError、无时间窗门 |
| W3 | 算子族+门+声明 | ops/minute_ops.py（im_*/day_* 自含分区表达式）；registry.py Literal 扩 "im"/"day"；engine/minute_gate.py（新静态门）；compute.py extra_codes 注入；spec.py interface 字段 | feat(engine) | 乱序确定性锁 n_diff=0、日内窗不跨日、day_last==239 行值、四门拒绝测试全红转绿；日频 2382 零回归 |
| W4 | run_factor_minute 全链 | engine/minute.py（新）+ compute.py 最小复用抽提（行为零变化） | feat(engine) | ch_db 假库：day_last(close)==日频 raw 信号全等；labels==日频同窗子集全等；chunk==整段严格相等；240 网格断言；停牌日无行；多输出；canonical code |
| W5 | CLI 分派 | cli/main.py:139 一处 | feat(cli) | CLI run 分钟 spec 与 API 同 schema；日频 CLI 全测试零回归 |
| W6 | 真 CH e2e 收尾 | tests/test_minute_prod_e2e.py（integration+ch_prod）；spec/plan 状态翻转 + 验证记录 | feat(engine) | loader 行数/240 网格对拍、day_last(close)==239 行==daily raw close（f32 容差）、label 全等、summary 元数据；全量含 integration 绿 |
| W7 | 全市场批算（研究侧） | tools/1m_features/run_1m_feature.py + README（研究分支） | research | 对拍自检（工具某月==引擎同窗）→ 2020-01..2026-08 vwap30_bias+open30_amt_share 全史；内存峰值<6GB；断点续跑 |

每 WS 纪律：红测试先行（缺失 API/存根必败）→ 绿 → 全量
`.venv/bin/python -m pytest -q` 后台 → 达标 → commit（Co-Authored-By 尾行）。

## 关键实现事实（实测/核验，勿推翻）

- expr_codegen 分区靠前缀硬编码（expr.py 仅 ts/cs/gp）→ 分钟窗**不能复用 ts_* 通道**；
  im_*/day_* 走 **CL 通道 + extra_codes 注入**（compute.py:154 同款；单字符串直进
  代码头，禁模块别名 import；多行注入形态 W3 首个红测试实测）。
- 自含分区表达式（V2 实测 n_diff=0）：rolling_over (code,date) order_by
  minute_index；day_last = when(minute_index==组max).then(x).max().over(...)。
- bars_1m 契约：每 code-day 恰 240 行（0=09:25…239=15:00），session_type 三态
  0/1/2（平台 docstring 是两态简化，W2 补正），raw 价、amount 元、volume 股，
  缺口全在整日层，flat bar ~3.6% 需公式守卫。
- 评估链只认 {date,code,signal,target} 列契约 → 折日面板复用零新代码。

## 验证

1. 每 WS：红→绿→全量后台绿→commit；engine 新增模块覆盖率 ≥85%（抽查）
2. 存根抽查：W3/W4 各一条——把 im_*/day_* 换成硬编码输出，对应测试必败
3. W6 真 CH 小窗 e2e 三对拍 PASSED（loader 网格 / 引擎折日 vs daily / label 全等）
4. W7：先对拍再全史；产物布局+断点续跑验证；内存纪律（单进程流式、RSS 峰值<6GB）
5. 收尾：interface.md/catalog.md 同步（catalog dump 重生成无漂移）；
   spec+plan 状态行翻转 + 尾部验证记录（dual-backend spec 先例格式）
6. 全量最终：2382+ 新增全绿；push 用户自理
