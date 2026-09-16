# FactorLab 开放算子底座 实施计划（Plan 1）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 拆除算子白名单闸门：库内全部函数（polars_ta 三库 + polars 方法）可直接写；每个调用可解析"分区 + 窗口"语义并据此做分区绑定、窗口推导与未来函数检查；存量 152 个因子零迁移。

**Architecture:** 新增"分类元数据"层（OpMeta：partition/window/mask_args）与"统一语义推断"pass（NodeInfo 自底向上），替换现有"名字前缀正则 + 未注册拒绝"的碎片逻辑；codegen 前做规范化改名（canonical 名 + import 注入），沿用 expr_codegen 前缀分类器，不改 vendor。

**Tech Stack:** Python 3.13 / polars / expr_codegen 0.16.6 / pytest；平台五层架构（core 纯净、I/O 在 adapters）。

**Spec:** `docs/reviews/2026-09-15-open-operators/design.md`（§4 开放面 / §5 算子生命周期 / §7 保证体系 / §9 G1-G4 / §13 Spike 结果）

> **勘误（2026-09-16，实测）**：本计划中的 `ts_quantile` 示例**不存在于 polars_ta 0.5.17**（团队 R22 实施时已记录
> 偏差并替换为 `ts_arg_max`/`ts_corr`/`ts_weighted_mean`/`BBANDS`，见 `docs/verification/R22/open-operators-summary.md`
> 与 `R22/02-ta-catalog/README.md:29-32`）。下文出现 `ts_quantile` 的测试名/断言均为原始计划文本，实施以 R22 替换为准。
> 另：`BBANDS` 返回 Struct 三条带（非标量信号），用法与限制见 R05-I1。

## Global Constraints（来自 spec 与仓库纪律）

- **core 纯净**：`factorlab.core.*` 禁止文件 IO / 三方数据库 import（`tests/test_architecture.py` 双门）；生成的分类表必须是**纯 Python 数据模块**，不得在 core 内读 JSON。
- **零迁移**：152 个存量 spec 行为逐值不变；`ts_/cs_/gp_` 前缀保留为语法糖。
- **TDD**：先写失败测试再实现；测试须能识别存根（硬编码必败）。
- **门与基线**：改动后 `make gates` 全绿；平台全量 pytest 基线 2570 passed / 13 skipped（R18 基线，不得回退）；lint 152/152。
- **工作区状态**：R21 修复（`close[-1]`/常量折叠门、staleness 等）已在工作区未提交——**不得回退**；提交时只动 `platform/` 树（一次提交一棵树）。
- **Spike 结论约束**：polars_ta 三库 440 函数（419 唯一名），目标实际可用 **350~400**；分类表须**按签名感知**生成；polars 方法 223 个中约 **35 个须人工判定**（默认拒绝）；截断重放只用于算子准入，不在本计划做因子级默认开关。
- 内存纪律：16GB 无页面文件；测试不得引入全市场重算。

---

## 文件结构（本计划落点）

| 动作 | 路径 | 职责 |
|---|---|---|
| 新建 | `platform/src/factorlab/core/ops/classification.py` | `OpMeta` 模型 + 查询 API（纯数据，无 IO） |
| 新建 | `platform/src/factorlab/core/ops/_generated_ta_ops.py` | 生成的 polars_ta 分类表（纯数据模块） |
| 新建 | `platform/src/factorlab/core/ops/_generated_polars_methods.py` | 生成的 polars 方法/访问器分类表（纯数据模块） |
| 新建 | `platform/scripts/gen_op_catalog.py` | 分类表生成器（签名感知 + 人工覆盖清单 + `--check`） |
| 新建 | `platform/src/factorlab/core/engine/semantics.py` | 统一语义推断 pass（NodeInfo） |
| 修改 | `platform/src/factorlab/core/engine/partitions.py` | 旧门收敛为新推断的薄封装（保留报错文案与行:列） |
| 修改 | `platform/src/factorlab/core/engine/compute.py` | 接入推断：窗口/分块/unbounded 改由 lookback 驱动；规范化改名与 import 注入 |
| 修改 | `platform/src/factorlab/core/ops/registration.py` | 注册面由分类表驱动（不再白名单） |
| 修改 | `platform/src/factorlab/surfaces/cli/main.py` | `lint` 接入完整静态管线 |
| 测试 | `platform/tests/test_op_classification.py` | 分类模型/生成表一致性 |
| 测试 | `platform/tests/test_semantics.py` | 推断 pass 全形态 |
| 测试 | `platform/tests/test_future_gate_v2.py` | 未来函数全形态（含 R21 用例） |
| 测试 | `platform/tests/test_open_ops_e2e.py` | 库函数直写端到端（`ts_quantile` 等） |
| 测试 | `platform/tests/test_regression_152.py` | 存量 lint 152/152 + 抽样值级回归（integration） |

