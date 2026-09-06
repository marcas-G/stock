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

from factorlab.config import settings as _settings
from factorlab.data.adjust import load_qfq_base_adj, view_prices
from factorlab.data.attributes import attributes_visible, load_code_attributes
from factorlab.data.backend import Rd, open_read
from factorlab.data.calendar import chunk_calendar, fill_suspensions, trading_calendar
from factorlab.data.source import load_daily
from factorlab.data.universe import align_to_listing, resolve_candidate_codes, resolve_universe_frame
from factorlab.domain.frames import LabelArtifact, SignalArtifact, SignalMeta
from factorlab.domain.timing import DEFAULT_EOD_SIGNAL_TIMING
from factorlab.engine.forward import DEFAULT_FORWARD_HORIZONS, compute_forward_returns
from factorlab.engine.partitions import reject_future_shifts, validate_partition_calls
from factorlab.factor.ast_gate import validate_formula
from factorlab.ops.platform_ops import (
    expand_platform_macros,
    expand_user_macros,
    inline_defs,
    register_platform_ops,
    rewrite_expr_methods,
)
from factorlab.ops.polars_ta_wrappers import register_polars_ta_ops
from factorlab.ops.universe_masking import (apply_universe_masking,
                                            validate_reserved_bindings)
from factorlab.process.registry import run_process_chain
from factorlab.spec import FactorSpec

# 名字类墙常量单点定义于 engine/reserved.py（M1 收拢，禁止散落字面量）：
# FUTURE_PREFIXES/FUTURE_NAMES——future/label 字段显式引用 → fail fast
# INTERNAL_PREFIX/INTERNAL_NAMES——引擎内部列（__factorlab_* / in_universe）读/绑定 → fail fast
from factorlab.engine.reserved import (
    FUTURE_NAMES,
    FUTURE_PREFIXES,
    validate_internal_reads,
)


_PARAM_PATTERN = re.compile(r"\$\{(\w+)\}")


