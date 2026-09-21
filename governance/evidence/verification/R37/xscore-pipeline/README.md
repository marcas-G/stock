# R37 xscore 流水线（Prefect 3）

- 安装：research/.venv（uv，prefect 3.8.6 + numpy + pyyaml）
- server：systemd user unit prefect-server.service（UI http://127.0.0.1:4200，active）
- 首跑：configs/m0-split.yaml（daily10/minute32/all42 × M0a × exec{open,close} × domain{all,Q1Q3}）
- 缓存复跑：第二次全 Cached、秒级完成；UI 记录 observant-terrier COMPLETED
- 产物：quantresearch/results/2026-09-xscore-pipeline/m0-split/{scores/*,REPORT.md}

REPORT 摘要（open 口径）：
```
| daily10_M0a | open/Q1Q3 | 26.24% | 22.66% | 2.79% | 0.46 | 0.0711 | 13.5 |
| daily10_M0a | open/all | 19.31% | 19.68% | -0.62% | -0.04 | 0.0711 | 13.5 |
| minute32_M0a | open/Q1Q3 | 33.35% | 22.66% | 8.14% | 1.14 | 0.0978 | 17.4 |
| minute32_M0a | open/all | 29.70% | 19.68% | 7.57% | 0.87 | 0.0978 | 17.4 |
| all42_M0a | open/Q1Q3 | 34.30% | 22.66% | 9.05% | 1.28 | 0.0977 | 18.8 |
| all42_M0a | open/all | 31.64% | 19.68% | 9.50% | 1.11 | 0.0977 | 18.8 |
```