---

### Task 1: OpMeta 分类模型与查询 API

**Files:**
- Create: `platform/src/factorlab/core/ops/classification.py`
- Test: `platform/tests/test_op_classification.py`

**Interfaces:**
- Produces:
  - `OpMeta(name: str, partition: Literal["el","ts","cs","gp","im","day"], window: int|str|None, mask_args: tuple[int,...], source: str, canonical: str)`（frozen dataclass）
  - `Catalog.get(name) -> OpMeta | None`
  - `Catalog.add(meta: OpMeta) -> None`（重复名报错，显式覆盖需 `replace=True`）
  - `Catalog.name_sets() -> dict[str, set[str]]`（expr_codegen 分类器用）
  - `window_spec(meta, args) -> int | None | "unbounded"`：`None`/int/`"${param}"`/`"arg:N"`/`"unbounded"` 求值
  - 单例 `default_catalog() -> Catalog`

- [ ] **Step 1: 写失败测试**

```python
# platform/tests/test_op_classification.py
import pytest
from factorlab.core.ops.classification import OpMeta, Catalog, window_spec

def test_add_and_get():
    c = Catalog()
    c.add(OpMeta("ts_mean", "ts", "arg:1", (), "builtin", "ts_mean"))
    assert c.get("ts_mean").partition == "ts"
    assert c.get("nope") is None

def test_duplicate_rejected():
    c = Catalog()
    c.add(OpMeta("x", "el", None, (), "builtin", "x"))
    with pytest.raises(ValueError, match="重复"):
        c.add(OpMeta("x", "ts", "arg:1", (), "builtin", "x"))

def test_name_sets_for_classifier():
    c = Catalog()
    c.add(OpMeta("ts_mean", "ts", "arg:1", (), "builtin", "ts_mean"))
    c.add(OpMeta("cs_rank", "cs", None, (0,), "builtin", "cs_rank"))
    ns = c.name_sets()
    assert "ts_mean" in ns["ts"] and "cs_rank" in ns["cs"]

def test_window_spec_arg_position():
    meta = OpMeta("ts_mean", "ts", "arg:1", (), "builtin", "ts_mean")
    import ast
    call = ast.parse("ts_mean(close, 20)").body[0].value
    assert window_spec(meta, call.args) == 20

def test_window_spec_unbounded():
    meta = OpMeta("ts_cum_sum", "ts", "unbounded", (), "builtin", "ts_cum_sum")
    assert window_spec(meta, []) == "unbounded"

def test_window_spec_param_resolved_by_caller():
    meta = OpMeta("my_op", "ts", "${win}", (), "user", "my_op")
    assert window_spec(meta, [], params={"win": 30}) == 30
    assert window_spec(meta, [], params={}) is None  # 无法求值 → 调用方报错
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd platform && .venv/bin/python -m pytest tests/test_op_classification.py -q`
Expected: FAIL（`ModuleNotFoundError: factorlab.core.ops.classification`）

- [ ] **Step 3: 最小实现**

```python
# platform/src/factorlab/core/ops/classification.py
from __future__ import annotations
import ast
from dataclasses import dataclass, replace
from typing import Literal

Partition = Literal["el", "ts", "cs", "gp", "im", "day"]
WindowSpec = int | str | None

@dataclass(frozen=True)
class OpMeta:
    name: str
    partition: Partition
    window: WindowSpec = None          # None=无窗 | int | "arg:N" | "${param}" | "unbounded"
    mask_args: tuple[int, ...] = ()
    source: str = "builtin"
    canonical: str = ""                # 生成代码里的规范名（空=同名）

    def __post_init__(self):
        if not self.canonical:
            object.__setattr__(self, "canonical", self.name)

class Catalog:
    def __init__(self) -> None:
        self._t: dict[str, OpMeta] = {}

    def add(self, meta: OpMeta, *, replace: bool = False) -> None:
        if meta.name in self._t and not replace:
            raise ValueError(f"重复登记算子: {meta.name}")
        self._t[meta.name] = meta

    def get(self, name: str) -> OpMeta | None:
        return self._t.get(name)

    def all(self) -> list[OpMeta]:
        return list(self._t.values())

    def name_sets(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {"ts": set(), "cs": set(), "gp": set()}
        for m in self._t.values():
            if m.partition in out:
                out[m.partition].add(m.name)
        return out

def window_spec(meta: OpMeta, args: list[ast.expr], params: dict | None = None) -> int | str | None:
    w = meta.window
    if w is None or w == "unbounded":
        return w
    if isinstance(w, int):
        return w
    if w.startswith("${") and w.endswith("}"):
        key = w[2:-1]
        v = (params or {}).get(key)
        return v if isinstance(v, int) else None
    if w.startswith("arg:"):
        pos = int(w.split(":")[1])
        if pos < len(args):
            a = args[pos]
            if isinstance(a, ast.Constant) and isinstance(a.value, int) and not isinstance(a.value, bool):
                return a.value
        return None
    return None

_default: Catalog | None = None

def default_catalog() -> Catalog:
    global _default
    if _default is None:
        from factorlab.core.ops._generated_ta_ops import build_ta_catalog
        from factorlab.core.ops._generated_polars_methods import build_polars_catalog
        _default = Catalog()
        build_ta_catalog(_default)
        build_polars_catalog(_default)
    return _default
```

