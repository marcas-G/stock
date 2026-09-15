from __future__ import annotations

import ast
import datetime
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl
import yaml
from expr_codegen import codegen_exec

from factorlab.core.domain.frames import LabelArtifact, SignalArtifact, SignalMeta
from factorlab.core.domain.timing import DEFAULT_EOD_SIGNAL_TIMING
from factorlab.core.engine.forward import DEFAULT_FORWARD_HORIZONS, compute_forward_returns
from factorlab.core.engine.partitions import reject_future_shifts, validate_partition_calls
from factorlab.core.factor.ast_gate import validate_formula
from factorlab.core.ops.platform_ops import (
    expand_platform_macros,
    expand_user_macros,
    inline_defs,
    register_platform_ops,
    rewrite_expr_methods,
)
from factorlab.core.ops.minute_ops import EXTRA_CODES, register_minute_ops
from factorlab.core.ops.polars_ta_wrappers import (register_polars_ta_ops,
                                                   rewrite_polars_ta_aliases)
from factorlab.core.ops.universe_masking import (apply_universe_masking,
                                            validate_reserved_bindings)
from factorlab.core.spec import FactorSpec

# 名字类墙常量单点定义于 engine/reserved.py（M1 收拢，禁止散落字面量）：
# FUTURE_PREFIXES/FUTURE_NAMES——future/label 字段显式引用 → fail fast
# INTERNAL_PREFIX/INTERNAL_NAMES——引擎内部列（__factorlab_* / in_universe）读/绑定 → fail fast
from factorlab.core.engine.reserved import (
    FUTURE_NAMES,
    FUTURE_PREFIXES,
    validate_internal_reads,
)


_PARAM_PATTERN = re.compile(r"\$\{(\w+)\}")


def substitute_params(formula: str, params: dict[str, Any]) -> str:
    """顶层参数替换：formula 内 ${name} 文本引用 → 字面量（str(params[name])）。

    文本替换（非 AST）：宏体/def 体内的 ${} 同样可见——宏体由调用方对 operators
    副本替换，def 体在 formula 文本内一并命中。未知参数名 → ValueError。
    """
    def _repl(match: re.Match) -> str:
        name = match.group(1)
        if name not in params:
            raise ValueError(f"未知参数: {name}（spec.params 未声明）")
        return str(params[name])

    return _PARAM_PATTERN.sub(_repl, formula)


def _check_future_inputs(formula: str) -> None:
    """M6-03：factor formula 显式引用 forward_*/future_*/target/label → fail fast。

    常量来自 engine/reserved.py（M1 收拢，与数据侧命名纪律同源）。"""
    for col in _formula_columns(formula):
        if col in FUTURE_NAMES or col.startswith(FUTURE_PREFIXES):
            raise ValueError(
                f"future/label inputs are forbidden in factor formula: {col!r}")


def _declared_output_names(formula: str) -> set[str]:
    """公式文本顶层赋值目标（M2：outputs 声明须由公式实际产生——codegen 前 fail fast）。

    变换链（inline_defs/宏展开）完成后的文本：用户 def/宏公式的赋值都已内联为
    顶层语句，顶层 Name 赋值目标 = 会出现在 codegen 结果里的列名全集。
    """
    tree = ast.parse(formula)
    declared = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    declared.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            declared.add(node.target.id)
    return declared


