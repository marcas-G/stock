# R23 验收索引（R04 效率快速项 + R05 使用验证 + R05-C1 事故修复）

- 对象：R04 效率提案快速项（§1 P1-P5 / §2 P1-P2 / §3 P0-1/P1-2/P1-3）、R05 使用验证 3 项、R05-C1（P0 内存事故）
- 台账：`docs/reviews/findings.md`（R03 轮 3 六行 + R05 四行，全部 fixed-claimed）

## 最终验收（冻结树，coordinator 实跑）

| 项 | 命令 | 结果 |
|---|---|---|
| 平台全量 | `cd platform && .venv/bin/python -m pytest -q` | **3098 passed / 13 skipped**，801s，exit 0 |
| 研究 T2 | `emb/bin/python -m pytest research/tools -q` | **278 passed / 10 skipped** |
| 研究 T1 | `platform/.venv/bin/python -m pytest -q research/tools/{strategies,ch_ingest,1m_features,ashare_ingest,universe_stages,converters,factor_lib}/tests` | **143 passed** |
| 常驻门 | `bash scripts/gates.sh` | 结构门全绿 + 数据接口门 ENFORCED 全绿（G-ANNOTATE/G-LINT 新增后首次全绿） |
| 因子 lint | `make lint-factors` | **159 通过 / 0 失败**（单进程批跑 ~3.8s，原 552s） |

## 交付映射

| 项 | commit | 关键结果 |
|---|---|---|
| R04 P1 分钟默认分块 | `6985bc5` | 全市场分钟 RSS 34.95GB→6.75GiB，IC 逐 bit 同；默认 20 交易日/块 |
| R04 P2 universe 复用 | `5fcb500` | 全窗 49.7s→36.9s（−26%），逐值 diff=0 |
| R04 P3 解码 str.slice | `3aa06d3` | 20M 行 1.955s→0.384s（5.1×） |
| R04 P4 CH schema 缓存 | `c7fe499` | M8 探针 356→180 查询、system.tables 100→2 |
| R04 P5 listed/YAML 缓存 | `d5524c5` | listed_codes_at 2→1、YAML 解析 2→1 |
| R04 流程 P1/P2 | `99509c6`/`536e519` | lint 552s→3.8s；gates 增 G-ANNOTATE/G-LINT |
| R04 §3 快清 | `76444ee`/`08ab327`/`d1df129` | NameError 修复；断链勘误；陈旧 gitignore 例外删除 |
| R05-I1/I2/M1 | `fa8aa1e`/`928b3ee`/`1c54ea4` | 返回形态标注+Struct 拒绝；spec strict+op_meta 明确；op list --catalog |
| R05-C1 内存护栏 | `2d4cbb2`/`bd7e85c` | RSS 看门狗+RLIMIT_AS+分钟估算门；1KB 阈值 clean abort；默认零行为变化 |
| factor_lib `_` 约定 | `2d34d80`/`a679f8e` | `_pools` 不入索引/族校验；tripwire 只增不删 |

## 证据目录

- `perf/` R04 §1 P1-P5 before/after 基准与逐值对拍
- `flow/` R04 §2/§3（lint 批跑计时、NameError 复现、gates、断链）
- `usage/` R05 I1/I2/M1（Struct 拒绝、spec strict、op catalog）
- `safety/` R05-C1（clean abort/默认不变/硬上限/分块守卫/39 单测）

## 未做（留用户/后续决策）

- R04 §1 P7 测试并行、P9 universe CH 下推、P10 M8 预载、P12 分钟 day_* 下推、P14 面板缓存
- R04 §3 P3-1 死符号清理、P2-x 僵尸文件删除（破坏性）、P4-1 uv lock
- R04 §4 P5 CH ZSTD（≈72GiB，需夜间窗口）、§5 目录重整（方案 A，需决策 D1-D5）
