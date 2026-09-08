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
from factorlab.ops.minute_ops import EXTRA_CODES, register_minute_ops
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
    register_minute_ops()  # 幂等注册 im_*/day_*（minute scope 公式面；daily 的拒门在下方）
    from factorlab.ops.stable_rank import register_stable_rank_ops
    register_stable_rank_ops()  # 幂等注册 cs_stable_rank（registry 可能被 reset_registry 清空）
    _check_future_inputs(formula)
    # scope 门（变换后文本——宏残余/内联 def 已就位）：bars_1m 静态门 vs daily 拒分钟算子
    from factorlab.engine.minute_gate import (
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
        # （实测），必须直接 import 名。bars_1m scope：追加 minute_ops 名（单
        # 字符串多行——minute 红测试实测通过；与注册表名单同源防漂移）。
        extra_codes=("from factorlab.ops.platform_ops import cs_mean, cs_rank\n"
                     + EXTRA_CODES) if scope == "bars_1m"
        else "from factorlab.ops.platform_ops import cs_mean, cs_rank",
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
        # 字面 "signal" 与其它输出并列时（outputs: [signal, neg]），对 neg 换名会
        # 与仍在 frame 的字面 signal 列相撞——先 drop 该字面列（本轮输出即其替身）
        work = sig
        if o != "signal":
            if "signal" in sig.columns:
                work = sig.drop("signal")
            work = work.rename({o: "signal"})
        proc = run_process_chain(work, process, ctx=rd)
        if proc.height != sig.height:
            raise ValueError(
                f"per-output process 链（{o}）行数变化 {sig.height} -> {proc.height}"
                f"——链不允许过滤/聚合（processors 单列覆盖写纪律）")
        proc_o = proc.select(["date", "code", "signal"]).rename({"signal": o})
        key = key.join(proc_o, on=["date", "code"], how="left")
    return key


def _inject_fill_state_seed(
    panel: pl.DataFrame,
    cal: pl.Series,
    rd: Rd,
    ctx: RunContext,
) -> tuple[pl.DataFrame, bool]:
    """跨 chunk 左边界 fill seed（M6-07C2F，原 _compute_signal 内联块抽取；
    label pool 模式复用——池 TS 条件与 signal runtime 必须同左界同 seed，
    否则 chunked 下两 runtime 成员资格不一致 → 对齐校验失败）。

    长期停牌跨块时 load_start 落在停牌中 → 块内无前值 → fill 无法初始化 →
    extra null。从 DB 取 window_start（cal.min()）前每 code 每字段 latest
    non-null 注入 synthetic seed 行（fill 初始化专用）——**fill 后调用方必须
    立即删除**（filter date >= cal.min()），seed 绝不进 formula/CS mask/
    artifact（§16 顺序锁定：fill → trim seed → formula）。

    返回 (panel, seed_added)。"""
    fillable_cols = [c for c in panel.columns if c not in {"date", "code"}]
    if not fillable_cols or not cal.len():
        return panel, False
    ws = cal.min()
    if ws is None:
        return panel, False
    seed_date = ws - datetime.timedelta(days=1)
    first_rows = panel.filter(pl.col("date") == ws)
    need = sorted(first_rows.filter(
        pl.any_horizontal(pl.col(c).is_null() for c in fillable_cols)
    )["code"].unique().to_list())
    if not need:
        return panel, False
    from factorlab.data.source import load_daily_fill_state
    fs = load_daily_fill_state(
        rd, need, before=ws.isoformat(),
        cols=fillable_cols, float32=ctx.float32)
    if not fs.height:
        return panel, False
    seed = pl.DataFrame({
        "date": [seed_date] * fs.height,
        "code": fs["code"].to_list(),
        **{c: fs[c].to_list() for c in fillable_cols if c in fs.columns},
    })
    # seed 列 dtype 与 panel 对齐（load_daily_fill_state 可能按请求列 cast
    # float32，而 panel 侧某些列保持 load_daily 语义）
    panel = pl.concat([seed.cast({c: panel.schema[c]
                                  for c in seed.columns if c in panel.schema}),
                       panel]).sort(["code", "date"])
    return panel, True


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
    pool: str | None = None,
) -> pl.DataFrame:
    """Signal Runtime（M6-03）：listed market skeleton → fill → 复权视图 →
    universe-aware formula → filter(active) → process。

    - TS/TA 使用 is_listed=true 的完整历史（含 in_universe=false 期间——listing 先行）
    - CS/GP 经 __factorlab_universe_active mask 只看到当日 active 横截面
      （M4/G2 池模式：mask = 骨架 in_universe ∧ 池公式条件——池外不进截面）
    - 最终 rows 只保留 active（process chain 只见成员）
    - **本路径绝不计算 forward returns**
    - M2（G1）：outputs 缺省 [signal]（legacy）；多输出时 compute_formula 共享
      一趟向量化 pass 产出全部声明列，process 链逐输出换名过链（见
      _apply_multi_output_process）
    - M4（G2）：pool 非 None 时公式引用列 = 主公式 ∪ 池公式（一趟供给，
      load_code_attributes 恰一次——calls == [1,1] 断言锁），池条件在全骨架
      上 unmasked 求值后重写 mask
    """
    outputs = list(outputs) if outputs is not None else ["signal"]
    # M3（G6）/M4（G2）：开放解析器——主公式与池公式引用列**并集**按来源供给
    # （daily/daily_basic 列走 load_daily；stock_basic 静态属性按需全量供给
    # join，每 code 一行，键 symbol = panel.code）。引用才供给（未引用 → 零
    # 属性读取）；属性列不送 daily 面（load_daily 会当未知列报错）。属性整段
    # 常量：不参与 align/fill/复权，view_prices 后 join 一次。
    visible = attributes_visible(rd)
    formula_cols = _formula_columns(formula)
    if pool is not None:
        formula_cols = sorted(set(formula_cols) | set(_formula_columns(pool)))
    attr_cols = [c for c in formula_cols if c in visible]
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
    panel, _seeded = _inject_fill_state_seed(panel, cal, rd, ctx)
    qfq_base_col = None
    if adjustment == "qfq" and base_adj is not None:
        # M6-07C2E：固定 sample base 列（**不覆盖 raw adj_factor**——字段保持
        # 市场语义，formula=adj_factor 在 FULL/CHUNK 下看到同一 raw 值）。
        # base 与 chunk 划分无关：FULL/CHUNK 共用 run_factor 传入的同一 base。
        panel = panel.join(base_adj, on="code", how="left")
        qfq_base_col = "__factorlab_qfq_base_adj"
    panel = fill_suspension_values(panel)
    if _seeded:
        # seed 只参与 fill 初始化——formula 前必须彻底删除（§15/16）
        panel = panel.filter(pl.col("date") >= cal.min())
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
    # universe mask 列：来源必须是 PIT in_universe（内部保留列，用户不得定义）。
    # M4（G2）池模式：mask 在 join 后重写为 骨架 ∧ 池条件——主公式 CS/GP 只见
    # 池成员当日横截面（4.4 不变式：池外不进截面），TS 仍见池外完整历史
    # （listing 先行语义不变）。
    panel = panel.join(uf.select(["date", "code", "in_universe"]), on=["date", "code"], how="left")
    panel = panel.with_columns(pl.col("in_universe").fill_null(False).alias("__factorlab_universe_active"))
    if pool is not None:
        # 池公式在**全骨架**上求值（unmasked——CS 见完整 listed 当日横截面、
        # gp_ 按属性全骨架组统计；成员资格不可能在成员过滤后的面板上计算）。
        # 与主公式同一趟供给/同一面板（view/attrs 已 join）——成员 = 骨架 ∧ 条件。
        cond = _pool_cond_frame(panel, pool)
        panel = panel.join(cond.select(["date", "code", "signal"])
                           .rename({"signal": "__factorlab_pool_cond"}),
                           on=["date", "code"], how="left")
        panel = panel.with_columns(
            (pl.col("__factorlab_universe_active")
             & pl.col("__factorlab_pool_cond").fill_null(False)
             ).alias("__factorlab_universe_active"))
        panel = panel.drop("__factorlab_pool_cond")
    member_col = "in_universe" if pool is None else "__factorlab_universe_active"
    result = compute_formula(panel, formula,
                             universe_mask="__factorlab_universe_active",
                             outputs=outputs)
    sig = panel.select(["date", "code", member_col, "close"]).join(
        result, on=["date", "code"], how="left")
    sig = sig.filter(pl.col(member_col)).drop(member_col)
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
    pool: str | None = None,
    base_adj: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Label Runtime（M6-03）：listed market history → compute_forward_returns →
    active-at-t keys → LabelArtifact frame。

    - 是否生成 t 的 label 取决于 t 是否 active（t+h 的未来 membership 不参与 censoring）
    - forward endpoint 无真实价格 → label null（sample 尾/停牌/退市——真 null 保持）
    - M6-04：date_start=chunk_start（label 不需要左侧 signal warmup——forward 只
      需要 t 与 t+h，无过去窗口）；date_end=label_end（right lookahead，仅 label）
    - M4（G2）池模式（pool 非 None）：label keys = 池成员 t（in_universe ∧ 池
      条件）。成员资格在 label runtime **独立求值**——与 signal runtime 同一
      复权视图基准（base_adj 同源 fixed sample base）/同一窗口左界（调用方把
      date_start 扩到 load_start + uf 窗口同步，池 TS warmup 一致，chunked 下
      chunk_start 首日成员资格不漂移 → 对齐校验不失败）。forward returns 恒在
      raw 价格上、view 之前计算；池条件才消费视图价格。属性供给只含池公式
      引用列（主公式不进 label runtime）。
    """
    load_cols = ["close", "adj_factor"]
    attr_cols: list[str] = []
    if pool is not None:
        visible = attributes_visible(rd)
        pool_cols = _formula_columns(pool)
        attr_cols = [c for c in pool_cols if c in visible]
        load_cols += [c for c in pool_cols
                      if c not in visible and c not in load_cols]
    attr_df = (load_code_attributes(rd, attr_cols, float32=ctx.float32)
               if attr_cols else None)
    raw = load_daily(
        rd, codes,
        date_start=date_start, date_end=date_end,
        cols=load_cols, float32=ctx.float32,
    ).collect()
    panel = align_to_listing(raw, uf)
    if panel.height == 0:
        raise ValueError("日期段无数据，可运行 data refresh（M3b）")
    if pool is not None:
        # 池 TS warmup 与 signal runtime 同左界 → 同 seed（fill 后立即 trim）
        panel, _seeded = _inject_fill_state_seed(panel, cal, rd, ctx)
    panel = compute_forward_returns(panel)   # fill 之前（现有顺序——停牌 endpoint null 合法）
    panel = fill_suspension_values(panel)
    if pool is not None and _seeded:
        panel = panel.filter(pl.col("date") >= cal.min())
    if pool is None:
        # legacy（无池）：active-at-t keys——原 in_universe filter
        panel = panel.join(uf.select(["date", "code", "in_universe"]),
                           on=["date", "code"], how="left")
        panel = panel.filter(pl.col("in_universe"))
        return panel.select(["date", "code", "forward_return_5d",
                             "forward_return_20d"]).sort(["date", "code"])
    # ---- M4（G2）池模式：成员资格视图与 signal runtime 同基准 ----
    adjustment = getattr(spec, "adjustment", None) or ctx.adjustment
    qfq_base_col = None
    if adjustment == "qfq" and base_adj is not None:
        panel = panel.join(base_adj, on="code", how="left")
        qfq_base_col = "__factorlab_qfq_base_adj"
    asof = None
    if adjustment == "pit_qfq":
        asof = (datetime.date.fromisoformat(spec.date.end)
                if spec.date.end else panel["date"].max())
    panel = view_prices(panel, adjustment, asof=asof, qfq_base_col=qfq_base_col)
    if qfq_base_col is not None:
        # internal base 不进用户公式与 artifact（同 _compute_signal）
        panel = panel.drop(qfq_base_col)
    if attr_df is not None:
        panel = panel.join(attr_df, left_on="code", right_on="symbol",
                           how="left")
    cond = _pool_cond_frame(panel, pool)
    panel = panel.join(uf.select(["date", "code", "in_universe"]),
                       on=["date", "code"], how="left")
    panel = panel.join(cond.select(["date", "code", "signal"]),
                       on=["date", "code"], how="left")
    panel = panel.filter(
        pl.col("in_universe").fill_null(False)
        & pl.col("signal").fill_null(False))
    return panel.select(["date", "code", "forward_return_5d",
                         "forward_return_20d"]).sort(["date", "code"])


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
    # ---- M4（G2）池公式：与主公式同一展开/门链（打开 DB 前全部完成）----
    # v1 文法（_normalize_pool_formula）：单布尔表达式（裸/赋值），赋值名归一
    # signal；保留名绑定门在归一**前**跑（赋值名会被归一掉，但 in_universe 等
    # 绑定入口仍属内部命名空间）；读取/未来引用/布尔可判定门在归一后跑。
    # compute_formula（_pool_cond_frame）内幂等重验一轮（含分区校验/stable
    # rank 改写——与主公式同一门链契约）。
    pool = None
    if spec.universe.formula is not None:
        pool = _substitute_params(spec.universe.formula, spec.params)
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
        # M4（G2）：warmup 覆盖主公式与池公式两者窗口最大值（池 TS 条件在
        # chunk_start 需要与 FULL 相同的左侧历史，否则成员资格漂移）
        ts_need = _ts_window_days(formula)
        if pool is not None:
            ts_need = max(ts_need, _ts_window_days(pool))
        warmup = ctx.warmup_days if ctx.warmup_days is not None \
            else ts_need + _WARMUP_SAFETY_PAD
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
                # M4（G2）池模式：Label 窗口左界扩到 load_start（池 TS warmup
                # 与 signal runtime 同左界——成员资格须同窗同值）
                signal_cal = cal.filter((cal >= load_start) & (cal <= chunk_end))
                label_end = label_lookahead_end(cal, chunk_end,
                                                max(DEFAULT_FORWARD_HORIZONS))
                label_cal = cal.filter(
                    (cal >= (load_start if pool is not None else chunk_start))
                    & (cal <= label_end))
            signal_uf = resolve_universe_frame(spec, rd, dates=signal_cal.to_list(),
                                               candidate_codes=codes)
            label_uf = resolve_universe_frame(spec, rd, dates=label_cal.to_list(),
                                              candidate_codes=codes)
            sig = _compute_signal(rd, ctx, spec, formula, codes, signal_uf,
                                  load_start.isoformat() if load_start else None,
                                  chunk_end.isoformat() if chunk_end else None,
                                  signal_cal, base_adj, outputs=outputs,
                                  pool=pool)
            lab = _compute_labels(rd, ctx, spec, codes, label_uf,
                                  (load_start if pool is not None else chunk_start).isoformat()
                                  if (load_start if pool is not None else chunk_start) else None,
                                  label_end.isoformat() if label_end else None,
                                  label_cal,
                                  pool=pool, base_adj=base_adj)
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
        if pool is not None and signal_df.height == 0:
            # M4（G2）：整体空池 fail fast——不产出空 artifact 静默成功。
            # 部分日无成员是合法语义（成员逐日动态），只有全样本零成员才报错。
            raise ValueError(
                "池公式无成员——全样本没有 (date, code) 同时满足 骨架 ∧ 池条件"
                "（公式/阈值可能过严；空池不产出空 artifact，fail fast）")
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