def compute_formula(
    df: pl.DataFrame,
    formula: str,
    asset: str = "code",
    date: str = "date",
    universe_mask: str | None = None,
    outputs: list[str] | None = None,
    *,
    scope: str = "daily",
) -> pl.DataFrame:
    """outputs（M2）：None → ["signal"]（旧调用方完全兼容）。声明输出须在公式顶层
    赋值中产生（变换后核对，codegen 前 fail fast）并按 outputs 顺序保留。

    scope（2026-09-08）："daily"（缺省——日频语义逐字节不变，且公式含
    im_*/day_* 分钟算子 → 明确 ValueError）| "bars_1m"（分钟面：公式面 = 日内窗
    im_* + 折日 day_*，输出必须折日常数；im_*/day_* 名经 extra_codes 注入 codegen
    作用域）。"""
    outputs = list(dict.fromkeys(outputs or ["signal"]))
    if scope == "bars_1m" and universe_mask is not None:
        raise ValueError(
            "minute scope（bars_1m）不接受 universe_mask（CS 截面掩码是日频机制"
            "——分钟池成员在装配期按 (code, date) 过滤，见 docs/interface.md 分钟面）")
    validate_formula(formula)
    # M1：内部保留名（__factorlab_* / in_universe）绑定与读取两门，无条件生效
    # （与 universe_mask 无关）——先绑定后读取，覆盖公式一切位置；必须在
    # apply_universe_masking 插入内部引用之前执行。绑定门此前只在 masked 路径
    # 生效，现提前到此（universe_mask=None 直调同样封）。
    validate_reserved_bindings(formula)
    validate_internal_reads(formula)
    formula = inline_defs(formula)  # def 内联（幂等：无 def 原样返回）——窗口算子合法化为顶层 ts_ 调用
    formula = rewrite_expr_methods(formula)  # 元素级方法链 → 函数调用（expr_codegen 不支持属性调用）
    formula = expand_platform_macros(formula)  # 薄封装 → ts_ 表达式，保证按 asset 分区
    from factorlab.core.ops.stable_rank import rewrite_stable_rank
    formula = rewrite_stable_rank(formula)  # M6-07C2I：cs_rank → 平台 stable 实现（+ import）
    formula = rewrite_polars_ta_aliases(formula)  # R01-ENG-I4：cs_regression_resid → vendor cs_resid
    if universe_mask is not None:
        # M6-03：CS/GP 算子的数据参数包 if_else(mask, arg, None)——TS 仍见完整
        # listed history，CS 只见当日 active universe。mask 列必须已存在于 df。
        if universe_mask not in df.columns:
            raise ValueError(f"universe mask 列 {universe_mask!r} 不在输入数据中（内部保留列）")
        formula = apply_universe_masking(formula, universe_mask)
    # DER-003：注册单点 = core.ops.registration.ensure_all_ops_registered（幂等）；
    # 此处防御性调用，保证核心被直接调用（测试/工具）时不依赖装配顺序。
    from factorlab.core.ops.registration import ensure_all_ops_registered
    ensure_all_ops_registered()
    _check_future_inputs(formula)
    # scope 门（变换后文本——宏残余/内联 def 已就位）：bars_1m 静态门 vs daily 拒分钟算子
    from factorlab.core.engine.minute_gate import (
        reject_minute_ops_in_daily,
        validate_minute_scope,
    )
    if scope == "bars_1m":
        validate_minute_scope(formula, outputs)
    else:
        reject_minute_ops_in_daily(formula)
    validate_partition_calls(formula)
    reject_future_shifts(formula)
    # M2：声明的输出必须在公式顶层赋值中产生（变换完成后再核对）——codegen 前
    # fail fast，点名缺哪个；避免未定义名退化成 NameError 深层报错
    missing_declared = [o for o in outputs if o not in _declared_output_names(formula)]
    if missing_declared:
        raise ValueError(
            f"因子脚本未产出声明输出列: {missing_declared}（outputs 声明与实际定义不符）")
    # M3（G6）：组算子翻译产物符号注入 codegen 作用域——gp_ 前缀函数
    # （gp_mean/gp_rank，已注册 registry/分区校验/masking）经 expr_codegen
    # printer 翻译为 cs_<名>(<去 key>) + .over(_DATE_, '<key>')：key 只作
    # 分区列、按日×组分区。生成代码 exec 需解析翻译产物 cs_mean/cs_rank
    # （platform_ops 模块符号，不注册——公式层直写会被 partition 门拒），
    # 否则 NameError。extra_codes 无条件追加 import 头（未使用 import 无
    # 副作用；codegen_exec 的 extra_codes 是单字符串——整体直接复制进生成
    # 代码头部，非序列）。模块别名 import 形式在 codegen 作用域不可用
    # （实测），必须直接 import 名。bars_1m scope：追加 minute_ops 名（单
    # 字符串多行——minute 红测试实测通过；与注册表名单同源防漂移）。
    extra_codes = ("from factorlab.core.ops.platform_ops import cs_mean, cs_rank\n"
                   + EXTRA_CODES) if scope == "bars_1m" \
        else "from factorlab.core.ops.platform_ops import cs_mean, cs_rank"
    # 第三方插件算子作用域（2026-09-14）：生成代码以**裸名字**调用算子，名字必须在
    # 其 exec 作用域内绑定；插件算子没有公式层 import 可用，故由加载器登记来源模块
    # （adapters.plugins.mark_source_module），此处注入 import 头。无插件登记 →
    # 字符串不追加，生成代码逐字节不变（位级门前提）。
    from factorlab.core.ops import registry as _ops_registry
    _plugin_imports = _ops_registry.source_import_lines()
    if _plugin_imports:
        extra_codes = extra_codes + "\n" + "\n".join(_plugin_imports)
    result = codegen_exec(
        df.lazy(),
        formula,
        over_null="partition_by",
        style="polars",
        date=date,
        asset=asset,
        extra_codes=extra_codes,
    ).collect()
    # 兜底：codegen 结果缺失声明输出（变换语义偏差）也点名报错
    missing = [o for o in outputs if o not in result.columns]
    if missing:
        raise ValueError(
            f"因子脚本未产出声明输出列: {missing}（outputs 声明与实际定义不符）")
    return result.select([date, asset, *outputs]).sort([date, asset])


