# 模拟盘 API 调研（2026-09-17）——可接入选项与推荐路线

**背景**：FactorLab 已有日频/周频策略链（YAML → M7 目标组合 → M8 回测），要接"模拟盘"做
前向纸面/仿真交易。**关键约束**：A 股、日频信号、Linux 服务器、Python；合规口径正在收紧。
**方法**：官方文档 + 实务资料交叉（来源见文末）；标注"待确认"的为 403/限流未取到一手页。

## 1. 结论（推荐顺序）

| 优先级 | 路线 | 一句话 |
|---|---|---|
| **①（推荐）** | **自建 EOD 模拟盘**（复用本平台 M8 语义） | 收盘后跑策略 → 纸面次日 open 成交（含涨跌停/停牌/T+1/费用）→ 持仓与 NAV 台账。**零合规风险、零门槛、与日频策略天然匹配**；未来要真实下单再换执行通道（本报告的 ②③④只是"执行器插件"） |
| ② | **Futu OpenAPI（富途）** | OpenD 支持 Linux（CentOS/Ubuntu）；**A 股（沪深港通标的）支持 Paper Trading**，非港通 A 股亦可 paper（live ✗）；API 免费。前提：富途账户（内地新开户受限，需确认）+ 行情权限 |
| **②b** | **同花顺 SuperMind（原 MindGo）** | **A 股原生、官方模拟链路完整**：网页/客户端**模拟交易**（日级/分钟级撮合、分红送股、webhook 推送）+ 客户端**仿真柜台**（`TradeAPI` + `research_trade`，可把回测代码直接变成模拟/仿真/实盘）。数据平台不限流量。**本地 SDK 仅面向机构**（≥2 年量化经验 + ≥3 人团队 + ≥1 名 6 年 Python）；仿真/研究环境需 Windows 客户端 + 同花顺账号 |
| ③ | **QMT 大客户端内置 Python**（券商版） | 客户端沙箱内跑策略（Windows），仿真/实盘均可；合规上"客户端内"是当前被许可形态。**MiniQMT/xtquant（本地直连）正在被券商清退（2026-07-06 起新开停发；2026-08-21 国金通知存量关闭），不要新押注**；Linux 侧可"信号中转"（本地算信号→文件/DB→QMT 读单） |
| ④ | **PTrade（恒生，券商版）** | 策略托管在券商服务器（仿真环境可申请），需券商开户 + 权限/资金门槛；对外网多封闭（个别券商留 HTTP 桥）。适合"托管式"而非"本机 API" |
| ⑤ | **掘金量化 myquant** | 独立平台，宣传含"仿真模拟 + 实盘"、Python/C++/C# SDK；官网 403 未取到一手细节——**Linux 支持与开通门槛待确认** |
| ⑥ | **Alpaca** | 美股/加密，Paper 免费、REST 简洁（`paper-api.alpaca.markets`）；**不含 A 股**——仅当你要做美股策略 |
| ✗ | 聚宽/米筐 平台内模拟 | 属平台自带模拟，外部程序化接入受限（且近年机构化），不作为接入选项 |
| ✗ | 同花顺"模拟炒股"（moni 网页练手版） | 无开放 API；**同花顺量化能力走 SuperMind（见 ②b）** |

## 2b. 同花顺 SuperMind 实测（2026-09-17，官方帮助文档）

**三条链路**（A 股原生）：

| 链路 | 入口 | 机制要点 |
|---|---|---|
| **模拟交易**（免费） | 网页/客户端 → "python 策略模拟"（`/view/trade.html`） | 日级：9:31 检查 9:30-9:31 成交量（为 0 不下单）；分钟级：**行情用交易所 Level-1，每分钟撮合一次**，市价单以涨跌停价尝试、逐分钟匹配 5 档，未成交顺延至收盘、收盘取消；成交量默认按 `set_volume_limit`（25%）限制；**自动处理分红送股**（不复权真实价格下单）；支持 Webhook 消息推送；交易明细/持仓可导出 |
| **仿真柜台**（更接近实盘） | Windows 客户端"研究环境"（Jupyter）→ `from tick_trade_api.api import TradeAPI` | 完全仿照实盘柜台（委托不立即成交、回报延迟、可能有初始持仓、`position_days=0` 等差异官方已列 6 条）；`TradeAPI(account_id, order_policy=MarketPolicy/LimitPolicy)` |
| **回测代码 1 分钟转模拟/仿真/实盘** | `research_trade(name, source_code, capital_base, frequency='DAILY'/'MINUTE', trade_api=…, signal_mode=True/False, recover_dt=…)` | `trade_api=None`→模拟交易；传 `TradeAPI`→仿真/实盘（**取决于账号是模拟资金账号还是实盘资金账号**）；`signal_mode=True` 先在内部模拟撮合、成交后再转柜台；策略须 9:00 前启动 |