def _substitute_params(formula: str, params: dict[str, Any]) -> str:
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
) -> pl.DataFrame:
    """outputs（M2）：None → ["signal"]（旧调用方完全兼容）。声明输出须在公式顶层
    赋值中产生（变换后核对，codegen 前 fail fast）并按 outputs 顺序保留。"""
    outputs = list(dict.fromkeys(outputs or ["signal"]))
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
    from factorlab.ops.stable_rank import rewrite_stable_rank
    formula = rewrite_stable_rank(formula)  # M6-07C2I：cs_rank → 平台 stable 实现（+ import）
    if universe_mask is not None:
        # M6-03：CS/GP 算子的数据参数包 if_else(mask, arg, None)——TS 仍见完整
        # listed history，CS 只见当日 active universe。mask 列必须已存在于 df。
        if universe_mask not in df.columns:
            raise ValueError(f"universe mask 列 {universe_mask!r} 不在输入数据中（内部保留列）")
        formula = apply_universe_masking(formula, universe_mask)
    register_polars_ta_ops()  # 幂等；保证分区校验能识别 ts_/cs_/ta_ 算子
    register_platform_ops()
    from factorlab.ops.stable_rank import register_stable_rank_ops
    register_stable_rank_ops()  # 幂等注册 cs_stable_rank（registry 可能被 reset_registry 清空）
    _check_future_inputs(formula)
    validate_partition_calls(formula)
    reject_future_shifts(formula)
    # M2：声明的输出必须在公式顶层赋值中产生（变换完成后再核对）——codegen 前
    # fail fast，点名缺哪个；避免未定义名退化成 NameError 深层报错
    missing_declared = [o for o in outputs if o not in _declared_output_names(formula)]
    if missing_declared:
        raise ValueError(
            f"因子脚本未产出声明输出列: {missing_declared}（outputs 声明与实际定义不符）")
    result = codegen_exec(
        df.lazy(),
        formula,
        over_null="partition_by",
        style="polars",
        date=date,
        asset=asset,
        # M3（G6）：组算子翻译产物符号注入 codegen 作用域——gp_ 前缀函数
        # （gp_mean/gp_rank，已注册 registry/分区校验/masking）经 expr_codegen
        # printer 翻译为 cs_<名>(<去 key>) + .over(_DATE_, '<key>')：key 只作
        # 分区列、按日×组分区。生成代码 exec 需解析翻译产物 cs_mean/cs_rank
        # （platform_ops 模块符号，不注册——公式层直写会被 partition 门拒），
        # 否则 NameError。extra_codes 无条件追加 import 头（未使用 import 无
        # 副作用；codegen_exec 的 extra_codes 是单字符串——整体直接复制进生成
        # 代码头部，非序列）。模块别名 import 形式在 codegen 作用域不可用
        # （实测），必须直接 import 名。
        extra_codes="from factorlab.ops.platform_ops import cs_mean, cs_rank",
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
class RunContext:
    """运行上下文。universe_override：6 位代码（如 600519）、universe 引用名或 yaml 文件路径。
    adjustment：复权视图口径兜底（raw|qfq|hfq|pit_qfq；spec.adjustment 声明时以 spec 为准）。
    chunk_days：日期分块（交易日/块；None=单块整段跑）。warmup_days：TS 窗口预热天数
    （None=按公式自动提取窗口最大值 + 20 安全垫）。"""

    db_path: Path = _settings.platform_db  # duckdb 后端读 + 写路径（data rebuild/refresh）
    data_backend: str | None = None  # 读路径后端 "duckdb"|"ch"（None → settings.data_backend）
    output_dir: Path = Path("results")
    universe_override: str | None = None
    float32: bool = _settings.use_float32
    adjustment: str = "qfq"
    chunk_days: int | None = None
    warmup_days: int | None = None


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
    from factorlab.artifacts import validate_signal_label_alignment
    if signal_artifact is not None:
        validate_signal_label_alignment(signal_artifact, label_artifact)
    elif not signal_df.select(["date", "code"]).equals(labels_df.select(["date", "code"])):
        raise ValueError(
            "Signal/Label (date, code) key 不一致（含顺序）——多输出 panel 构造拒绝")
    label_values = labels_df.select(["forward_return_5d", "forward_return_20d"])
    panel = signal_df.hstack(label_values)
    return panel.select([c for c in _chunk_keep(outputs) if c in panel.columns])


def _apply_multi_output_process(
    sig: pl.DataFrame,
    outputs: list[str],
    process: list[str],
    rd: Rd,
) -> pl.DataFrame:
    """M2（G1）：多输出 per-output process 链。

    processors 的单列约束（只写 alias(SIGNAL)、不增删行）是换名副本的合法性
    前提：对每个输出 o，把 sig 的 o 列改名为 signal（其余列原样）过整条链；
    逐输出以 (date, code) 键 join 收回原名（不依赖链内行序），close 原样保留。
    """
    key = sig.select(["date", "code", "close"])
    for o in outputs:
        # 换名（rename）而非 drop+with_columns：drop 后原列已不存在，无法在同一
        # frame 上 expr 引用——rename 让 o 列直接以 signal 名义进入链
        work = sig if o == "signal" else sig.rename({o: "signal"})
        proc = run_process_chain(work, process, ctx=rd)
        if proc.height != sig.height:
            raise ValueError(
                f"per-output process 链（{o}）行数变化 {sig.height} -> {proc.height}"
                f"——链不允许过滤/聚合（processors 单列覆盖写纪律）")
        proc_o = proc.select(["date", "code", "signal"]).rename({"signal": o})
        key = key.join(proc_o, on=["date", "code"], how="left")
    return key


def _compute_signal(
    rd: Rd,
    ctx: RunContext,
    spec: FactorSpec,
    formula: str,
    codes: list[str],
    uf: pl.DataFrame,
    date_start: str,
    date_end: str,
    cal: pl.Series,
    base_adj: pl.DataFrame | None = None,
    outputs: list[str] | None = None,
) -> pl.DataFrame:
    """Signal Runtime（M6-03）：listed market skeleton → fill → 复权视图 →
    universe-aware formula → filter(active) → process。

    - TS/TA 使用 is_listed=true 的完整历史（含 in_universe=false 期间——listing 先行）
    - CS/GP 经 __factorlab_universe_active mask 只看到当日 active 横截面
    - 最终 rows 只保留 in_universe=true（process chain 只见 active）
    - **本路径绝不计算 forward returns**
    - M2（G1）：outputs 缺省 [signal]（legacy）；多输出时 compute_formula 共享
      一趟向量化 pass 产出全部声明列，process 链逐输出换名过链（见
      _apply_multi_output_process）
    """
    outputs = list(outputs) if outputs is not None else ["signal"]
    # M3（G6）：开放解析器——公式引用列按来源供给：daily/daily_basic 列走
    # load_daily（M1 分类器负责归类/报错）；stock_basic 静态属性（industry 等）
    # 按需全量供给 join（每 code 一行，键 symbol = panel.code）。引用才供给
    # （未引用 → 零属性读取）；属性列不送 daily 面（load_daily 会当未知列报错）。
    # 属性整段常量：不参与 align/fill/复权，view_prices 后 join 一次。
    formula_cols = _formula_columns(formula)
    attr_cols = [c for c in formula_cols if c in attributes_visible(rd)]
    data_cols = [c for c in formula_cols if c not in attr_cols]
    attr_df = (load_code_attributes(rd, attr_cols, float32=ctx.float32)
               if attr_cols else None)
    raw = load_daily(
        rd, codes,
        date_start=date_start, date_end=date_end,
        cols=data_cols + ["close", "adj_factor"], float32=ctx.float32,
    ).collect()
    panel = align_to_listing(raw, uf)   # is_listed skeleton（停牌日保留 null 行）
    if panel.height == 0:
        raise ValueError("日期段无数据，可运行 data refresh（M3b）")
    adjustment = getattr(spec, "adjustment", None) or ctx.adjustment
    # ---- M6-07C2F：boundary fill state（跨 chunk 左边界 seed）----
    # 长期停牌跨块时 load_start 落在停牌中 → 块内无前值 → fill 无法初始化 →
    # extra null。从 DB 取 window_start 前每 code 每字段 latest non-null 注入
    # synthetic seed 行（fill 初始化专用）——**fill 后立即删除**，seed 绝不进
    # formula/CS mask/artifact（§16 顺序锁定：fill → trim seed → formula）。
    fillable_cols = [c for c in panel.columns if c not in {"date", "code"}]
    seed = None
    seed_date = None
    if fillable_cols and cal.len():
        ws = cal.min()
        if ws is not None:
            seed_date = ws - datetime.timedelta(days=1)
            first_rows = panel.filter(pl.col("date") == ws)
            need = sorted(first_rows.filter(
                pl.any_horizontal(pl.col(c).is_null() for c in fillable_cols)
            )["code"].unique().to_list())
            if need:
                from factorlab.data.source import load_daily_fill_state
                fs = load_daily_fill_state(
                    rd, need, before=ws.isoformat(),
                    cols=fillable_cols, float32=ctx.float32)
                if fs.height:
                    seed = pl.DataFrame({
                        "date": [seed_date] * fs.height,
                        "code": fs["code"].to_list(),
                        **{c: fs[c].to_list() for c in fillable_cols if c in fs.columns},
                    })
                    # seed 列 dtype 与 panel 对齐（load_daily_fill_state 可能按
                    # 请求列 cast float32，而 panel 侧某些列保持 load_daily 语义）
                    panel = pl.concat([seed.cast({c: panel.schema[c]
                                                  for c in seed.columns if c in panel.schema}),
                                       panel]).sort(["code", "date"])
    qfq_base_col = None
    if adjustment == "qfq" and base_adj is not None:
        # M6-07C2E：固定 sample base 列（**不覆盖 raw adj_factor**——字段保持
        # 市场语义，formula=adj_factor 在 FULL/CHUNK 下看到同一 raw 值）。
        # base 与 chunk 划分无关：FULL/CHUNK 共用 run_factor 传入的同一 base。
        panel = panel.join(base_adj, on="code", how="left")
        qfq_base_col = "__factorlab_qfq_base_adj"
    panel = fill_suspension_values(panel)
    if seed is not None:
        # seed 只参与 fill 初始化——formula 前必须彻底删除（§15/16）
        panel = panel.filter(pl.col("date") >= ws)
    asof = None
    if adjustment == "pit_qfq":
        asof = datetime.date.fromisoformat(spec.date.end) if spec.date.end else panel["date"].max()
    panel = view_prices(panel, adjustment, asof=asof, qfq_base_col=qfq_base_col)
    if qfq_base_col is not None:
        # internal base 不进用户公式（compute_formula 前 drop）与 artifact
        panel = panel.drop(qfq_base_col)
    if attr_df is not None:
        # M3（G6）：属性 join（view 后）——属性每 code 整段常量，与 align/fill 的
        # (code, date) 骨架正交；join 键 symbol = panel.code（canonicalization 在
        # artifact boundary，此处 code 仍是 symbol；polars 不同名键 join 消费右侧
        # 键列，输出只有 panel 全列 + 属性列）。停牌 null 行同 code 属性在行
        # （组键齐全）；真 null 属性（空串已 decode 为 null）不进组。
        panel = panel.join(attr_df, left_on="code", right_on="symbol",
                           how="left")
    # universe mask 列：来源必须是 PIT in_universe（内部保留列，用户不得定义）
    panel = panel.join(uf.select(["date", "code", "in_universe"]), on=["date", "code"], how="left")
    panel = panel.with_columns(pl.col("in_universe").fill_null(False).alias("__factorlab_universe_active"))
    result = compute_formula(panel, formula,
                             universe_mask="__factorlab_universe_active",
                             outputs=outputs)
    sig = panel.select(["date", "code", "in_universe", "close"]).join(
        result, on=["date", "code"], how="left")
    sig = sig.filter(pl.col("in_universe")).drop("in_universe")
    if outputs == ["signal"]:
        # legacy 单输出：chain 直接消费 signal 列——字节级路径不变
        sig = run_process_chain(sig, spec.process, ctx=rd)
    else:
        # M2（G1）：per-output 换名副本过链（共享一趟 compute pass，见
        # _apply_multi_output_process——processors 单列纪律保证合法性）
        sig = _apply_multi_output_process(sig, outputs, spec.process, rd)
    return sig.sort(["date", "code"])


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


def _compute_labels(
    rd: Rd,
    ctx: RunContext,
    spec: FactorSpec,
    codes: list[str],
    uf: pl.DataFrame,
    date_start: str,
    date_end: str,
    cal: pl.Series,
) -> pl.DataFrame:
    """Label Runtime（M6-03）：listed market history → compute_forward_returns →
    active-at-t keys → LabelArtifact frame。

    - 是否生成 t 的 label 取决于 t 是否 active（t+h 的未来 membership 不参与 censoring）
    - forward endpoint 无真实价格 → label null（sample 尾/停牌/退市——真 null 保持）
    - M6-04：date_start=chunk_start（label 不需要左侧 signal warmup——forward 只
      需要 t 与 t+h，无过去窗口）；date_end=label_end（right lookahead，仅 label）
    """
    raw = load_daily(
        rd, codes,
        date_start=date_start, date_end=date_end,
        cols=["close", "adj_factor"], float32=ctx.float32,
    ).collect()
    panel = align_to_listing(raw, uf)
    if panel.height == 0:
        raise ValueError("日期段无数据，可运行 data refresh（M3b）")
    panel = compute_forward_returns(panel)   # fill 之前（现有顺序——停牌 endpoint null 合法）
    panel = fill_suspension_values(panel)
    panel = panel.join(uf.select(["date", "code", "in_universe"]), on=["date", "code"], how="left")
    panel = panel.filter(pl.col("in_universe"))
    return panel.select(["date", "code", "forward_return_5d", "forward_return_20d"]).sort(["date", "code"])


def run_factor(spec: FactorSpec, ctx: RunContext) -> FactorResult:
    """M6-03 装配链路：两条独立 runtime——

        Listed Market History → Signal Runtime → SignalArtifact
        Listed Market History → Label Runtime → LabelArtifact
        （PIT UniverseFrame 在两条路径的入口：listed skeleton + active mask/keys）

    Signal 路径绝不计算 forward returns；Label 路径独立调用 compute_forward_returns。
    legacy panel = signal LEFT JOIN labels（CLI/eval 兼容视图）。"""
    if spec.factors is not None:
        raise NotImplementedError("多因子 factors/combine 组合不在平台范围（平台定位单因子计算与评估）")
    # 展开链（打开数据库前全部完成，语法/参数错误先暴露）：
    # spec.params 顶层参数先替换（宏体经 operators 副本、def 体在 formula 文本内一并命中）
    # → spec.operators 内联宏展开（用户宏公式可引用平台薄封装与 ${}）
    # → 校验 → def 内联（窗口算子合法化为顶层 ts_ 调用）→ 平台薄封装展开
    formula = _substitute_params(spec.formula or "", spec.params)
    operators = {
        name: op.model_copy(update={"formula": _substitute_params(op.formula, spec.params)})
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
    # M2（G1）：outputs 声明（spec 加载期四规则已校验）——缺省 [signal] = legacy
    outputs = list(spec.outputs) if spec.outputs is not None else ["signal"]
    signal_artifact: SignalArtifact | None = None
    signal_frames: dict[str, pl.DataFrame] | None = None
    try:
        rd = open_read(data_backend=ctx.data_backend, db_path=ctx.db_path)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"数据库不存在: {ctx.db_path}（可运行 data refresh 或检查路径）") from exc
    try:
        codes = resolve_candidate_codes(spec, rd, override=ctx.universe_override)
        cal = trading_calendar(rd, date_start=spec.date.start, date_end=spec.date.end)
        # trade_cal 含未来公告日（~94 个到 20261231）：补全面板截断到今天，不产生未来 null 行
        today = datetime.date.today()
        cal = cal.filter(cal <= today)
        if cal.len() == 0:
            raise ValueError("日期段无数据，可运行 data refresh（M3b）")
        warmup = ctx.warmup_days if ctx.warmup_days is not None \
            else _ts_window_days(formula) + _WARMUP_SAFETY_PAD
        # M6-07C2E：qfq 固定 sample base 与执行模式无关——FULL/CHUNK 同一 base。
        # effective_end：spec.date.end（非交易日合法，取 <= end 最后 adj）或
        # 研究 calendar 最后一天（无 end 时不读库中未来 adj_factor）。
        adjustment = getattr(spec, "adjustment", None) or ctx.adjustment
        if adjustment == "qfq":
            effective_end = spec.date.end if spec.date.end \
                else (cal[-1].isoformat() if cal.len() else None)
            base_adj = load_qfq_base_adj(rd, effective_end)
        else:
            base_adj = None
        if ctx.chunk_days is None:
            start_d = datetime.date.fromisoformat(spec.date.start) if spec.date.start else None
            end_d = datetime.date.fromisoformat(spec.date.end) if spec.date.end else None
            chunks = [(start_d, start_d, end_d)]
        else:
            chunks = chunk_calendar(cal, ctx.chunk_days, warmup)
        sig_parts, lab_parts = [], []
        for load_start, chunk_start, chunk_end in chunks:
            if ctx.chunk_days is None:
                # 单块全历史：signal/label 同窗口，无 lookahead——
                # label_end = sample end（截断后 cal 最后一天；spec.date.end 可能
                # 是非交易日，不在 cal——不能做额外 lookahead）
                signal_cal = label_cal = cal
                label_end = cal[-1] if cal.len() else None
            else:
                # M6-04 双窗口：
                #   Signal: [left warmup | output chunk]——结束于 chunk_end
                #   Label:  [output chunk | right lookahead]——结束于 label_end
                signal_cal = cal.filter((cal >= load_start) & (cal <= chunk_end))
                label_end = label_lookahead_end(cal, chunk_end,
                                                max(DEFAULT_FORWARD_HORIZONS))
                label_cal = cal.filter((cal >= chunk_start) & (cal <= label_end))
            signal_uf = resolve_universe_frame(spec, rd, dates=signal_cal.to_list(),
                                               candidate_codes=codes)
            label_uf = resolve_universe_frame(spec, rd, dates=label_cal.to_list(),
                                              candidate_codes=codes)
            sig = _compute_signal(rd, ctx, spec, formula, codes, signal_uf,
                                  load_start.isoformat() if load_start else None,
                                  chunk_end.isoformat() if chunk_end else None,
                                  signal_cal, base_adj, outputs=outputs)
            lab = _compute_labels(rd, ctx, spec, codes, label_uf,
                                  chunk_start.isoformat() if chunk_start else None,
                                  label_end.isoformat() if label_end else None,
                                  label_cal)
            if ctx.chunk_days is not None:
                # 双边裁剪 [chunk_start, chunk_end]：right-lookahead rows 不得
                # 进入任何输出（signal/label/panel）；每块算完即裁剪到对齐输出列
                # （全列面板堆叠会让峰值内存 = 所有块之和，OOM）
                sig = sig.filter((pl.col("date") >= chunk_start) & (pl.col("date") <= chunk_end))
                lab = lab.filter((pl.col("date") >= chunk_start) & (pl.col("date") <= chunk_end))
            sig_parts.append(sig.select([c for c in _chunk_keep(outputs) if c in sig.columns]))
            lab_parts.append(lab)
        signal_df = pl.concat(sig_parts)
        labels_df = pl.concat(lab_parts)
        if ctx.chunk_days is not None:
            del sig_parts, lab_parts, signal_cal, label_cal, base_adj  # 立即释放块级引用（评估阶段省内存）
        # M7-05：artifact boundary canonicalization——内部 symbol（"000001"）→
        # canonical ts_code（"000001.SZ"，stock_basic reference data，一次 mapping）。
        # Signal/Label/panel 正式 artifact 的 code 必须为 canonical research
        # identifier（M7/M8 消费方 canonical guard 的唯一合法输入）。
        from factorlab.data.universe import resolve_canonical_code_map
        canonical_map = resolve_canonical_code_map(rd, codes)
        signal_df = _canonicalize_artifact_codes(signal_df, canonical_map)
        labels_df = _canonicalize_artifact_codes(labels_df, canonical_map)
        codes = canonical_map["code"].to_list()   # summary.codes 同 namespace
        # M6-01 domain contract 接线
        adjustment = getattr(spec, "adjustment", None) or ctx.adjustment
        meta = SignalMeta(name=spec.name, frequency="1d",
                          timing=DEFAULT_EOD_SIGNAL_TIMING, adjustment=adjustment)
        if outputs == ["signal"]:
            # legacy 单输出：SignalArtifact 单列 signal（契约不变）
            signal_artifact = SignalArtifact(
                frame=signal_df.select(["date", "code", "signal"]), meta=meta)
            signal_frames = None
        else:
            # M2（G1）：多输出无单列 signal artifact——逐输出 frame 独立落盘
            # （不写 signal.parquet，绝不提供"signal = 某输出"的隐式别名）
            signal_artifact = None
            signal_frames = {o: signal_df.select(["date", "code", o])
                             for o in outputs}
        label_artifact = LabelArtifact(
            frame=labels_df.select(["date", "code", "forward_return_5d", "forward_return_20d"]))
        # legacy panel：Signal/Label key 对齐已证明 → 位置化附加 label 值列
        # （M6-07C2B：不做 hash join——1,155 万行 × 2 侧的 join 峰值分配在
        # 无页面文件机器上撞 commit 空间 → 0xC0000005；多输出下对齐由
        # _build_legacy_panel 键 equals 直验）
        panel = _build_legacy_panel(signal_df, labels_df, signal_artifact, label_artifact,
                                    outputs)
    finally:
        rd.close()

    # M6-05：统一 artifact persistence——signal → labels → panel → summary（最后 = 完成标记）
    from factorlab.artifacts import write_factor_artifacts
    if signal_artifact is not None:
        summary = {
            "name": spec.name,
            "category": spec.category,
            "direction": spec.direction,
            "universe_count": len(codes),   # 兼容字段（legacy 语义——候选集规模）
            "candidate_count": len(codes),
            "codes": codes,
            "date_start": str(panel["date"].min()),  # panel.height == 0 已在链路中 raise，无需兜底
            "date_end": str(panel["date"].max()),
            "panel_rows": panel.height,
            "signal_rows": signal_artifact.frame.height,
            "label_rows": label_artifact.frame.height,
            "signal_null_ratio": round(panel["signal"].null_count() / panel.height, 4),
            "runtime_semantics": "pit_universe_signal_label_v1",
            "process": spec.process,
            "adjustment": adjustment,
            "float32": ctx.float32,
            "spec_yaml": yaml.safe_dump(spec.model_dump(), allow_unicode=True),
        }
        summary = write_factor_artifacts(ctx.output_dir, signal_artifact, label_artifact,
                                         panel, summary)
        return FactorResult(spec=spec, signal_artifact=signal_artifact,
                            label_artifact=label_artifact, panel=panel, summary=summary)
    # M2（G1）多输出分支：per-output signal__<output>.parquet × N → labels → panel
    from factorlab.artifacts import write_multi_output_factor_artifacts
    summary = {
        "name": spec.name,
        "category": spec.category,
        "direction": spec.direction,
        "universe_count": len(codes),
        "candidate_count": len(codes),
        "codes": codes,
        "date_start": str(panel["date"].min()),
        "date_end": str(panel["date"].max()),
        "panel_rows": panel.height,
        "outputs": outputs,
        "signals": {
            o: {"rows": frame.height,
                "null_ratio": round(frame[o].null_count() / frame.height, 4)}
            for o, frame in signal_frames.items()
        },
        "runtime_semantics": "pit_universe_signal_label_v1",
        "process": spec.process,
        "adjustment": adjustment,
        "float32": ctx.float32,
        "spec_yaml": yaml.safe_dump(spec.model_dump(), allow_unicode=True),
    }
    summary = write_multi_output_factor_artifacts(ctx.output_dir, signal_frames, meta,
                                                  label_artifact, panel, summary)
    return FactorResult(spec=spec, signal_artifact=None, label_artifact=label_artifact,
                        panel=panel, summary=summary, signals=signal_frames)