# ---- M3a run_factor 装配 ----

# 元素级函数名（按名称调用时非数据列；Call 形式已由 called 集合排除，此处兜底）
_ELEMENTWISE_COLS = {"abs", "log", "log1p", "sqrt", "exp", "sign", "floor", "if_else"}


def _formula_columns(formula: str) -> list[str]:
    """提取公式实际引用的数据列（排除算子名/函数参数/import 名/中间变量）。"""
    tree = ast.parse(formula)
    # ast.arg 在 Python 3.13 的属性是 .arg（.name 为 3.14+ 别名）
    defined = {n.name if isinstance(n, ast.FunctionDef) else n.arg
               for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.arg))}
    # 赋值中间变量（非下划线）也是 defined：Assign 的 targets 是列表、AnnAssign 的 target 是单个。
    # 注意：Python 3.13 内联 comprehension 的 if 条件里用三元表达式会误报 NameError，故拆成两个集合
    defined |= {n.targets[0].id for n in ast.walk(tree)
                if isinstance(n, ast.Assign) and n.targets and isinstance(n.targets[0], ast.Name)}
    defined |= {n.target.id for n in ast.walk(tree)
                if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
    # AnnAssign 注解名（如 ret: float = ... 的 float）也不是数据列
    annotated = {sub.id for n in ast.walk(tree) if isinstance(n, ast.AnnAssign)
                 for sub in ast.walk(n.annotation) if isinstance(sub, ast.Name)}
    imported = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    cols = names - defined - imported - called - annotated - _ELEMENTWISE_COLS - {"signal"}
    return sorted(c for c in cols if not c.startswith("_") and c not in {"date", "code"})


# R01-ENG-I1：依赖"块内全历史"的累计算子族（vendor ts_cum_* 无窗口参数，
# 定义即 cum_sum/cum_max/... 全历史累计）。--chunk-days 每块独立跑完整流水线
# → 每块重新累计，违反 docs/interface.md「分块计算」的"与单块整段跑逐 cell
# 一致"承诺（实测 ts_cum_sum/vwap 分块差异 14 行）。
_CUMULATIVE_PREFIX = "ts_cum_"


def cumulative_ops_used(source: str) -> list[str]:
    """公式引用的累计算子名（ts_cum_* 族；import 别名解析，未知名不报错）。"""
    tree = ast.parse(source)
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                aliases[alias.asname or alias.name] = alias.name
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = aliases.get(node.func.id, node.func.id)
            if name.startswith(_CUMULATIVE_PREFIX):
                used.add(name)
    return sorted(used)


def reject_cumulative_chunking(formula: str, pool: str | None = None) -> None:
    """分块 × 累计算子 fail fast（R01-ENG-I1）。

    调用方保证 formula/pool 已过 `prepare_formula_pipeline`（宏展开后文本——
    vwap 已展开为 ts_cum_sum）。任一累计算子出现即拒绝：分块下每块重置，
    结果与整段跑不再逐 cell 一致；单块整段跑（chunk_days=None）语义正确，
    文案指引用户去掉 --chunk-days 或改用窗口算子。
    """
    names = set(cumulative_ops_used(formula))
    if pool is not None:
        names.update(cumulative_ops_used(pool))
    if names:
        raise ValueError(
            f"累计算子 {sorted(names)} 与 --chunk-days 分块不兼容：累计算子依赖"
            f"块内全历史，分块时每块重新累计，结果不再与整段跑逐 cell 一致"
            f"（docs/interface.md §分块计算）。请去掉 --chunk-days 单块整段跑，"
            f"或改用非累计算子（如 ts_sum/ts_mean 窗口算子）")


_WINDOW_PREFIXES = ("ts_", "ta_")  # 窗口参数在第二位置的算子族（tdx_* 参数语义不同，不提取）


def _ts_window_days(formula: str) -> int:
    """AST 提取公式窗口需求：ts_*/ta_* 窗口算子的窗口参数，**沿变量引用链叠加**。

    嵌套滚动（如 robust_z 的 MAD = ts_median((x - ts_median(y, N)).abs(), N)）时，
    med 的 N 窗被外层 N 窗消费 → 总需求 = 2N。chunked warmup 只覆盖单层 N 时，
    每块嵌套滚动全 null（研究轮 204 根因）。窗口参数非常量时忽略该项；无窗口算子 → 0。
    """
    tree = ast.parse(formula)
    assigns: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            assigns[node.targets[0].id] = node.value

    def _call_window(node: ast.Call) -> int:
        name = None
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) \
                and node.func.value.id in ("wq", "ta"):
            name = node.func.attr
        if not name or not name.startswith(_WINDOW_PREFIXES):
            return 0
        arg = node.args[1] if len(node.args) >= 2 else None
        if isinstance(arg, ast.Constant) and isinstance(arg.value, int) and not isinstance(arg.value, bool):
            return arg.value
        return 0

    def _need(node: ast.AST) -> int:
        # DSL 公式无循环引用（变量先赋值后使用）——不做 visited 防呆，同一变量
        # 在表达式不同位置分别展开（否则 seen 消耗后嵌套链的窗口叠加丢失）
        if isinstance(node, ast.Call):
            sub = max((_need(a) for a in node.args), default=0)
            if isinstance(node.func, ast.Attribute):
                sub = max(sub, _need(node.func.value))  # 方法链：.abs() 的 BinOp 在 func.value
            return _call_window(node) + sub
        if isinstance(node, ast.Name) and node.id in assigns:
            return _need(assigns[node.id])
        return max((_need(c) for c in ast.iter_child_nodes(node)), default=0)

    needs = [_need(n) for n in ast.walk(tree) if isinstance(n, ast.Call)]
    needs += [_need(expr) for expr in assigns.values()]
    return max(needs) if needs else 0


