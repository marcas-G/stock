# R18 `quant_core` 内核包收编（platform 树，2026-09-15）

用户指令："不能直接合并吗…拆解重构，这两个感觉是历史遗留问题"（指 `projects/` 下两块）。
本轮处理 **`projects/quant_core_shim`**：从 gitignore 的本地目录收进平台树，
落 **`platform/kernels/quant_core/`**（= 内核发行物的**唯一声明点**；**不能**进 `platform/src/`，
否则 `packages.find where=["src"]` 会把 `quant_core` 并进 factorlab 发行物，
与"Rust 内核将来同名包替换"直接冲突）。

## 1. 为什么先做这块

两块历史遗留里，这块**最脆且最急**：

- 两个 venv 以 **editable + 写死绝对路径**引用它（`__editable___quant_core_0_1_0_finder.py`
  的 `MAPPING['quant_core'] = '/data/students/gaolei/stock/projects/quant_core_shim/quant_core'`），
  目录一动就断（S5 有过 26 个测试 `ModuleNotFoundError` 的先例）；
- 它的**契约文档与对拍测试在活跃树里根本不存在**——只见于 git 侧枝（实体副本在
  `_archive/2026-09-12-S3/`，**2026-10-12 到期清空**）。

## 2. 做了什么

| 步 | 内容 |
|---|---|
| 取回契约资产 | `git show a4efabd:{docs/superpowers/specs/2026-08-26-quant-core-contract.md,tests/test_quant_core_shim.py}` → 入树；**跨枝取件**（`a4efabd` 不是 HEAD 祖先，仅经 tag `pre-monorepo/local-backup-20260903` 可达——恢复命令与 sha256 都写死在 `00-cross-branch-source.txt`） |
| 测试修 1 处 | `from factorlab.eval.ic_series import weekly_ic` → `factorlab.core.eval.ic_series`（R2 三分后的路径）；另加一条**契约文档存在性**断言（防指针再悬空） |
| 契约文档 | 加**勘误与位置变更头块**（6 条：取回来源/新位置 = `platform/kernels/quant_core`/两处 class 路径变更/**§4.1 周序号 1-based 消歧**/target 固定 5d 的桥接覆盖/锚点测试已回）；**正文一字不改** |
| 搬迁 | `platform/kernels/quant_core/{pyproject.toml, quant_core/__init__.py}`（与源**逐字节相同**：`938a2a28…` / `8332f389…`；egg-info 与 `__pycache__` 不入树） |
| 重装 | 平台 venv **没有 pip**（uv 建）→ `uv pip uninstall quant-core` + `uv pip install -e platform/kernels/quant_core --no-deps --no-build-isolation`；快照里零 `projects/` 残留 |
| 删 emb 安装 | research 侧 `.py/.toml/.yaml` 对 `quant_core` **零命中** → 删 emb 的三个安装产物（**不碰** `easy-install.pth`，它挂的是 BERT-pytorch）；验证 = `import quant_core` → ModuleNotFoundError；恢复命令一行备查 |
| 删旧目录 | `rm -rf projects/quant_core_shim` 后 `import quant_core` 仍解析到新位 → 证明**不依赖**旧路径 |
| 门 | `scripts/gates.sh` G-VENV 增 **quant_core 正向 + 反向**断言（落位 + `"projects/" not in`）；恢复的测试加文档存在性断言 |
| 新脚本 | `scripts/reinstall_editable.sh`（**不是** venv 重建脚本：只做两个 editable 重装 + 落位断言 + 可选 freeze 快照） |
| 文档 | shim docstring 3 处悬空指针、`platform/CLAUDE.md`、`platform/README.md`（旧 `-e ../quant_core_shim`）、根 `README.md`/`CLAUDE.md`、`docs/directory-conventions.md §3`、`docs/data-map.md C3`、S3 归档 status 补记"已入 git"、pending #18/#19 |

## 3. 验证（真跑，逐条可复算）

| 项 | 结果 | 证据 |
|---|---|---|
| 取回的两文件与归档副本 sha256 | **逐字节相同**（`8d5f5181…` / `092420b1…`） | `00-cross-branch-source.txt` |
| 契约锚点测试（取回后） | **11 passed**（10 项取回 + 1 项存在性） | `01-recover-assets.txt` |
| shim 纯移动核验 | `pyproject.toml` `938a2a28…`、`__init__.py` `8332f389…` **逐字节相同** | `02-shim-move.txt` |
| 落位（正向 + 反向） | 落 `platform/kernels/quant_core`，**零** `projects/` 残留 | `02-shim-move.txt` |
| 搬迁后定向测试 | **19 passed**（契约 11 + 桥接 8） | `02-shim-move.txt` |
| emb 删装 | `ModuleNotFoundError: No module named 'quant_core'` | `03-emb-uninstall.txt` |
| **G-VENV 负向自检** | 临时把 finder 指回 `projects/_negtest/…`（已确认真能解析）→ 门**两条都红**、`exit=1`；还原后全绿 | `06-gvenv-selftest.txt` |
| `reinstall_editable.sh` 真跑 | 两 editable 落位断言通过；freeze 快照 **72 包**（其中 7 个 pyproject 未声明 → 重建不可复现的实证） | `04-reinstall.txt` / `04-venv-freeze.txt` |
| 全门 | **exit=0：结构门全绿 + 数据接口门 ENFORCED 全绿** | `05-gates.txt` |
| 平台全量 | **2570 passed / 13 skipped / 0 failed**（446.77s；原基线 2559 → +11 = 取回的契约测试 10 + 存在性 1） | `07-platform-full.log` |

## 4. 偏差与抓回

| 偏差 | 暴露方式 | 处置 |
|---|---|---|
| **负向自检第一次是假绿**：`sed` 没匹配上 finder 的 MAPPING 行（grep 复核发现文件未变），那一次的"全绿"不能算证据 | 复核补丁是否真的落地（`grep MAPPING`） | 改用 python 精确改写 + 断言锚点存在，**重做**；这次门两条都红（见上） |
| 差点把"一次命令装两个 editable"写进 CLAUDE.md 而未验证 | 证据纪律 | 先真跑 `uv pip install -e platform -e platform/kernels/quant_core` → `Installed 2 packages` ✓，才写进文档 |

## 5. 仍未做（不静默）

- 平台全量测试会真连 CH 建/删 `factorlab_test_<uuid>` 库（状态变更）；本轮定向 3 文件是硬证据，
  全量结果另记（`07-platform-full.log`）；
- **venv 不可从声明复现**（pending #18）与 **`check_dataiface.SKIP_PARTS` 缺 `.venv`**（pending #19）
  均已登记，前者不修（代价大），后者随 R20 同批修；
- `ashare_alpha3` 的收编（数据侧 → `ashare_ingest/`、股票池段 → `universe_stages/`）是 R19/R20。
