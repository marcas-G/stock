# Task 15 Step 1：评估单一实现方向裁决（D12）

**裁决：A —— kernel 并入 `factorlab.core.eval`（新 `core/eval/kernel.py`）；
删除 `platform/kernels/quant_core/` 壳与独立 `quant-core` dist；
`adapters/rust_ic.py` → `adapters/ic_kernel.py`；P-6 替换缝以 `ports/eval_kernel.py` 接口文档保留。**

裁决日：2026-09-17（R30 批 2，Task 13 落地后）。

## 备选方案

| 方案 | 内容 |
|---|---|
| **A（选定）** | 内核函数整体迁入 `core/eval/kernel.py`；桥接层 `adapters/ic_kernel.py` 直接调用；`kernels/`、独立 dist、安装/落位断言、pyproject 依赖与 uv.sources 全部删除 |
| B（未选） | `quant_core` 保留为唯一实现（外置包形态），`ic_series/layered` 转调用；保留 `kernels/quant_core` 与安装链 |

## 理由（可核查）

1. **单一代码位置**：`core/eval` 已是评估计算层（`kernel`/`ic_series`/`layered`/`alignment`/`metrics`）。
   A 后 daily/weekly 桥接只依赖 core，不存在"外置独立 dist"的安装/落位断言维护面
   （`reinstall_editable.sh` 降为单包、`gates.sh` G-VENV 反向断言"独立包不得复活"）。
2. **R08 §9 现状核查**：`platform/kernels/quant_core` 内**无任何 Rust**（无 `.rs`/`Cargo.toml`），
   唯一实现即 238 行纯 Python；"Rust 内核未来同包名替换"无立项、无触发条件。B 会继续保留
   这一没有实现主体的双实现/残留命名形态，与 D12"去除双实现与 `rust_ic/kernels` 残留命名"冲突。
3. **Web 曲线/分层输出影响 = 零**（实现前核查）：Web 详情页只消费
   `summary.json`（`evaluation.decile_returns.groups` / `layered_backtest.net_values`）与
   `weekly.parquet`（经 `results_fs` + `ic_series` 计算 IC 曲线）；不 import 内核、
   不依赖内核模块位置。A 是纯搬移（数值/结构不变，见下），B 亦不改变数值——
   但 A 少一套安装链即少一类环境漂移面（如旧 editable finder 指死路径）。
4. **P-6 替换缝不依赖物理位置**：端口是 `EvalKernelPort` 协议 + `ports/eval_kernel.py`
   文档。未来任何实现（含真正外置的加速内核）实现协议即可替换；端口注释已改为
   "单一实现 `core.eval.kernel` 的端口契约"（去 Rust 叙事，中性表述）。

## 实现前影响核查证据

- Web 消费点：`grep -n "import\|results_fs\|ic_series\|charts" platform/src/factorlab/surfaces/web/app.py`
  → 无内核模块 import（见 `task15-decision-grep.txt`）。
- 数值不变：Task 15 Step 2 对拍（`task15-parity-*.json` + `kernel_parity.py`）：
  非 docstring AST 全等（纯搬移，`task15-parity-code-identity.txt`），
  R08 三面板全字段 `rel_tol=1e-9`（实测 max_rel≈9.1e-15；polars 并行归约噪声见
  `task15-parity-nondeterminism.txt`）。
- 迁移后 Web/分层/6 代表 spec 的复跑回归：全量测试（`07-task15-fullsuite.txt`）
  含 `test_web`/`test_e2e_web`/R22 6 代表 spec weekly 值级回归。