def fill_suspension_values(panel: pl.DataFrame) -> pl.DataFrame:
    """停牌补全行数值列前值填充（fill 前已算 forward_return，评估样本不变）。

    polars 滚动算子默认 min_samples=window 且 null 计入有效值要求——停牌补全行的
    1 个 null 使其后 d 天窗口统计全 null（传染），长窗因子（如 360 日）因此失效；
    嵌套滚动（MAD 等）进一步放大传染至全 null（研究轮 111/204 根因）。
    前值填充 = 停牌期间价格/成交视为不变（金融惯例），窗口统计不再含 null。
    """
    fill_cols = [c for c in panel.columns
                 if c not in {"date", "code"} and not c.startswith("forward_return")]
    return panel.with_columns(
        [pl.col(c).fill_null(strategy="forward").over("code") for c in fill_cols]
    )


@dataclass
class FactorResult:
    """M6-03：signal/label runtime 分离产物 + legacy panel 兼容视图。

    M2（G1）：单输出（缺省）沿用 signal_artifact；多输出时 signal_artifact 保持
    None、逐输出 frame（date/code/<output>）在 `signals[output]`，落盘见
    artifacts.write_multi_output_factor_artifacts。
    """

    spec: FactorSpec
    signal_artifact: SignalArtifact | None
    label_artifact: LabelArtifact
    panel: pl.DataFrame
    summary: dict = field(default_factory=dict)
    signals: dict[str, pl.DataFrame] = field(default_factory=dict)