（`_generated_*` 在 Task 2/3 创建；本 Task 测试不触碰 `default_catalog()`，先留 lint 可过的最小引用。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd platform && .venv/bin/python -m pytest tests/test_op_classification.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add platform/src/factorlab/core/ops/classification.py platform/tests/test_op_classification.py
git commit -m "feat(platform): OpMeta 分类模型与查询 API（开放算子底座 1/8）"
```

---

### Task 2: polars_ta 全量分类表生成器与产物

**Files:**
- Create: `platform/scripts/gen_op_catalog.py`
- Create（生成产物）: `platform/src/factorlab/core/ops/_generated_ta_ops.py`
- Test: `platform/tests/test_op_classification.py`（追加）

**Interfaces:**
- Consumes: `Catalog`, `OpMeta`（Task 1）
- Produces: `build_ta_catalog(catalog: Catalog) -> None`（把生成表中 `usable=True` 的条目注册进 catalog）

**生成规则（签名感知，Spec §5.2 / Spike 1/2）：**
1. 遍历 `polars_ta.prefix.{wq,ta,tdx}` 的函数；
2. `ts_/cs_/gp_` 前缀 → 对应分区；`window` 取"第 2 个位置参数若是 int"→ `arg:1`，否则 `None`；
3. 无前缀且全大写（TA 风格）→ 默认 `el`，但**信号签名含 ≥2 个必需参数且第 2 个为 int** → `ts` + `arg:1`；
4. 调用冒烟（签名生成参数模板）失败的 → `usable=False` + 原因，不注册；
5. 人工覆盖清单 `MANUAL_OVERRIDES`（本 Task 先放空 dict + TODO 注释清除后填充规则：逐条注明理由）——**不允许 TBD 残留**，若首轮生成无歧义则为空。
6. `--check`：重新生成与产物字节一致，否则 exit 1（模仿 `build_index.py --check`）。

- [ ] **Step 1: 写失败测试（产物一致性 + 抽查条目）**

```python
# 追加到 platform/tests/test_op_classification.py
import subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # platform/

