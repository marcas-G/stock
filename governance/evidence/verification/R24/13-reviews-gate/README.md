# R24 / 13-reviews-gate —— R04-Q7 台账门 G-REVIEWS 证据（2026-09-16）

交付物：`governance/ops/check_reviews.py`（stdlib only；`--selftest` 负向自检）
接入：`governance/ops/gates.sh` `structure()` 内 `[G-REVIEWS]`（G-INDEX/G-ANNOTATE 之后）
判据源：R04 报告 §2 P3 + `governance/evidence/reviews/README.md` 词表（open/fixed-claimed/verified/reopened/wontfix/deferred）

## 结果一览

| 验证 | 命令 | 结果 | 原始输出 |
|---|---|---|---|
| 负向 fixture（5 类坏各 1 处） | `check_reviews.py --ledger .../bad-ledger.md` | **exit 1**（重复 ID / 非法状态 / 死引用 / 空修复说明 / 统计不符全命中） | `bad-ledger-run.txt` |
| 正向 fixture（含 I1a/I1b 折叠） | `check_reviews.py --ledger .../good-ledger.md` | exit 0 | `good-ledger-run.txt` |
| 门内负向自检 | `check_reviews.py --selftest` | exit 0（7 类场景） | `selftest.txt` |
| 真实台账 | `check_reviews.py` | exit 0：93 行 finding（R01 13C/50I · R02 2C/9I · R03 0C/8I · R05 1C/3I） | `real-ledger-run.txt` |
| TDD 外置测试（红→绿） | `pytest check_reviews-tdd-tests.py -q` | 8 passed（实现前 8 failed：FileNotFoundError） | `tdd-tests-run.txt` |
| 门集成 | `make gates` | G-REVIEWS 节 + 自检均 ✓（同次 G-INDEX 红为挖矿在途） | `gates-G-REVIEWS-section.txt`、`gates-full.txt` |

## 判据与设计取舍（No Hidden Design）

1. **ID 唯一**：全局；finding 行 = 有 `状态` 列的表里 ID 匹配 `R\d{2}-(分区-)?[CIM]\d+[a-z]?` 的行
   （R21「复查例外」表无状态列，自动排除）。
2. **状态词表**：词表外即红；`fixed-claimed` 必须「修复说明」非空。
3. **引用路径存在**：扫 finding 行所有反引号 span，只认「含 `/` + 已知扩展名/已知仓内前缀」的 token
   （列名对 `pre_close/pct_chg`、股票代码 `000018/000023` 等自动剔除）。
   解析规则：
   - 迁移映射 `MAP_PREFIX`（`docs/reviews|verification` → `governance/evidence/*`；
     `platform/docs` → `knowledge/contracts`；`research/docs` → `knowledge/dossiers`；
     `research/tools` → `platform/tools`；`scripts/` → `governance/ops`），逐条注释在脚本内；
   - 简写解析根 `ROOT_SUFFIXES`（`core/adapters/app/surfaces`→`platform/src/factorlab` 等）与
     工具内简写 `SHORTHAND_SUFFIXES`（≤2 层通配）；
   - `R02|R03/` 并列展开为多路径（全部存在才算过）。
   白名单（注释理由在脚本 `WHITELIST`）：`polars_ta/`（vendor）、`platform/results`（已迁 runs/platform）、
   `projects/ashare_alpha3`（D5 归档）。
4. **统计口径 = 实计**：识别 `X Critical / Y Important`、`XC + YI` 三类写法；
   实计按 finding 计（`I6a`/`I6b` 为同一 finding 的两条审计细化行，折叠为一条——R02 声明 2C+9I 与
   表内 I6a/I6b 的实测口径依据）；声明归属轮次取「同行最近 R 编号 → 最近标题/轮次行 → 最近上文编号」；
   无显式上下文的声明按「是否匹配任一轮实计」核对（R21 注记 `13C + 50I` 即由此核对通过）。
   豁免仅 1 条并注明理由：R01 首行 `11 Critical / 37 Important`（append-only 初始口径，
   EVID C1/C2、STRAT-C3 等后续登记未计入；R04-Q7 实计 13C+50I）。无声明的轮次打印实计（"缺失则报告"）。

## 台账修复（开发团队可写列；评审列未动）

- `findings.md:51`（R01-M8-I2 关键位置）：`core/execution/fills.py:275` →
  `platform/src/factorlab/app/backtest/fills.py:275`（附 R04-Q7 校正说明；旧坐标不再加反引号以免门扫描）。

## 复现

```bash
cd /data/students/gaolei/stock
platform/.venv/bin/python governance/ops/check_reviews.py            # 绿
platform/.venv/bin/python governance/ops/check_reviews.py --selftest # 绿
make gates                                                          # G-REVIEWS 节绿
```