_WARMUP_SAFETY_PAD = 20  # 自动 warmup 的安全垫：覆盖 ts_delay 等窗口内偏移
# spec 2.5 对齐输出列（分块路径每块算完即裁剪到这些列再累积，避免全列面板堆叠 OOM）。
# M2：signal 字面 → outputs 声明列（全保留）+ 对齐尾列
_LEGACY_KEEP_TAIL = ["forward_return_5d", "forward_return_20d", "close"]


def _chunk_keep(outputs: list[str]) -> list[str]:
    return ["date", "code", *outputs, *_LEGACY_KEEP_TAIL]


def _canonicalize_artifact_codes(
    frame: pl.DataFrame,
    code_map: pl.DataFrame,
) -> pl.DataFrame:
    """Artifact boundary canonicalization（M7-05）：frame.code = symbol → canonical ts_code。

    - 只改 code 列（date/signal/close/forward_* 数值与 null mask 严格保持）
    - 行数精确不变（N 输入 → N 输出；缺失映射 fail fast，不丢行不膨胀）
    - 映射碰撞（两 symbol → 同一 ts_code 致 (date, code) 重复）→ fail fast
    - 输出按 (date, code) canonical 稳定排序
    code_map schema：(symbol, code=canonical ts_code)——来自
    resolve_canonical_code_map（stock_basic reference data，禁止启发式）。
    """
    n = frame.height
    joined = frame.join(code_map.rename({"code": "_canonical"}),
                        left_on="code", right_on="symbol", how="left")
    missing = joined.filter(pl.col("_canonical").is_null())
    if missing.height:
        raise ValueError(
            f"artifact canonicalization 缺失 {missing.height} 行 symbol 映射"
            f"（fail fast，不丢行）: {missing['code'].unique().to_list()}")
    out = (joined.with_columns(pl.col("_canonical").alias("code"))
           .drop("_canonical"))
    if out.height != n:
        raise ValueError(
            f"canonicalization 行数变化 {n} -> {out.height}（join 放大——BLOCKED）")
    dup = out.group_by(["date", "code"]).len().filter(pl.col("len") > 1)
    if dup.height:
        raise ValueError(
            f"canonicalization 后 (date, code) 重复 {dup.height} 组"
            f"（symbol→ts_code 映射碰撞——不 dedup）")
    return out.sort(["date", "code"])


