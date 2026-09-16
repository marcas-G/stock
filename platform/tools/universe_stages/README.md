# universe_stages

股票池段研究工具：按因子值多级构建股票池。

- 第一层 `v4`：全市场月度粗筛，输出 `v4_top300_local` / `v4_top100_local` / `v4_eligible_local`。
- 第二层 `sas`：基于 `bars_1m` 动态聚合 5m，输出卖压冲击特征与事件。
- 第三层 `tick`：只对第二层候选读取逐笔成交，输出事件前后微观结构特征。

本工具是 `projects/ashare_alpha3` 股票池段在 R20 的收编落位。数据侧收编见
`platform/tools/ashare_ingest/`；原始 V4 迁移缺口记录见 `MIGRATION_GAP.md`。

## 路径纪律

- 事实库路径不在 `config.yaml` 里，一律经 `universe_paths.py` →
  `factorlab.core.factio.paths`（支持 `FACTORLAB_STOCK_ROOT` 换根）。
- 工具内不得出现工作区绝对路径前缀。
- 工具模块名 `universe_paths` 与 `ashare_ingest/datapaths.py` 故意不同，避免
  G-TOPO 因同名模块跨工具解析判红。

## 运行

```bash
# 第一层：单期
python scripts/run_layer1.py --scan-date 2026-07-01

# 第一层：按 golden 股池中的历史 scan/factor 日期批量跑
python scripts/run_layer1.py --from-golden --start 2020-01-01 --end 2026-07-31

# 第一层 parity
python scripts/validate_layer1_parity.py --scan-date 2026-07-01

# 尾部捕获审计
python scripts/tail_capture_audit.py

# 第二层
python scripts/run_layer2_sas.py --scan-date 2026-07-01

# 第三层（依赖第二层产物）
python scripts/run_layer3_tick.py --scan-date 2026-07-01 --top-k 50
```

## 测试

单解释器（平台 venv 3.13）：

```bash
platform/.venv/bin/python -m pytest platform/tools/universe_stages/tests -q
```

依赖真实数据源的集成路径当前不跑：fundamentals 源缺失（pending #4）、`20260817` tick
归档未解包（pending #3）、golden 股池生成链未留存（pending #9）。各脚本入口先跑
`universe_paths.preflight_layer{1,2,3}()`：缺源时 `MissingInput` 会点名**具体路径 + 获取路径**
（不再裸 `FileNotFoundError`）。逐层可执行状态见 `MIGRATION_GAP.md` 的 Executable status 表。