def test_generated_ta_catalog_is_up_to_date():
    r = subprocess.run([sys.executable, str(ROOT / "scripts/gen_op_catalog.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr

def test_ta_catalog_key_entries():
    from factorlab.core.ops.classification import Catalog
    from factorlab.core.ops._generated_ta_ops import build_ta_catalog
    c = Catalog(); build_ta_catalog(c)
    assert c.get("ts_quantile").partition == "ts"
    assert c.get("ts_quantile").window == "arg:1"
    assert c.get("ts_cum_sum").window == "unbounded"
    assert c.get("BBANDS") is not None            # 签名感知：第二个参数为 int → ts
    assert c.get("ts_mean").partition == "ts"

def test_ta_catalog_size_floor():
    from factorlab.core.ops.classification import Catalog
    from factorlab.core.ops._generated_ta_ops import build_ta_catalog
    c = Catalog(); build_ta_catalog(c)
    usable = [m for m in c.all() if m.source == "polars_ta"]
    assert len(usable) >= 350                       # Spike 1 结论：350~400
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd platform && .venv/bin/python -m pytest tests/test_op_classification.py -q`
Expected: FAIL（`_generated_ta_ops` 不存在 / 脚本不存在）

- [ ] **Step 3: 实现生成器**

```python
# platform/scripts/gen_op_catalog.py
"""生成 polars_ta 算子分类表（纯数据模块）。用法：--check 校验一致性。"""
from __future__ import annotations
import inspect
import sys
from pathlib import Path

import polars as pl

OUT = Path(__file__).resolve().parents[1] / "src/factorlab/core/ops/_generated_ta_ops.py"
MODULES = {"wq": "polars_ta.prefix.wq", "ta": "polars_ta.prefix.ta", "tdx": "polars_ta.prefix.tdx"}
MANUAL_OVERRIDES: dict[str, dict] = {}  # 人工覆盖：{"name": {"partition": ..., "window": ..., "reason": ...}}

def _required(fn):
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return None
    return [(i, p) for i, p in enumerate(sig.parameters.values())
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
            and p.default is inspect.Parameter.empty]

def _second_is_int(fn) -> bool:
    req = _required(fn)
    if not req or len(req) < 2:
        return False
    p = req[1][1]
    return p.annotation is int or p.name.lower() in ("n", "window", "d", "period", "length", "lag", "timeperiod")

def _smoke_ok(fn) -> bool:
    req = _required(fn)
    if req is None:
        return False
    args = []
    for i, p in req:
        n = p.name.lower()
        if i == 0:
            args.append(pl.col("close"))
        elif p.annotation is int or n in ("n", "window", "d", "period", "length", "lag", "timeperiod"):
            args.append(5)
        elif p.annotation is float or n in ("p", "alpha", "q"):
            args.append(0.5)
        else:
            args.append(pl.col("volume"))
    try:
        return isinstance(fn(*args), (pl.Expr, pl.Series))
    except Exception:
        return False

def classify(name: str, fn) -> dict | None:
    if not _smoke_ok(fn):
        return None
    if name in MANUAL_OVERRIDES:
        o = dict(MANUAL_OVERRIDES[name]); o.pop("reason", None); return o
    if name.startswith("ts_cum_"):
        return {"partition": "ts", "window": "unbounded", "mask_args": ()}
    if name.startswith("ts_"):
        return {"partition": "ts", "window": "arg:1" if _second_is_int(fn) else None, "mask_args": ()}
    if name.startswith("cs_"):
        return {"partition": "cs", "window": None, "mask_args": (0,)}
    if name.startswith("gp_"):
        return {"partition": "gp", "window": None, "mask_args": (1,)}
    if name.isupper() and _second_is_int(fn):
        return {"partition": "ts", "window": "arg:1", "mask_args": ()}
    return {"partition": "el", "window": None, "mask_args": ()}

HEADER = '''# 由 platform/scripts/gen_op_catalog.py 生成；勿手改（--check 校验）。
from factorlab.core.ops.classification import Catalog, OpMeta

ROWS = [
'''

def render(rows) -> str:
    body = "".join(
        f"    ({r['name']!r}, {r['partition']!r}, {r['window']!r}, {r['mask_args']!r}, "
        f"{r['source']!r}, {r['name']!r}),\n"
        for r in sorted(rows, key=lambda r: r["name"]))
    return HEADER + body + '''

def build_ta_catalog(catalog: Catalog) -> None:
    for name, part, win, mask, src, canon in ROWS:
        catalog.add(OpMeta(name, part, win, tuple(mask), src, canon), replace=True)
'''

def main(argv: list[str]) -> int:
    rows = []
    for modname in MODULES.values():
        mod = __import__(modname, fromlist=["*"])
        for name, fn in vars(mod).items():
            if name.startswith("_") or not (inspect.isfunction(fn) or inspect.isbuiltin(fn)):
                continue
            meta = classify(name, fn)
            if meta:
                rows.append({**meta, "name": name, "source": "polars_ta"})
    text = render(rows)
    if "--check" in argv:
        return 0 if OUT.read_text(encoding="utf-8") == text else 1
    OUT.write_text(text, encoding="utf-8")
    print(f"生成 {len(rows)} 条")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

`render` 生成形如：

```python
# 由 platform/scripts/gen_op_catalog.py 生成；勿手改。--check 校验。
from factorlab.core.ops.classification import Catalog, OpMeta

ROWS = [
    ("ts_quantile", "ts", "arg:1", (), "polars_ta", "ts_quantile"),
    ...
]

def build_ta_catalog(catalog: Catalog) -> None:
    for name, part, win, mask, src, canon in ROWS:
        catalog.add(OpMeta(name, part, win, mask, src, canon), replace=True)
```

- [ ] **Step 4: 生成产物并跑测试**

Run:
```bash
cd platform && .venv/bin/python scripts/gen_op_catalog.py && .venv/bin/python -m pytest tests/test_op_classification.py -q
```
Expected: PASS（若 `usable` 数量 < 350，回到分类规则修正后在 `MANUAL_OVERRIDES` 补充并写明理由——不留 TBD）

- [ ] **Step 5: 提交**

```bash
git add platform/scripts/gen_op_catalog.py platform/src/factorlab/core/ops/_generated_ta_ops.py platform/tests/test_op_classification.py
git commit -m "feat(platform): polars_ta 全量算子分类表（签名感知生成 + --check 门）"
```

---

### Task 3: polars 方法/访问器分类表

**Files:**
- Modify: `platform/scripts/gen_op_catalog.py`（追加 polars 部分）
- Create（生成产物）: `platform/src/factorlab/core/ops/_generated_polars_methods.py`
- Test: `platform/tests/test_op_classification.py`（追加）

**Interfaces:**
- Produces: `build_polars_catalog(catalog: Catalog) -> None`；方法条目 `source="polars_method"`。

**分类规则（Spike 2 结论）：**
- 窗口关键字（`rolling/prefetch?` 实际清单：`rolling_`, `shift`, `diff`, `ewm_`, `cum_`, `cum_sum/cum_max/cum_min/cum_count/cum_prod`）→ `ts`；`shift/diff` 的 `window="arg-equiv"` 采用 `"arg:0"`（periods 参数）+ 允许 kwargs（见 Task 5 处理）；
- `rank/quantile/median/mode` 等写进人工清单按"拒绝"（语义取决于上下文）；
- 默认 `el`；
- 35 项人工判定清单在生成器里落成 `POLARS_AMBIGUOUS_DENY`（拒绝 + 指引）与 `POLARS_MANUAL_TS`（明确窗口签名）；访问器（dt/str/list/arr/struct/cat/name/meta）默认 `el`，`.list.eval` 等特例进人工清单。
- **方法条目以 `.<method>` 为 name**（如 `.rolling_mean`、`.shift`），与函数命名空间隔离（避免 `.rank` 与函数 `rank` 冲突）；生成产物导出 `EL_METHODS/TS_METHODS/DENIED_METHODS` 三组（不含点，供版本锁测试），`build_polars_catalog` 注册时加 `.` 前缀。

- [ ] **Step 1: 写失败测试（版本锁）**

```python
def test_polars_methods_version_locked():
    import polars as pl
    from factorlab.core.ops._generated_polars_methods import EL_METHODS, TS_METHODS, DENIED_METHODS
    public = {m for m in dir(pl.Expr) if not m.startswith("_")}
    covered = EL_METHODS | TS_METHODS | DENIED_METHODS
    missing = public - covered
    assert not missing, f"polars 升级后有未分类方法，请补清单: {sorted(missing)}"

def test_ambiguous_denied_has_guidance():
    from factorlab.core.ops.classification import default_catalog
    c = default_catalog()
    assert c.get(".rank") is None           # 方法 .rank() 不作算子开放（指引 by=）
    assert c.get("ts_mean") is not None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd platform && .venv/bin/python -m pytest tests/test_op_classification.py::test_polars_methods_version_locked -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现生成 + 产物，Step 4: 跑测试**

Run:
```bash
cd platform && .venv/bin/python scripts/gen_op_catalog.py && .venv/bin/python -m pytest tests/test_op_classification.py -q
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add platform/scripts/gen_op_catalog.py platform/src/factorlab/core/ops/_generated_polars_methods.py platform/tests/test_op_classification.py
git commit -m "feat(platform): polars 方法/访问器分类表（版本锁测试）"
```

---

### Task 4: 统一语义推断 pass（NodeInfo）

**Files:**
- Create: `platform/src/factorlab/core/engine/semantics.py`
- Test: `platform/tests/test_semantics.py`

**Interfaces:**
- Consumes: `Catalog`（Task 1-3）
- Produces:
  - `NodeInfo(level: str, keys: tuple[str,...], order: str|None, lookback: int, forward: int, unbounded: bool)`
  - `infer(source: str, catalog: Catalog, params: dict | None = None) -> dict[int, NodeInfo]`（键为 `id(ast.Node)`；含 `errors: list[str]` 由调用方 raise，或直接抛 `SemanticError(line, col, msg)`）
  - `SemanticError(FactorDSLError)` 子类
- 推断规则（Spec §6.5）：
  - 字面量/列名 → `el, keys=(), lookback=0, forward=0`
  - `ts` 算子：`lookback = window + max(args.lookback)`；`forward = max(args.forward)`
  - `cs`：`keys=("date",)`；`gp`：`keys=("date", <key>)`；`el`：继承最外层非 el 子节点
  - 方法调用按分类表同上（**方法条目查 `.<attr>` 键**，如 `.rolling_mean`；未分类方法 → `SemanticError` 并指引改用函数形式或补 op_meta）
  - 未知调用（不在 catalog、非 def、非元素白名单）→ `SemanticError("未知算子 <name>；若是自定义函数请补 op_meta")`
  - `window` 无法求值（如 `${param}` 未给值、`arg:N` 非常量）→ `SemanticError("窗口参数必须是常量或 ${param}")`

- [ ] **Step 1: 写失败测试（全形态）**

```python
# platform/tests/test_semantics.py
import pytest
from factorlab.core.ops.classification import default_catalog
from factorlab.core.engine.semantics import infer, SemanticError

CAT = default_catalog()

def info(src):
    nodes = infer(src, CAT)
    # 取最外层目标赋值的值节点
    import ast
    tree = ast.parse(src)
    target = [n.value for n in ast.walk(tree) if isinstance(n, ast.Assign) and n.targets[0].id in ("signal", "_x")][-1]
    return nodes[id(target)]

def test_ts_window():
    assert info("signal = ts_mean(close, 20)").lookback == 20

def test_nested_ts_lookback_additive():
    assert info("signal = ts_delta(ts_mean(close, 20), 5)").lookback == 24

def test_cs_of_ts_level_cs():
    i = info("signal = cs_rank(ts_delta(close, 1))")
    assert i.level == "cs" and i.lookback == 1

def test_gp_keys():
    i = info("signal = gp_rank(ts_mean(close, 20), industry)")
    assert i.level == "gp" and i.lookback == 20

def test_method_window():
    i = info("signal = close.rolling_mean(5)")
    assert i.level == "ts" and i.lookback == 5

def test_unbounded():
    assert info("signal = ts_cum_sum(volume)").unbounded is True

def test_unknown_operator_message():
    with pytest.raises(SemanticError, match="op_meta"):
        infer("signal = my_magic(close)", CAT)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd platform && .venv/bin/python -m pytest tests/test_semantics.py -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现推断 pass**（自底向上 `ast.NodeVisitor`；`ts_cum_*`/宏展开后的 `vwap` 已在调用方展开——本 pass 只吃展开后的公式）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd platform && .venv/bin/python -m pytest tests/test_semantics.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add platform/src/factorlab/core/engine/semantics.py platform/tests/test_semantics.py
git commit -m "feat(platform): 统一语义推断 pass（分区/窗口/未来自底向上）"
```

---

### Task 5: 未来函数门统一（全形态）

**Files:**
- Modify: `platform/src/factorlab/core/engine/partitions.py`（`reject_future_shifts` 改为调用 `infer`：任何 `forward > 0` 拒绝；保留 `行:列` 文案）
- Test: `platform/tests/test_future_gate_v2.py`

**Interfaces:**
- Consumes: `infer` / `SemanticError`（Task 4）
- Produces: 统一入口 `check_causality(source, catalog, params=None) -> None`（partitions.py 内新函数，供 compute/lint 调用）

**必测形态（每条一个用例，全部须被拒）：**
`ts_delay(close, -1)`；`_n=3; ts_delay(close, -_n)`；`ts_delay(close, 1-3)`；`close[-1]`；`close[-_n]`；`close.shift(-1)`；`close.diff(-2)`；`close.rolling_mean(-5)`；`ts_rank(close, -20)`；`ts_delta(close, -_n)`。合法对照：`close[-0]`、`close[1]`、`close.shift(1)`、`ts_delay(close, _n)`（`_n=3` 正）。

- [ ] **Step 1: 写失败测试**（参数化 12 用例 + 4 对照）
- [ ] **Step 2: 跑测试确认失败**

Run: `cd platform && .venv/bin/python -m pytest tests/test_future_gate_v2.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**（`infer` 对 `Subscript`/`shift`/`diff`/窗口参数做常量折叠：复用 partitions.py 的 `_fold_consts`/`_top_level_consts`，传入 semantics；`forward>0` → raise）

- [ ] **Step 4: 跑测试 + R21 既有用例回归**

Run:
```bash
cd platform && .venv/bin/python -m pytest tests/test_future_gate_v2.py tests/test_partitions.py tests/test_m6_semantic_guards.py -q
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add platform/src/factorlab/core/engine/semantics.py platform/src/factorlab/core/engine/partitions.py platform/tests/test_future_gate_v2.py
git commit -m "feat(platform): 未来函数门统一到语义推断（全形态 + 行:列定位）"
```

---

### Task 6: 窗口/分块统一（lookback 驱动）

**Files:**
- Modify: `platform/src/factorlab/core/engine/compute.py`（`_ts_window_days` → `infer(...).lookback`；`reject_cumulative_chunking` 改用 `unbounded` 标志）
- Test: `platform/tests/test_chunk_warmup_v2.py`

**Interfaces:**
- Consumes: `infer`（Task 4）
- Produces: `required_lookback(formula: str, pool: str | None, catalog) -> int`；`unbounded_ops(formula, pool, catalog) -> list[str]`

- [ ] **Step 1: 写失败测试**

```python
def test_lookback_via_inference():
    from factorlab.core.ops.classification import default_catalog
    from factorlab.core.engine.compute import required_lookback
    cat = default_catalog()
    assert required_lookback("signal = ts_delta(ts_mean(close, 20), 5)", None, cat) >= 24
    assert required_lookback("signal = ts_quantile(volume, 120, 0.9)", None, cat) == 120

def test_unbounded_blocks_chunking():
    from factorlab.core.ops.classification import default_catalog
    from factorlab.core.engine.compute import unbounded_ops
    cat = default_catalog()
    assert "ts_cum_sum" in unbounded_ops("signal = ts_cum_sum(volume)", None, cat)
```

- [ ] **Step 2: 跑测试确认失败**；**Step 3: 实现**；**Step 4: 跑测试 + 既有分块用例**

Run: `cd platform && .venv/bin/python -m pytest tests/test_chunk_warmup_v2.py tests/test_chunk_label_exactness.py tests/test_qfq_chunk_invariance.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git commit -m "feat(platform): 窗口/分块统一由语义推断驱动（unbounded 互斥）"
```

---

### Task 7: 注册闸门拆除 + 分区绑定规范化（核心交付）

**Files:**
- Modify: `platform/src/factorlab/core/engine/compute.py`（新增规范化改名 pass：分类表驱动的 canonical 名 + import 注入；`validate_partition_calls` 调用点改为"catalog 解析"）
- Modify: `platform/src/factorlab/core/ops/registration.py`（注册来源加分类表全集；`source_import_lines` 输出生成导入）
- Test: `platform/tests/test_open_ops_e2e.py`

**Interfaces:**
- Produces: `normalize_calls(source: str, catalog) -> tuple[str, str]`（新源码 + 额外 import 行）；未知函数 → `SemanticError`（含 op_meta 示例文案）
- 行为：`ts_quantile`、`BBANDS`、`ts_zscore` 等直接写入公式可通过 mock 面板计算；`factorlab lint` 不再报"未知算子"

- [ ] **Step 1: 写失败测试（mock LazyFrame 值级）**

```python
import polars as pl
from factorlab.core.engine.compute import compute_formula

def _panel():
    return pl.DataFrame({
        "date": ["2024-01-01"]*4 + ["2024-01-02"]*4 + ["2024-01-03"]*4,
        "code": ["a","b","c","d"]*3,
        "volume": [1.0,2,3,4, 2,3,4,5, 3,4,5,6],
    })

def test_ts_quantile_computes():
    out = compute_formula(_panel(), "signal = ts_quantile(volume, 2, 0.9)", outputs=["signal"])
    assert out["signal"].null_count() < out.height   # 至少部分有值（非硬编码）

def test_unknown_op_guides_op_meta():
    import pytest
    from factorlab.core.factor.errors import FactorDSLError
    with pytest.raises(FactorDSLError, match="op_meta"):
        compute_formula(_panel(), "signal = totally_new(volume)", outputs=["signal"])
```

- [ ] **Step 2: 跑测试确认失败**（现在报"未知算子: ts_quantile"）

Run: `cd platform && .venv/bin/python -m pytest tests/test_open_ops_e2e.py -q`
Expected: FAIL

- [ ] **Step 3: 实现规范化 + catalog 解析 + 导入注入**

要点：
- 遍历调用（含方法），查 `catalog.get(name)`；
- 命中且 `canonical != name` → 改写 `ast.Call.func` 名字；记录 `from <module> import <attr> as <canonical>`；
- `ts_/cs_/gp_` 前缀名保持原样（expr_codegen 前缀分类器继续工作）；
- `mask_args` 替换 `universe_masking._CS_GP_MASK_ARGS` 静态表（按 meta 查）；
- 未命中且不是 def/元素白名单 → 报错文案附 `op_meta` 示例。

- [ ] **Step 4: 跑测试 + 存量 lint 全量**

Run:
```bash
cd platform && .venv/bin/python -m pytest tests/test_open_ops_e2e.py tests/test_run_factor.py tests/test_platform_ops.py -q
make lint-factors        # 152/152
```
Expected: PASS / 152/152

- [ ] **Step 5: 提交**

```bash
git commit -m "feat(platform): 拆除算子白名单闸门（分类表解析 + 规范化改名 + import 注入）"
```

---

### Task 8: lint 接入静态管线 + 文档门

**Files:**
- Modify: `platform/src/factorlab/surfaces/cli/main.py`（`lint` 调 `prepare_static`：参数替换 → 宏展开 → def 内联 → infer → causality → 输出名检查；不触 DB）
- Test: `platform/tests/test_cli_lint_v2.py`

- [ ] **Step 1: 写失败测试**

```python
from typer.testing import CliRunner
from factorlab.surfaces.cli.main import app

def test_lint_rejects_negative_shift(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("""
name: t_lint_bad
category: custom
direction: 1
universe: {rules: {exclude_st: true, exchanges: ["SSE"]}}
date: {start: "2024-01-01", end: "2024-02-01"}
process: [standardize()]
formula: |
  signal = ts_delay(close, -1)
""", encoding="utf-8")
    r = CliRunner().invoke(app, ["lint", str(p)])
    assert r.exit_code != 0 and "负位移" in r.output

def test_lint_accepts_ts_quantile(tmp_path):
    p = tmp_path / "ok.yaml"
    p.write_text("""
name: t_lint_ok
category: custom
direction: 1
universe: {rules: {exclude_st: true, exchanges: ["SSE"]}}
date: {start: "2024-01-01", end: "2024-02-01"}
process: [standardize()]
formula: |
  signal = ts_quantile(volume, 120, 0.9)
""", encoding="utf-8")
    r = CliRunner().invoke(app, ["lint", str(p)])
    assert r.exit_code == 0, r.output
```

- [ ] **Step 2-4: 红→绿循环**

Run: `cd platform && .venv/bin/python -m pytest tests/test_cli_lint_v2.py -q`
Expected: 先 FAIL 后 PASS

- [ ] **Step 5: 提交**

```bash
git commit -m "feat(platform): lint 接入完整静态管线（语义推断 + 未来门）"
```

---

### Task 9: 存量 152 因子零迁移回归（integration）

**Files:**
- Test: `platform/tests/test_regression_152.py`（`@pytest.mark.integration`，CH 可用时跑）
- Modify: 如有非预期行为差异 → 修复而不是改断言（记录在案）

- [ ] **Step 1: 写回归测试**

```python
import json, os, subprocess
from pathlib import Path
import pytest

pytestmark = pytest.mark.integration

SPECS = {
    "reversal_20d": "reversal_20d/reversal_20d.yaml",
    "momentum_20d": "momentum_20d/momentum_20d.yaml",
    "vol_run_energy_symrun": "vol_run_energy/symrun.yaml",
    "crash_bottom_leader": "crash_bottom_leader/crash_bottom_leader.yaml",
    "value_bp": "value/bp.yaml",
    "low_vol_20d": "volatility/low_vol_20d.yaml",
}
IC_KEYS = ("mean", "t_stat", "ir")

def test_specs_lint_all():
    root = Path(__file__).resolve().parents[2] / "research/factor"
    specs = sorted(root.glob("*/*.yaml"))
    assert len(specs) == 152
    # 全量 lint 由 `make lint-factors` 承担；此处锁数量与目录结构

def test_sample_value_regression():
    """改动后重跑 6 个代表 spec，summary IC 与基准档一致（|Δ| ≤ 1e-9）。"""
    repo = Path(__file__).resolve().parents[2]
    baseline_dir = repo / "docs/verification/R22/00-baseline"
    env = {**os.environ, "FACTORLAB_DATA_BACKEND": "ch"}
    for name, rel in SPECS.items():
        r = subprocess.run(
            [str(repo / "platform/.venv/bin/factorlab"), "run", str(repo / "research/factor" / rel)],
            cwd=str(repo), env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        got = json.loads((repo / f"platform/results/{name}/summary.json").read_text())
        base = json.loads((baseline_dir / f"{name}.json").read_text())
        for k in IC_KEYS:
            a, b = got["evaluation"]["ic"][k], base["evaluation"]["ic"][k]
            assert abs(a - b) <= 1e-9, f"{name}.ic.{k}: {a} != {b}"
```

- [ ] **Step 2: 生成基准（改动前跑一次，存 docs/verification/R22/ 前置档）**

Run:
```bash
FACTORLAB_DATA_BACKEND=ch platform/.venv/bin/factorlab run research/factor/reversal_20d/reversal_20d.yaml
```
（6 个 spec 的 summary.json 存 `docs/verification/R22/00-baseline/`）

- [ ] **Step 3: 改动后重跑并对比**

Run: `cd platform && .venv/bin/python -m pytest tests/test_regression_152.py -q`
Expected: PASS（差异 0 或按容差；有差异 → 修代码）

- [ ] **Step 4: 全门**

Run:
```bash
make gates && cd platform && .venv/bin/python -m pytest -q
```
Expected: gates 全绿；pytest ≥ 2570 passed / 13 skipped

- [ ] **Step 5: 证据与提交**

```bash
# docs/verification/R22/：命令 + 原始输出 + 门结果（AGENTS.md 纪律）
git add docs/verification/R22 platform/
git commit -m "test(platform): R22 开放算子底座回归（152 lint + 抽样值级 + 全门）"
```

---

## Self-Review（本计划对 spec 的覆盖）

| Spec 要求 | 对应 Task |
|---|---|
| §4 开放面：库全量可用、未知不再拒（改指引） | Task 2/3/7 |
| §5.2 语义三来源（组合继承/自动推断/op_meta 报错指引） | Task 4/7（op_meta 字段本体在 Plan 2） |
| §7.1 未来门全形态 | Task 5 |
| §7.2 窗口/分块/准确性 | Task 6 |
| §7.2 lint 静态 | Task 8 |
| §8 零迁移 | Task 7/9 |
| §9 G1-G4 | Task 2/3/4/5/6/7 |
| §10 验收 1/2/5/6/7 | Task 7/8/9 |
| §5 算子生命周期（档案/conformance/命名算子） | **Plan 2**（依赖本计划推断层） |
| §6 截面（by= + 原语 + 数据可用性） | **Plan 3**（依赖本计划分类层） |

## Execution Handoff

Plan 1 完成后：写 Plan 2（算子生命周期：命名算子文件、conformance 套件、算子档案、插件元数据）与 Plan 3（截面表达：`by=` 语法、agg/rank/clip/cut/dist/proj/mask、数据可用性检查）。