def _build_legacy_panel(
    signal_df: pl.DataFrame,
    labels_df: pl.DataFrame,
    signal_artifact: SignalArtifact | None,
    label_artifact: LabelArtifact,
    outputs: list[str],
) -> pl.DataFrame:
    """Legacy panel 兼容视图（M6-07C2B）：**不做 key join**。

    Signal/Label 的 (date, code) 键对齐（行数/键/顺序）由正式
    validate_signal_label_alignment() 证明后，仅位置化附加 labels 值列
    （forward_return_5d/20d）——避免 1,155 万行 × 2 侧的 hash join 峰值
    分配在无页面文件机器上撞 commit 空间（C2A 定位的 0xC0000005）。

    M2（G1）：多输出下 signal_artifact=None——不构造虚拟单列 artifact，改为
    signal_df/labels_df 键 equals 直验（两帧经同一 canonicalize 排序，等价性
    同单输出对齐契约）。输出列 = _chunk_keep(outputs)（含全声明输出 + 尾列）。

    职责窄：alignment validation + positional attach + legacy schema select；
    不含 persistence（write_factor_artifacts 是独立的 persistence boundary
    guard，重复 alignment 验证属正常）。禁止任何 join——本步的数学关系
    已由 alignment contract 证明。
    """
    from factorlab.core.engine.alignment import validate_signal_label_alignment
    if signal_artifact is not None:
        validate_signal_label_alignment(signal_artifact, label_artifact)
    elif not signal_df.select(["date", "code"]).equals(labels_df.select(["date", "code"])):
        raise ValueError(
            "Signal/Label (date, code) key 不一致（含顺序）——多输出 panel 构造拒绝")
    label_values = labels_df.select(["forward_return_5d", "forward_return_20d"])
    panel = signal_df.hstack(label_values)
    return panel.select([c for c in _chunk_keep(outputs) if c in panel.columns])






def _normalize_pool_formula(text: str) -> str:
    """池公式 v1 文法门（M4/G2）：单语句 → 归一为 `signal = <expr>`。

    v1 池语法只接受**单个布尔表达式**：裸表达式（`close > 15`）或单条赋值
    （`signal = close > 15`）——赋值名不参与语义，统一归一为 signal（宏展开后
    的文本同样过此门）。def/多语句/多目标赋值/注解赋值/空文本 → ValueError
    （文案含"池公式"，指引 v1 文法）。
    """
    try:
        tree = ast.parse(text.strip())
    except SyntaxError as exc:
        raise ValueError(
            f"池公式语法错误: {exc.msg}（v1 池公式 = 单个布尔表达式，见 "
            f"docs/interface.md §公式化股票池）") from exc
    body = tree.body
    if len(body) != 1:
        raise ValueError(
            f"池公式 v1 语法只接受单个布尔表达式（裸表达式或单条赋值），"
            f"收到 {len(body)} 条语句——def/多语句不在 v1 池文法，"
            f"请把成员条件写成一条表达式（或 def 内联后仍是单条）")
    stmt = body[0]
    if isinstance(stmt, ast.Expr):
        expr = stmt.value
    elif isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
            and isinstance(stmt.targets[0], ast.Name):
        expr = stmt.value
    else:
        raise ValueError(
            f"池公式 v1 语法只接受单条 `目标 = <布尔表达式>` 或裸布尔表达式"
            f"（收到 {type(stmt).__name__}——def/多目标/注解不在 v1 文法）")
    return f"signal = {ast.unparse(expr)}"


def _require_boolean_pool(text: str) -> None:
    """池公式布尔可判定门（静态，run_factor 打开 DB 前）：归一文本的表达式中
    无任何比较/布尔运算 → fail fast（不落库求值）。动态 dtype 门（结果列 Bool）
    在求值后兜底——含比较但返回数值的表达式（如 if_else 数值分支）静态放行、
    dtype 门拦截。
    """
    tree = ast.parse(text)
    stmt = tree.body[0]                       # _normalize_pool_formula 已归一
    assert isinstance(stmt, ast.Assign)
    decidable = any(
        isinstance(n, (ast.Compare, ast.BoolOp)) for n in ast.walk(stmt.value))
    if not decidable:
        raise ValueError(
            "池公式必须布尔可判定（表达式含比较 `>`/`<`/`==`/`!=` 或布尔运算，"
            "逐 (code, 交易日) 得真/假）——纯数值/窗口表达式不是成员条件；"
            "v1 文法指引见 docs/interface.md §公式化股票池")


