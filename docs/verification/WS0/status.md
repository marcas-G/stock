# WS0 基线冻结状态（2026-09-12 15:05）

## 结果：PASS（全部基线可复现、留证）

| # | 项 | 结果 | 证据 |
|---|---|---|---|
| 1 | 平台全量 | **2423 passed / 13 skipped**（collected 2436），430.85s | `01-platform-pytest.log` |
| 2 | 研究侧 lob_fact | **183 passed**，6.11s | `02-lob-tests.log` |
| 3 | 位级门（chunk×2 + label 精确 + 双跑 bitwise） | **66 passed**，197.21s | `04-bitwise-catalog.log` |
| 4 | catalog 同源门（含 docs/catalog.md diff==0） | **27 passed** | `04-bitwise-catalog.log` |
| 5 | 分支 sha | main `319fa3e` · research `69be9f3`（main 独有 14 / research 独有 222） | `05-baseline-counts.txt` |
| 6 | 错误文案计数 | `pytest.raises` **692** · `match=` **442** | `05-baseline-counts.txt` |
| 7 | 漂移面 | 5 文件（minute_gate −4/+1、conftest 9、test_e2e_web 9、test_minute_gate −57、test_strategy +377） | `05-baseline-counts.txt` |
| 8 | 磁盘 | /dev/sda5 可用 362,283,212,800 B（96% 已用） | `05-baseline-counts.txt` |
| 9 | RSS 基线 | 2020-01 单月批算 18.2s / 峰值 **3.87 GiB**（小月份；大月份上界见 1m_features README ≈7GB） | `09-rss-baseline.txt` |

## 设计文档落盘

平台 main 分支新增：
`docs/superpowers/specs/2026-09-12-mining-system-refactor-design.md`
（P0-P4 全文 + 五层架构 + 六端口 + 收敛表 + 范围外 + 验证 V1-V17 + 风险）。
本 WS0 即该 spec 的「设计定稿」节点；WS1 起按 spec §10 实施。

## 回滚

无代码改动；删除本目录与（若需）revert spec 提交即可。

## 下一阶段入口（WS1 前置）

- 合并前 research sha 已记录（`69be9f3`）——回滚锚点
- WS1 冲突面已锁定为 5 文件（见表 #7）