**门槛与限制**：
- 网页/客户端模拟交易：注册同花顺账号即可（免费额度/规则以平台为准）；
- **本地 SDK（本地跑策略）：仅机构**（申请条件：投资机构、≥2 年量化经验、团队≥3 开发、≥1 名 6 年 Python；邮件审核）；
- 仿真柜台/研究环境：需 **Windows 的 SuperMind 客户端** 且账户已登录（Linux 服务器不能直接跑仿真柜台）；
- 平台策略框架是 `init/before_trading/handle_bar` 模式（回测代码可复用）。

**与 FactorLab 的接法**（推荐）：FactorLab（Linux）盘后产出目标组合 → 同步到 Windows 机器 → SuperMind 策略（或研究环境 `research_trade`）读信号执行 → 模拟/仿真/实盘；或基金路径：把 SuperMind 当"执行器"。若要全自动跨机，用文件/DB 中转（同 QMT 信号中转模式）。

## 2c. 网页模拟与 GitHub 生态（2026-09-17 调研）

**结论**：SuperMind **没有公开的"网页模拟"编程 API**——网页侧只有 UI + Webhook 消息推送 + 交易明细导出。
GitHub 上的"类似操作"实际是以下三类，均非 SuperMind 网页 API：

| 项目 | ★ | 最近更新 | 形态 | 适用性/风险 |
|---|---|---|---|---|
| [easytrader](https://github.com/shidenggui/easytrader) | 10.2k | 2026-02 | 同花顺客户端模拟操作 / miniQMT 官方接口 / 雪球组合 / **跟踪 joinquant、ricequant 模拟交易** | 生态最活跃；本质是客户端自动化（Windows），合规口径收紧下脆弱 |
| [ths_trade](https://github.com/skyformat99/ths_trade) | 459 | 2021 | Windows 自动化 `xiadan.exe`，自带 Web API 服务（多策略队列） | 停更、GUI 自动化 |
| [atomat/10jqka-API](https://github.com/atomat/10jqka-API) | 94 | 2020 | 同花顺协议逆向（模拟登录下单/可转债打新） | 停更、高风险 |
| [MindGoWrapper](https://github.com/Jamesits/MindGoWrapper) | 40 | ~2016 | 平台内 Jupyter 回测 wrapper（hack 禁用模块） | 老；平台已改名 SuperMind |

**可选路线（网页模拟相关）**：
- **≈已选定**：用户选定 `Cfu4536/ths_simulated_API` 路线（同花顺**模拟炒股网页** `mncg.10jqka.com.cn` 的 POST 接口）
  —— 设计/计划：`2026-09-17-ths-simulated-api-design.md` + `plans/2026-09-17-ths-simulated-api.md`（Plan T）。
  ⚠️ 该仓库**硬编码了真实会话 Cookie**（含账号信息），**严禁复用**；必须用自己的会话（本地 0600 配置）。
- **A 半自动官方路线**（备选）：SuperMind 策略代码 + Webhook/导出；
- **B GitHub 现成路线**（备选）：聚宽/米筐模拟 + `easytrader` 跟踪；
- **C 自建 EOD 模拟盘**（兜底）：平台内闭环（零门槛），见 §3。

## 2. 对比表

| 平台 | 市场 | 模拟盘 | 接入形态 | 运行环境 | 门槛/费用 | 主要风险 |
|---|---|---|---|---|---|---|
| **自建** | A 股（自定） | 自建纸面撮合 | Python（本平台） | Linux ✓ | 无 | 自担撮合真实度（可用 M8 语义补偿） |
| Futu OpenAPI | 港/美/**A股通**/日/新 | ✓（含 paper） | OpenD + Python SDK | **Linux ✓**（CentOS/Ubuntu） | 富途账户；行情权限另计；API 交易免费 | 内地开户限制；A股通标的范围有限 |
| QMT（大客户端） | A 股/两融/ETF | ✓（仿真） | 客户端内置 Python | Windows（沙箱） | 券商开通（常见资金门槛） | MiniQMT 清退潮；沙箱限库 |
| PTrade | A股/期货/期权/可转债 | ✓（仿真） | 券商托管上传策略 | 券商服务器 | 券商+门槛 | 外网封闭；迁移成本 |
| 掘金量化 | A股/期货/ETF | 宣传✓ | 终端 + SDK | 待确认 | 待确认（仿真常免费） | 一手信息未核 |
| Alpaca | 美股/加密 | ✓ 免费 | REST/SDK | Linux ✓ | 邮箱注册（Paper-only 可） | 无 A 股 |

## 3. 推荐落地设计（自建 EOD 模拟盘，V1）

写在 FactorLab 体系内，**复用现有产物与语义**，不引入新依赖：

```
schedule（收盘后，如 17:30）
  └─ run_strategy --as-of 今日（YAML 策略：日频/周频）
       → target_portfolio（决策日=今日）
  └─ paper_broker（新模块，参照 M8 产物结构）
       · 次日 open 成交：reference=CH daily.open（raw）
       · 涨跌停（stk_limit）/停牌（缺行）/T+1/资金约束：与 M8 同规则
       · 费用：佣金/印花/过户/滑点（同 costs.py）
       · 台账：orders/fills/positions/accounting/valuation/nav_series
  └─ 留痕：runs/paper/<strategy>/<date>/（manifest + sha256）
```

- **为何是 EOD**：你的信号与调仓都是日/周频，EOD 撮合与回测口径完全一致（回测=模拟盘的同一套规则，
  避免"模拟盘与回测两套逻辑"漂移）；盘中实时撮合对这类策略收益边际极小。
- **未来接真实/外部模拟**：把 `paper_broker` 换成执行适配器（Futu SDK / QMT 信号中转 / PTrade 上传），
  其余（信号、风控、台账）不变——即我们已有的"L5 执行层"边界。
- **合规**：程序化交易报告制 + 券商口径收紧（MiniQMT 案例）；自建纸面与"券商许可通道"是安全组合，
  不要走"本地直连绕过券商"。

## 4. 待确认清单（需要你/进一步核）

1. 你是否有/可开 **富途** 账户（内地身份限制）？A股通 paper 的标的覆盖是否够用（不含全部小票）；
2. **掘金量化** Linux 终端/门槛（官网 403）；
3. **PTrade** 你券商是否开通、门槛多少；
4. 若做美股：Alpaca Paper-only（邮箱即可）可直接接；
5. 合规口径：所在券商对"外部信号+人工/半自动下单"的报备要求。

## 5. 来源

- Futu OpenAPI 官方文档（OpenD 平台支持 / Paper Trading 能力表）：https://openapi.futunn.com/futu-api-doc/en/intro/intro.html
- MiniQMT 清退事实核查（2026-08-24）：https://invest.wtsolutions.cn/posts/miniqmt-termination/
- PTrade 社区文档：https://ptrade.wiki/ ；QMT/掘金官网：https://www.myquant.cn/（403）
- Alpaca Paper Trading 文档：https://docs.alpaca.markets/docs/paper-trading
- **同花顺 SuperMind 官方帮助**：模拟仿真 https://quant.10jqka.com.cn/view/help/10 ｜本地SDK https://quant.10jqka.com.cn/view/help/3 ｜API 文档 https://quant.10jqka.com.cn/view/help/4 ｜研究环境-模拟仿真 /view/help/14

**附**：本机 `mcp_bing_search_fixed.py`（223 行语法错误、会误导 opencode 的 web_search）
已按用户要求于 2026-09-17 删除（备份：`/tmp/opencode/mcp_bing_search_fixed.py.deleted-20260917`）；
codex 配置引用的 `mcp_bing_search.py` 未被触碰。