def _pool_cond_frame(panel: pl.DataFrame, pool: str) -> pl.DataFrame:
    """池公式布尔条件求值（M4/G2，signal/label runtime 共用）。

    - compute_formula 全骨架 **unmasked**（universe_mask=None）：CS/GP 算子
      不受任何 __factorlab_* mask——池公式看到完整 listed 当日横截面/全骨架
      属性组（设计 §4.3/4.4：成员资格计算必须先于成员过滤）
    - 动态 dtype 门：结果列必须 Bool（含比较但返回数值的 if_else 数值分支 →
      此处 fail fast，文案含 "Bool"）
    - 返回 (date, code, signal) Bool 列 frame——signal 为归一输出名
    """
    cond = compute_formula(panel, pool)       # outputs 缺省 [signal]；归一文本必产 signal
    if cond["signal"].dtype != pl.Boolean:
        raise ValueError(
            f"池公式求值结果列 dtype 不是 Bool（实际 {cond['signal'].dtype}）——"
            f"池成员必须是逐 (code, 交易日) 真/假的布尔条件；if_else 等数值"
            f"分支表达式不可作 v1 池公式")
    return cond




def label_lookahead_end(cal: pl.Series, chunk_end: datetime.date, horizon: int) -> datetime.date:
    """M6-04：chunk_end 向后 horizon 个交易日的 label_end（right lookahead）。

    截断到研究 calendar 最后一天（**label lookahead 可跨内部 chunk boundary，
    不可跨研究 sample boundary**）。chunk_end 不在 calendar / horizon<0 /
    calendar 为空 → fail fast。
    """
    if horizon < 0:
        raise ValueError(f"horizon 不能为负: {horizon}")
    if cal.len() == 0:
        raise ValueError("calendar 为空——无法计算 label lookahead")
    idx = int(cal.search_sorted(chunk_end))
    if idx >= cal.len() or cal[idx] != chunk_end:
        raise ValueError(f"chunk_end {chunk_end} 不在研究 calendar 中")
    return cal[min(idx + horizon, cal.len() - 1)]




def prepare_formula_pipeline(spec: FactorSpec) -> tuple[str, str | None]:
    """run_factor/run_factor_minute 共用的公式展开链（**打开数据库前全部完成**，
    语法/参数错误先暴露）。

    顺序锁定：spec.params 顶层参数先替换（宏体经 operators 副本、def 体在
    formula 文本内一并命中）→ spec.operators 内联宏展开（用户宏公式可引用平台
    薄封装与 ${}）→ 校验 → def 内联（窗口算子合法化为顶层 ts_ 调用）→ 平台
    薄封装展开。池公式（spec.universe.formula）同链展开并归一/布尔可判定门
    （v1 文法：单布尔表达式，赋值名归一 signal；保留名绑定门在归一**前**跑，
    读取/未来引用/布尔可判定门在归一后跑）。返回 (formula, pool)。
    """
    formula = substitute_params(spec.formula or "", spec.params)
    operators = {
        name: op.model_copy(update={"formula": substitute_params(op.formula, spec.params)})
        for name, op in spec.operators.items()
    }
    formula = expand_user_macros(formula, operators)
    validate_formula(formula)
    # M1：内部保留名墙在打开数据库前 fail fast（宏/def 展开后文本已含全部定义）——
    # compute_formula 顶部同门再验一次（幂等），此处前置让错误先于 DB/加载暴露
    validate_reserved_bindings(formula)
    validate_internal_reads(formula)
    formula = inline_defs(formula)
    formula = rewrite_expr_methods(formula)
    formula = expand_platform_macros(formula)  # 薄封装 → ts_ 表达式（compute_formula 内部再展开幂等无害）
    _check_future_inputs(formula)  # future/label 显式引用 → fail fast（AC-09）
    # ---- M4（G2）池公式：与主公式同一展开/门链（打开 DB 前全部完成）----
    pool = None
    if spec.universe.formula is not None:
        pool = substitute_params(spec.universe.formula, spec.params)
        pool = expand_user_macros(pool, operators)
        validate_reserved_bindings(pool)
        pool = _normalize_pool_formula(pool)
        validate_formula(pool)
        validate_internal_reads(pool)
        pool = inline_defs(pool)
        pool = rewrite_expr_methods(pool)
        pool = expand_platform_macros(pool)
        _check_future_inputs(pool)
        _require_boolean_pool(pool)  # 静态布尔可判定门（动态 dtype 门在求值后）
    return formula, pool


