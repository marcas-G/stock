# R37 porteval（组合评估器）

- spec：knowledge/design/research/specs/2026-09-21-porteval-design.md（V1 参数冻结）
- 实现：research/tools/porteval/{engine.py,run.py,README.md} + tests（7 passed）
- 集成：xscore 流水线 portfolio 步 → 薄壳转发 porteval；Prefect run healthy-narwhal COMPLETED
- 真实信号核对（all42_M0a open/all）：ann=31.32% excess=9.22% IR=1.08 expo=99.9% pos=451 blocked_buys=27
- 分年：2024 +29.0%/基准+20.9%；2025 +39.5%/+33.1%；2026 -3.2%/-10.4%
- 与旧口径差：涨跌停 block 生效后超额 +9.50%→+9.22%（-0.28pp）
