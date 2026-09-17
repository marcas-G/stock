# 分钟因子挖掘战役总结（M1–M8，2026-09-17/18）

## 口径与流程
- 数据：bars_1m（240 bar/日，bars1m_2023_2025 池，~4850 股 × 723 交易日）；adjustment=raw。
- 评估：evaluation v2 **daily/forward_return_1d**（D9/D11），n=723 逐日期。
- 每因子工序：lint → run → 显著性（|t|≥2）→ **D10 冗余/增量**（corr + resic against 库）→
  代码审核（subagent）→ 守卫修复 → 档案/入册。

## 成果
- **minute 参考库 17 员**（全成员互查 |ρ|<0.9、resIC 显著、retention≥0.5）：
  核心反转族（尾盘/竞价/冲击）+ 数据驱动延续族（amihud/cross_count/vol_spike/open_gap 等）。
- 最强三：close_pos_last30（|t|=23.8）、close_auction_premium（22.7）、closing_push（14.7）。
- 修复平台侧问题：7 处分母守卫（防 inf/NaN 传播；amihud 首轮死信号教训）、
  10 处方向修正（原始 IC 符号 vs direction）；数据缺陷登记 pending-items A1/A2。

## 饱和证据（M4–M6 起）
- M4：6 候选 → 2 入册；M5：4 → 1；M6：3 → 0（raw t 达标但 resIC≈0/retention<0.5，全被库吸收）；
  M8：4 → 1。库 r²_lib 普遍 0.17–0.70，**当前 bars_1m 单变量空间已挖尽**。
- 冗余对处置：close_ret×close_auction_premium ρ=1.00（待删）；am_pm×volume_timing ρ=-0.93；
  vol_asym×vol_price_corr 0.77；close_to_vwap×range_position 0.80；premium_x_vol 0.97；
  auction_conc_inter 0.91（r²_lib 0.96，线性组合被库覆盖）。

## 下一步方向（需新维度，非当前 DSL 能力）
1. **tick/L2 数据**（盘口失衡、逐笔撤单率）——`data/raw/20260817` 解包后（pending-items #3）。
2. **跨日分钟特征**（如日内动量跨日衰减）：需分钟引擎支持跨日窗口（现 minute scope 禁）。
3. **daily×minute 交互**：分钟库成员与 daily 库成员的条件组合（cs_ 在分钟 scope 禁用）。
4. 近端衰减成员（event_density/autocorr_micro）的**月度复核**，转负即移出库。
