"""M5（design §5.4 + §6 G4/G5）：schema 元数据 → 目录正文 + 机器可读 JSON + 错误修复手册。

目录五部分（活文档，同源生成，不手抄）：
- scope/source_ref/schema_version：元信息。数据全开放——目录是活文档不是校验门，
  任何真实存在的读面列/字段/新算子都自由（无字段白名单；纪律只拦名字类）。
- open_surface：columns（字段语义/单位/产生时点，来自 source 常量并集）、
  operators（平台自有 6 行 + registry 实时清单 + 元素级方法链白名单 + 分区前缀族）、
  def_composition（组合规范 + 示例）、naming（命名类约定，来自 engine.reserved）。
- closed_gates：关闭面三类墙（syntax_efficiency/future/internal）逐条收录，
  与错误修复手册同源（过滤 category ∈ 墙类别）。
- error_handbook：全部错误门的 rule/trigger/sample/fix/tests/probe——sample 是
  源码里逐字存在的报错文案样板（三重锁：源码逐字、probe 真实 raise 含样板、
  对照测试真实存在），tests 引用 tests/<file>.py::<test>。
- known_approximations：已知近似（design §7 三条逐字语义）。

目录正文 = render_catalog_markdown() 输出（docs/catalog.md 逐字节同源，防陈旧）；
JSON = catalog_json()（确定性别名导出）。
"""

from __future__ import annotations

import json

from factorlab.data import source as _source
from factorlab.data.source import _MSG_COLUMN_DIR_HINT, _MSG_RAW_MAPPED_PREFIX
from factorlab.data.verify import (
    _FUTURE_COL_FIX,
    _FUTURE_COL_SUFFIX,
    _INTERNAL_COL_FIX,
    _INTERNAL_COL_SUFFIX,
)
from factorlab.core.engine import reserved as _reserved
from factorlab.core.factor.ast_gate import ALLOWED_EXPR_METHODS
from factorlab.core.ops import registry as _registry
from factorlab.core.ops.platform_ops import register_platform_ops
from factorlab.core.ops.polars_ta_wrappers import register_polars_ta_ops
from factorlab.core.ops.minute_ops import register_minute_ops
from factorlab.core.ops.stable_rank import register_stable_rank_ops

SCHEMA_VERSION = 1

# 占位/省略式描述词：validate_catalog 在任何描述字段发现这些词即拒绝
# （"详见 interface.md"类文本同样含占位词命中——目录要求事无巨细，不许指向他处省略）
STUB_WORDS = (
    "详见", "见上文", "待补充", "待写", "同前", "占位",
    "TODO", "TBD", "FIXME", "stub", "placeholder",
)

# ---------------------------------------------------------------------------
# 开放面：字段（语义/单位/产生时点）
# ---------------------------------------------------------------------------
# 列清单必须与解析器/读路径单源常量并集一致（防漂移），且新增列必须有元数据：
# _col_row 对未知名直接 KeyError——目录完整性在 build 期强制。
# 口径：tushare 原始值直通（重建链路不换算）；不复权 = 原始价。
_COLUMN_META = {
    # ---- daily（_PLATFORM_COLS）----
    "open": {
        "kind": "行情",
        "semantic": "开盘价——不复权原始价（当日第一笔成交价，不含集合竞价以外调整）",
        "unit": "元",
        "timing": "T 日开盘时点（09:30 前集合竞价撮合结果）",
    },
    "high": {
        "kind": "行情",
        "semantic": "最高价——不复权原始价（当日盘中最高成交价）",
        "unit": "元",
        "timing": "T 日盘中（15:00 前滚动更新，盘后定值）",
    },
    "low": {
        "kind": "行情",
        "semantic": "最低价——不复权原始价（当日盘中最低成交价）",
        "unit": "元",
        "timing": "T 日盘中（15:00 前滚动更新，盘后定值）",
    },
    "close": {
        "kind": "行情",
        "semantic": "收盘价——不复权原始价（当日最后一笔成交价，官方收盘口径；前复权由 qfq 基准折算，不复权是读面基准价）",
        "unit": "元",
        "timing": "T 日盘后时点（15:00 收盘定值，回看即已定）",
    },
    "pre_close": {
        "kind": "行情",
        "semantic": "前收盘价——不复权原始价（T-1 收盘；除权除息日为除权参考价，与当日真实昨收不同源）",
        "unit": "元",
        "timing": "T 日盘前（T-1 收盘定值）",
    },
    "change": {
        "kind": "行情",
        "semantic": "涨跌额——不复权原始价（close − pre_close）",
        "unit": "元",
        "timing": "T 日盘后时点",
    },
    "pct_chg": {
        "kind": "行情",
        "semantic": "涨跌幅（%——(close/pre_close − 1)×100；除权除息日按除权参考价口径，跨除权的真实收益用前复权价自算）",
        "unit": "%",
        "timing": "T 日盘后时点",
    },
    "volume": {
        "kind": "行情",
        "semantic": "成交量——股（2026-09-08 实测：daily.vol 对 bars_1m 按 (code, 交易日) "
                   "汇总比值 ≈ 1，与分钟面同单位）",
        "unit": "股",
        "timing": "T 日盘后（全天累计成交量）",
    },
    "amount": {
        "kind": "行情",
        "semantic": "成交额——元（2026-09-08 实测：daily.amount 对 bars_1m 按 (code, "
                   "交易日) 汇总比值 ≈ 1，与分钟面同单位）",
        "unit": "元",
        "timing": "T 日盘后（全天累计成交额）",
    },
    # ---- daily_basic（_DAILY_BASIC_MAP：估值/交易指标，独立表 join 读面）----
    "turnover": {
        "kind": "估值/交易指标",
        "semantic": "换手率（%——成交量/流通股本）",
        "unit": "%",
        "timing": "T 日盘后公布（含当日）",
    },
    "total_mv": {
        "kind": "估值/交易指标",
        "semantic": "总市值——万元（未复权股本口径）",
        "unit": "万元",
        "timing": "T 日盘后公布（含当日）",
    },
    "circ_mv": {
        "kind": "估值/交易指标",
        "semantic": "流通市值——万元（未复权股本口径）",
        "unit": "万元",
        "timing": "T 日盘后公布（含当日）",
    },
    "pe_ttm": {
        "kind": "估值/交易指标",
        "semantic": "市盈率 TTM（倍——亏损股为负值，非缺失）",
        "unit": "倍",
        "timing": "T 日盘后公布（盈利取最近报告期外推 TTM）",
    },
    "pb": {
        "kind": "估值/交易指标",
        "semantic": "市净率（倍——每股净资产取最近报告期）",
        "unit": "倍",
        "timing": "T 日盘后公布",
    },
    "dv_ratio": {
        "kind": "估值/交易指标",
        "semantic": "股息率（%——近 12 个月每股分红/股价）",
        "unit": "%",
        "timing": "T 日盘后公布",
    },
    "volume_ratio": {
        "kind": "估值/交易指标",
        "semantic": "量比（倍——当日每分钟均量/过去 5 日每分钟均量，衡量放量程度）",
        "unit": "倍",
        "timing": "T 日盘中实时（15:00 收盘定值）",
    },
    # ---- special（_SPECIAL_COLS：解码后 join 进面板的引擎特殊名）----
    "date": {
        "kind": "键",
        "semantic": "交易日键——YYYYMMDD 文本（解码后恒在；所有日频行按它对齐）",
        "unit": "—（键列）",
        "timing": "恒在（T 日标识）",
    },
    "code": {
        "kind": "键",
        "semantic": "股票代码键——ts_code 带后缀形态（000001.SZ；解码后恒在；与纯数字写法映射提示见 hint 行）",
        "unit": "—（键列）",
        "timing": "恒在（个体标识）",
    },
    "adj_factor": {
        "kind": "特殊",
        "semantic": "复权因子（后复权口径累积因子——除权除息日调整；前复权价 = 不复权价 × 因子 / 最新因子，qfq 基准见读路径折算）",
        "unit": "倍",
        "timing": "T 日盘后（与当日行情同步）",
    },
    "idx_ret": {
        "kind": "特殊",
        "semantic": "指数日收益率——小数（基准指数当日 pct_chg/100；市场状态代理，默认中证 1000，cols 含 idx_ret 时 join）",
        "unit": "小数（0.01 = 1%）",
        "timing": "T 日盘后（指数同日收盘）",
    },
    # ---- attributes（属性面：非逐日行情，最新快照）----
    "industry": {
        "kind": "属性",
        "semantic": "行业——当前分类值（申万行业最新归属，属性面按股票回填；非历史时点 PIT 值——回看样本分组按最新分类，近似见已知近似表）",
        "unit": "—（分类文本）",
        "timing": "属性时点（最新快照，非逐日变更）",
    },
    # ---- hint（原始列名映射提示行：不供给公式，只进报错助手语义）----
    "vol": {
        "kind": "hint",
        "semantic": "原始列名映射提示：vol → volume（平台库原始列不同名——公式写 vol 报「未知列名」并给此映射）",
        "unit": "—（提示行）",
        "timing": "—（提示行）",
    },
    "ts_code": {
        "kind": "hint",
        "semantic": "原始列名映射提示：ts_code → code（平台库原始列不同名——公式写 ts_code 报「未知列名」并给此映射）",
        "unit": "—（提示行）",
        "timing": "—（提示行）",
    },
    "trade_date": {
        "kind": "hint",
        "semantic": "原始列名映射提示：trade_date → date（平台库原始列不同名——公式写 trade_date 报「未知列名」并给此映射）",
        "unit": "—（提示行）",
        "timing": "—（提示行）",
    },
}

# 引擎特殊名字的原文口径见 source._SPECIAL_COLS 注释；daily_basic 键顺序与读面映射
# 一致（表归属按所属常量组，禁止手改——_column_rows 由常量驱动）
_SURFACE_GROUPS = (
    ("daily", _source._PLATFORM_COLS),
    ("daily_basic", tuple(_source._DAILY_BASIC_MAP)),
    ("special", _source._SPECIAL_COLS),
)
_HINT_GROUPS = (("attributes", ("industry",)), ("hint", tuple(_source._RAW_MAP_HINTS)))


def _column_rows() -> list[dict[str, str]]:
    """列行表：常量驱动（daily/daily_basic/special 并集 = 读面供给集；
    attributes/hint 附加行不入供给集）。未知列名 KeyError——目录完整性强制。"""
    rows: list[dict[str, str]] = []
    for table, names in _SURFACE_GROUPS + _HINT_GROUPS:
        for name in names:
            meta = _COLUMN_META[name]
            rows.append({"name": name, "table": table, **meta})
    return rows


# ---------------------------------------------------------------------------
# 开放面：算子（平台自有 6 行 + registry 实时清单）
# ---------------------------------------------------------------------------
# platform_owned 行语义/约束与注册清单同源（owned ⊆ inventory 在 build 期断言）；
# 约束措辞与关闭面门表一致（窗口/截面算子不能进 def 体内联）。
_PLATFORM_OWNED = (
    {
        "name": "returns", "kind": "ts",
        "semantic": "单期收益率：close / pre_close − 1（当日对前收盘的简单收益；不复权口径，跨除权日失真见 close 语义）",
        "constraints": "无窗口/lookback 参数；注册算子，def 体内联被拒（见 gate_def_inner_window_op）",
    },
    {
        "name": "vwap", "kind": "ts",
        "semantic": "成交量加权均价（累计式——自样本起点累计成交额/累计成交量）",
        "constraints": "累计式：起点敏感，非固定窗；注册算子，def 体内联被拒",
    },
    {
        "name": "adv20", "kind": "ts",
        "semantic": "20 日均成交额/量（流动性与成交活跃度代理）",
        "constraints": "固定 20 日滚动窗（与 ts_mean 同窗语义）；注册算子，def 体内联被拒",
    },
    {
        "name": "gp_rank", "kind": "gp",
        "semantic": "组内排名：gp_rank(key, x)——x 按 key 当日组内 rank（1..K，平均秩；null 不占名次）",
        "constraints": "参数顺序 (key, x)：第一参数为分组键（按交易日分组，键不参与掩码）；组缺失时组内余下成员互相竞争（独木 = 1.0）",
    },
    {
        "name": "gp_mean", "kind": "gp",
        "semantic": "组内均值：gp_mean(key, x)——x 按 key 当日组内均值（null 不参与）",
        "constraints": "参数顺序 (key, x)：第一参数为分组键（按交易日分组）；组缺失时组内独木即其自身值",
    },
    {
        "name": "cs_stable_rank", "kind": "cs",
        "semantic": "横截面 stable dense rank（并列同秩）——pct 输出 = 名次/(当日截面非 null 样本数 − 1)，K=1 → 0.0",
        "constraints": "逐日截面无跨日记忆；掩码外/缺失样本不占名次；cs_rank 是它的公式层别名（alias 可写，canonical 名在注册清单）",
    },
)


def _live_inventory() -> list[dict[str, str]]:
    """注册清单 = registry.list_ops() 实时快照（幂等注册链触发后逐名一致——
    与 compute_formula 相同的三注册器；alias 无独立行）。"""
    register_polars_ta_ops()
    register_platform_ops()
    register_minute_ops()
    register_stable_rank_ops()
    return [{"name": op.name, "kind": op.kind, "version": op.version}
            for op in _registry.list_ops()]


# ---------------------------------------------------------------------------
# 开放面：def 组合规范 / 命名类约定
# ---------------------------------------------------------------------------
_DEF_RULES = (
    "def 用于给一段可复用表达式命名参数化组合；体内只能写赋值与 return（中间变量用 _ 前缀，末尾 return 恰一条）",
    "注册算子（窗口 ts_/截面 cs_/组 gp_/平台自有）不能写在 def 体内——def 按元素级黑盒执行，体内窗口会在全表上滚动、跨资产泄漏；把窗口语义写在调用处的公式顶层",
    "def 形参只允许位置参数（可带缺省值）；*args/**kwargs/kwonly 形参拒绝、调用带关键字参数拒绝（内联展开按位置形参表绑定）",
    "def 不能递归（直接或间接）——内联需要有限展开深度；def 名不能以 _ 开头（_ 是中间变量约定）",
    "同一 def 多次调用各自实例化（中间变量不串、参数不串）；def 调 def 支持且不依赖源码定义顺序",
    "顶层赋值常量（_d = -3 这类）会被负位移静态检查折叠——位移量请写成字面量或顶层常量，便于未来函数门检查（def 形参是未知变量，静态放行，见已知近似）",
)
_DEF_EXAMPLE = '''# 元素级组合 + 缺省参数（def 内不得出现窗口/截面算子）
def scale_shift(x, n=2):
    _m = x * n
    return x / _m

# 窗口语义写在公式顶层（不在 def 体内）
signal = ts_mean(scale_shift(close), 20) - ts_delay(close, 5)
# 若某窗口结果要在 def 内复用：把窗口调用结果作为实参传入——
# windowed = ts_rank(close, 20); signal = scale_shift(windowed)  '''


def _naming() -> dict:
    """命名类约定 = engine/reserved 单源（防漂移）；纪律文本落在开放面。"""
    return {
        "future_prefixes": list(_reserved.FUTURE_PREFIXES),
        "future_names": sorted(_reserved.FUTURE_NAMES),
        "internal_prefix": _reserved.INTERNAL_PREFIX,
        "internal_names": sorted(_reserved.INTERNAL_NAMES),
        "disciplines": [
            "未来类列名（内容含未来信息的列、标签/目标列）必须落 forward_*/future_* 前缀或 target/label 精确名——这是平台命名类约定；读面表按构造即 PIT，未来列禁止进读面（数据入库校验 validate_engine_surface 与活文档双重锁）",
            "内部保留名（__factorlab_* 前缀与 in_universe 精确名）是引擎运行时命名空间：模板公式不可见、不可读写、不可绑定、不由用户数据占用",
            "数据全开放：任何真实存在的读面列/新算子/新字段都自由，无字段白名单——命名纪律只拦名字类（未来信息必须用未来前缀声明、内部名不占用），不设内容检查",
        ],
    }


# ---------------------------------------------------------------------------
# 关闭面：门表（墙行与错误修复手册同源——closed_gates = 手册中墙类别的行）
# ---------------------------------------------------------------------------
# 每条门的 sample 是源码中逐字存在的连续文案样板（无插值占位）；probe 非空时
# exec 必须真实 raise 且文案含样板（DB 无关门）；tests 引用真实对照测试。
def _gates() -> list[dict]:
    """错误修复手册全量行（21 行）：id/category/name/rule/trigger/sample/fix/tests/probe。
    category ∈ {syntax_efficiency, future, internal}(墙) ∪ {spec, processor, helper}。"""
    return [
        {
            "id": "gate_unknown_operator",
            "category": "syntax_efficiency",
            "name": "未知算子（未注册名/typo）",
            "rule": "公式中的算子调用名必须已注册（registry.has_op）或是本公式 def / 元素级函数名；未知名直接拒绝",
            "trigger": "生成/手写公式引用不存在的算子名（拼写、大小写、未触发注册的 polars_ta 名、误以为 def 名可见）",
            "sample": "未知算子: ",
            "fix": "对照本目录「注册清单」（catalog dump / factorlab op list）改正名字：平台算子名全小写下划线式（ts_delay 而非 tsDelay），新算子走 op 插件注册",
            "tests": [
                "tests/test_partitions.py::test_rejects_unknown_operator",
                "tests/test_compute.py::test_compute_rejects_unknown_operator",
            ],
            "probe": "from factorlab.core.engine.partitions import validate_partition_calls\nvalidate_partition_calls('signal = no_such_op(close, 3)')",
        },
        {
            "id": "gate_def_inner_window_op",
            "category": "syntax_efficiency",
            "name": "def 体内窗口/截面算子",
            "rule": "注册算子（窗口 ts_/截面 cs_/组 gp_/平台自有）禁止写在 def 体内——def 按元素级黑盒执行，体内窗口会跨资产滚动泄漏",
            "trigger": "def 体内出现 ts_mean/ts_rank 等注册算子（含 import 别名形态）",
            "sample": "不能在 def 内使用，请直接写在公式顶层",
            "fix": "把窗口语义提到公式顶层（def 只做元素级/参数化组合）；或把窗口结果作为实参传入 def",
            "tests": [
                "tests/test_partitions.py::test_rejects_window_op_inside_def",
                "tests/test_partitions.py::test_import_alias_of_window_op_inside_def_rejected",
            ],
            "probe": "from factorlab.core.ops.polars_ta_wrappers import register_polars_ta_ops\nregister_polars_ta_ops()\nfrom factorlab.core.engine.partitions import validate_partition_calls\nvalidate_partition_calls('def f(x):\\n    return ts_mean(x, 5)\\nsignal = f(close)')",
        },
        {
            "id": "gate_def_varargs",
            "category": "syntax_efficiency",
            "name": "def vararg/kwonly 形参",
            "rule": "def 形参只允许位置参数（可带缺省值）；*args/**kwargs/kwonly 形参一律拒绝",
            "trigger": "def f(x, *args)/f(x, **kwargs)/f(x, *, n)——vararg 数量任意、kwonly 与位置实参错位，内联展开无法绑定",
            "sample": "不支持 *args/**kwargs 参数",
            "fix": "形参全部改写为确定的位置参数表（可带缺省值）——内联展开需要确定形参绑定",
            "tests": [
                "tests/test_inline_defs.py::test_inline_def_varargs_kwonly_rejected",
            ],
            "probe": "from factorlab.core.ops.platform_ops import inline_defs\ninline_defs('def f(x, *args):\\n    return x + args\\n\\nsignal = f(close, 1)')",
        },
        {
            "id": "gate_def_keyword_call",
            "category": "syntax_efficiency",
            "name": "def 调用带关键字参数",
            "rule": "def 调用一律按位置传参——关键字实参在展开后与绑定形参表错位",
            "trigger": "def 调用写 f(x=close, n=20)",
            "sample": "调用不支持关键字参数",
            "fix": "def 调用按形参定义顺序给位置实参",
            "tests": [
                "tests/test_inline_defs.py::test_inline_def_keyword_args_rejected",
            ],
            "probe": "from factorlab.core.ops.platform_ops import inline_defs\ninline_defs('def f(x, n):\\n    return x + n\\n\\nsignal = f(close, n=1)')",
        },
        {
            "id": "gate_def_recursion",
            "category": "syntax_efficiency",
            "name": "递归 def",
            "rule": "def 不能递归（直接或间接）——内联展开需要有限深度",
            "trigger": "def 体内自调用，或 a→b→a 的间接递归环",
            "sample": "递归 def 不支持内联: ",
            "fix": "改写为非递归结构（把递归体展开为有限层或改为顶层窗口组合）",
            "tests": [
                "tests/test_inline_defs.py::test_inline_recursive_def_rejected",
                "tests/test_inline_defs.py::test_inline_indirect_recursion_rejected",
            ],
            "probe": "from factorlab.core.ops.platform_ops import inline_defs\ninline_defs('def f(x):\\n    return f(x)\\n\\nsignal = f(close)')",
        },
        {
            "id": "gate_negative_shift",
            "category": "future",
            "name": "负位移（未来函数）",
            "rule": "ts_delay/ts_delta 的位移参数（第 2 位置参数或关键字 d）必须 ≥ 0——负位移读取未来，公式按构造即 PIT",
            "trigger": "负字面量 -2、可折叠表达式（1−2）、顶层常量间接（_d = -3 后 ts_delay(x, _d)）、import 别名（td(x, -2)）",
            "sample": "不允许负位移（lookback 只能取过去）",
            "fix": "位移参数改正/0；需要未来窗口收益的语义由评估运行时 forward 计算，不由公式读未来",
            "tests": [
                "tests/test_partitions.py::test_rejects_negative_delay",
                "tests/test_partitions.py::test_rejects_negative_delay_via_top_level_const",
                "tests/test_partitions.py::test_rejects_negative_delay_via_alias_import",
            ],
            "probe": "from factorlab.core.engine.partitions import reject_future_shifts\nreject_future_shifts('signal = ts_delay(close, -2)')",
        },
        {
            "id": "gate_future_column_input",
            "category": "future",
            "name": "公式引用未来/标签列",
            "rule": "公式输入不能引用未来列（forward_*/future_*/target/label）——未来数据只由评估运行时在内存计算，读面不供给未来列",
            "trigger": "公式/池公式引用 forward_return_5d 这类列（读面没有——compute 收口拦截）",
            "sample": "future/label inputs are forbidden in factor formula: ",
            "fix": "收益/未来窗口写成公式内对过去的引用（收益对齐与标签由评估框架 forward 计算，见已知近似）",
            "tests": [
                "tests/test_signal_label_runtime.py::test_formula_future_input_guard",
                "tests/test_pool_formula.py::test_pool_reject_future_input",
            ],
            "probe": "from factorlab.core.engine.compute import _check_future_inputs\n_check_future_inputs('x = close + forward_return_5d')",
        },
        {
            "id": "gate_future_surface_discipline",
            "category": "future",
            "name": "读面表未来列（数据侧入库校验）",
            "rule": "引擎读面表（ENGINE_SURFACE_TABLES 9 表：daily/daily_basic/adj_factor/index_daily/stock_basic/trade_cal/stock_st/stk_limit/suspend_d）禁止出现未来前缀列——入库校验 validate_engine_surface/validate_surface_columns 与活文档双重锁",
            "trigger": "重建/入库（build_final_db 收口）时读面表含 forward_*/future_* 前缀或 target/label 精确名列",
            # sample/fix 与 verify.py 违例文案常量同源（import 引用，不抄写——文案单源）
            "sample": _FUTURE_COL_SUFFIX,
            "fix": _FUTURE_COL_FIX,
            "tests": [
                "tests/test_column_discipline.py::test_pure_fn_future_prefixed_columns_rejected",
                "tests/test_column_discipline.py::test_build_final_db_rejects_violating_surface",
            ],
            "probe": "",
        },
        {
            "id": "gate_surface_internal_column",
            "category": "internal",
            "name": "读面表内部保留列（数据侧）",
            "rule": "引擎读面表禁止内部保留名列（__factorlab_* 前缀 / in_universe 精确名）——读面列注入/join 与引擎内部列碰撞毒化面板",
            "trigger": "重建/入库（build_final_db 收口）或读面探测（validate_engine_surface）发现读面表含内部名列",
            # sample/fix 与 verify.py 违例文案常量同源（import 引用，不抄写——文案单源）
            "sample": _INTERNAL_COL_SUFFIX,
            "fix": _INTERNAL_COL_FIX,
            "tests": [
                "tests/test_column_discipline.py::test_pure_fn_internal_name_rejected",
                "tests/test_column_discipline.py::test_engine_surface_reports_violations_via_rd",
            ],
            "probe": "",
        },
        {
            "id": "gate_reserved_binding",
            "category": "internal",
            "name": "绑定/写入保留名",
            "rule": "内部保留名（__factorlab_* / in_universe）不可被用户公式赋值、作参数、作 import 别名",
            "trigger": "公式写 in_universe = 1 / def 参数名撞保留名 / import 别名撞保留名",
            "sample": "（平台内部保留：",
            "fix": "改名——内部名是引擎运行时命名空间（__factorlab_* 前缀 / in_universe），不由模板占用",
            "tests": [
                "tests/test_universe_masking_hardening.py::test_reserved_assignment_fails",
                "tests/test_input_surface.py::test_in_universe_binding_rejected",
            ],
            "probe": "from factorlab.core.ops.universe_masking import validate_reserved_bindings\nvalidate_reserved_bindings('in_universe = 1')",
        },
        {
            "id": "gate_reserved_read",
            "category": "internal",
            "name": "读取内部列/标记",
            "rule": "模板公式不可读取引擎内部列/标记（in_universe/__factorlab_*）——模板可见面只有用户数据列",
            "trigger": "公式引用 in_universe（成员状态由运行时维护，模板读不到）",
            "sample": "（引擎内部列/标记，模板公式不可见）",
            "fix": "成员条件由池公式声明；模板只写数据列，不写运行时内部状态",
            "tests": [
                "tests/test_input_surface.py::test_run_factor_rejects_reading_in_universe",
                "tests/test_pool_formula.py::test_pool_reject_reserved_read",
            ],
            "probe": "from factorlab.core.engine.reserved import validate_internal_reads\nvalidate_internal_reads('x = close + in_universe')",
        },
        {
            "id": "gate_spec_universe_exclusive",
            "category": "spec",
            "name": "universe 成员条件源互斥",
            "rule": "universe 的 ref / codes / rules / formula 四选一：恰一个成员条件源（空对象同样拒绝）",
            "trigger": "spec 同时给 codes 与 rules / codes 与 formula / 空 universe 对象",
            "sample": "universe 必须且只能提供 ref / codes / rules / formula 之一",
            "fix": "成员条件只保留一种形态（静态 codes、规则公式 rules 或 formula 化成员条件，按 M4 文法）",
            "tests": [
                "tests/test_spec.py::test_rejects_universe_both_codes_and_rules",
                "tests/test_pool_formula.py::test_universe_formula_mutually_exclusive_with_others",
            ],
            "probe": "import factorlab.core.spec as spec\nspec.UniverseSpec.model_validate({})",
        },
        {
            "id": "gate_spec_outputs_reserved",
            "category": "spec",
            "name": "outputs 用未来保留名",
            "rule": "FactorSpec.outputs 不得使用未来保留名（forward_*/future_* 前缀 / target/label 精确名）——为数据侧未来列命名纪律，公式输出不可用",
            "trigger": "spec.outputs 写 forward_return_5d / target 等",
            "sample": "为数据侧未来列命名纪律，公式输出不可用）",
            "fix": "输出名避开未来前缀（forward_*/future_*/target/label 由评估运行时 forward 计算占用）",
            "tests": [
                "tests/test_outputs_multi.py::test_outputs_reserved_names_rejected",
                "tests/test_outputs_multi.py::test_outputs_structural_collision_rejected",
            ],
            "probe": "import factorlab.core.spec as spec\nspec.FactorSpec.model_validate({'name': 'f', 'category': 'custom', 'direction': 1, 'universe': {'codes': ['000001']}, 'formula': 'x = close', 'outputs': ['forward_return_5d']})",
        },
        {
            "id": "gate_spec_outputs_duplicate",
            "category": "spec",
            "name": "outputs 重复",
            "rule": "FactorSpec.outputs 全局唯一（同 spec 内重复拒绝）",
            "trigger": "outputs: ['a', 'a']——第 1 与第 2 位置冲突",
            "sample": "outputs 重复: ",
            "fix": "去掉重复输出名（同一表达式要双份请起不同名）",
            "tests": [
                "tests/test_outputs_multi.py::test_outputs_duplicate_rejected",
                "tests/test_outputs_multi.py::test_outputs_empty_rejected",
            ],
            "probe": "import factorlab.core.spec as spec\nspec.FactorSpec.model_validate({'name': 'f', 'category': 'custom', 'direction': 1, 'universe': {'codes': ['000001']}, 'formula': 'x = close', 'outputs': ['a', 'a']})",
        },
        {
            "id": "gate_pool_v1_grammar",
            "category": "spec",
            "name": "池公式 v1 文法（单条布尔）",
            "rule": "池公式 v1 语法只接受单个布尔表达式（裸表达式或单条赋值）——def/多语句不在 v1 文法",
            "trigger": "池公式写 def f(): … / 多条语句 / 多条赋值",
            "sample": "池公式 v1 语法只接受单个布尔表达式（裸表达式或单条赋值）",
            "fix": "成员条件写成一条布尔表达式（裸表达式或单条赋值——def 内联后仍是单条可接受）",
            "tests": [
                "tests/test_pool_formula.py::test_pool_reject_multi_statement",
            ],
            "probe": "from factorlab.core.engine.compute import _normalize_pool_formula\n_normalize_pool_formula('def f():\\n    pass\\nx = 1')",
        },
        {
            "id": "gate_pool_boolean_required",
            "category": "spec",
            "name": "池公式非布尔（语法判定）",
            "rule": "池公式必须布尔可判定——表达式须含比较（>/</==/!=）或布尔运算，逐 (code, 交易日) 得真/假",
            "trigger": "池公式写纯数值/窗口表达式（signal = 1 + 2）作成员条件",
            "sample": "池公式必须布尔可判定（表达式含比较 `>`/`<`/`==`/`!=` 或布尔运算，",
            "fix": "成员条件补比较/布尔运算（如 close > 20 或 (volume > 100) & (close > 20)）",
            "tests": [
                "tests/test_pool_formula.py::test_pool_reject_non_boolean_expression",
            ],
            "probe": "from factorlab.core.engine.compute import _require_boolean_pool\n_require_boolean_pool('signal = 1 + 2')",
        },
        {
            "id": "gate_pool_bool_dtype",
            "category": "spec",
            "name": "池公式求值非 Bool（dtype 判定）",
            "rule": "池公式求值结果列 dtype 必须 Bool——if_else 等数值分支表达式不是成员条件",
            "trigger": "池公式通过语法门但求值 dtype 为 Int/Float（如 (close > 15) * 1）",
            "sample": "分支表达式不可作 v1 池公式",
            "fix": "成员条件去掉算术尾巴——写成比较/布尔式（(close > 15) & (volume > 100)）",
            "tests": [
                "tests/test_pool_formula.py::test_pool_reject_dtype_not_bool",
            ],
            "probe": "from factorlab.core.engine.compute import _pool_cond_frame\nimport polars as pl\ndf = pl.DataFrame({'date': ['2024-01-02', '2024-01-02'], 'code': ['000001', '600519'], 'close': [10.0, 20.0]})\n_pool_cond_frame(df, 'signal = (close > 15) * 1')",
        },
        {
            "id": "gate_unknown_processor",
            "category": "processor",
            "name": "未知处理器",
            "rule": "process 链的处理器名必须已注册（get_processor）——错误文案带可用名清单",
            "trigger": "process 链写 no_such_processor",
            "sample": "未知处理器: ",
            "fix": "按错误文案的可用清单改正处理器名（clip/csranknorm/fillna/neutralize/robustzscore/standardize/winsorize/zscore）",
            "tests": [
                "tests/test_process.py::test_unknown_processor_rejected",
            ],
            "probe": "from factorlab.process.registry import get_processor\nget_processor('no_such_processor')",
        },
        {
            "id": "gate_chain_parse_order",
            "category": "processor",
            "name": "process 链解析顺序（关键字后位置参数）",
            "rule": "process 链项解析：关键字参数出现后不允许再接位置参数（chain item 是串行管道语法）",
            "trigger": "写 clip(a=1, 2)——关键字后跟位置参数",
            "sample": "关键字参数后不允许位置参数: ",
            "fix": "参数按位置参数在前、关键字在后排列（clip(2, a=1)）",
            "tests": [
                "tests/test_process.py::test_parse_chain_item_keyword_before_positional_rejected",
                "tests/test_process.py::test_parse_chain_item_invalid",
            ],
            "probe": "from factorlab.process.registry import parse_chain_item\nparse_chain_item('clip(a=1, 2)')",
        },
        {
            "id": "gate_unknown_column_helper",
            "category": "helper",
            "name": "未知列名（报错助手）",
            "rule": "公式列名按当前数据面实探供给；未知列名报「未知列名」并给最接近候选 + 目录指针——无字段白名单，可用列随当前数据面变化",
            "trigger": "公式写错列名（typo/混淆原始名与引擎名/字段当前数据面没有）",
            # sample 与 source.py _unknown_col_message 文案片段同源（import 引用，不抄写）
            "sample": _MSG_COLUMN_DIR_HINT,
            "fix": "按错误文案的候选提示改正；原始列名（vol/ts_code/trade_date）查映射提示行改用引擎列名",
            "tests": [
                "tests/test_input_surface.py::test_typo_col_error_suggests_closest",
                "tests/test_pool_formula.py::test_pool_reject_unknown_column_error_helper",
                "tests/test_attributes_face.py::test_unknown_attribute_like_column_helpful_error",
            ],
            "probe": "",
        },
        {
            "id": "gate_raw_name_mapping_hint",
            "category": "helper",
            "name": "原始列名映射提示（helper）",
            "rule": "平台库原始列名（vol/ts_code/trade_date）与引擎列名不同——公式写原始名报未知列名并附「平台库原始列 … 已映射为引擎列 …」提示",
            "trigger": "公式写 vol / ts_code / trade_date（tushare 原始命名习惯）",
            # sample 与 source.py _unknown_col_message 文案片段同源（import 引用，不抄写）
            "sample": _MSG_RAW_MAPPED_PREFIX,
            "fix": "改用引擎列名（vol → volume、ts_code → code、trade_date → date，hint 行列语义有逐条映射）",
            "tests": [
                "tests/test_input_surface.py::test_raw_hidden_name_gets_mapping_hint",
            ],
            "probe": "",
        },
    ]


# ---------------------------------------------------------------------------
# 已知近似（design §7 三条：语义近似以「实探/声明」进目录，不伪装成精确）
# ---------------------------------------------------------------------------
_KNOWN_APPROXIMATIONS = (
    {
        "id": "approx_industry_current_value",
        "area": "属性面 industry",
        "statement": "industry 是当前分类值而非 PIT 历史值：属性面按股票最新申万行业归属回填，历史时点分组可能与当时实际行业不同",
        "mitigation": "需要历史行业归属的研究自行维护行业变更表；读面按构造即 PIT，属性面只保证当前值",
    },
    {
        "id": "approx_unknown_param_static_pass",
        "area": "未来函数静态门",
        "statement": "未知变量（def 形参等）无法静态判断未来性：位移/窗口参数若由 def 形参绑定，静态门放行（保守方向——宁放行不漏杀）",
        "mitigation": "写因子时把位移量写成字面量或顶层常量（顶层赋值会被静态折叠检查）；def 形参只做元素级组合",
    },
    {
        "id": "approx_future_naming_prefix_only",
        "area": "未来列命名纪律",
        "statement": "未来列前缀纪律是名字类约束（无字段白名单）：读面不校验某列是否真的是未来数据，只按构造即 PIT 约定禁止未来列进读面表",
        "mitigation": "未来/标签数据落 forward_*/future_* 前缀名后由评估运行时（engine/forward.compute_forward_returns 内存计算）或研究侧数据表承载并显式命名",
    },
)

WALLS = {"syntax_efficiency", "future", "internal"}


# ---------------------------------------------------------------------------
# 组装 + 校验
# ---------------------------------------------------------------------------
def build_catalog() -> dict:
    """组装目录（活文档数据体）。registry 清单实时触发；owned ⊆ inventory 断言。"""
    open_surface = {
        "columns": _column_rows(),
        "operators": {
            "platform_owned": [dict(row) for row in _PLATFORM_OWNED],
            "registry_inventory": _live_inventory(),
            "elementwise_methods": sorted(ALLOWED_EXPR_METHODS),
            "partition_prefixes": [
                "ts_——时序分区（按 (code, 交易日) 排序的滚动/累计窗口；窗口算子只在公式顶层）",
                "cs_——横截面分区（按交易日逐日截面；同日样本构成截面）",
                "gp_——组内分区（按交易日、按第一参数 key 分组：gp_rank(key, x)/gp_mean(key, x)）",
                "ta_——polars_ta 技术指标族（注册清单 kind=ta，如 ts_MACD/ts_RSI）",
            ],
        },
        "def_composition": {
            "rules": list(_DEF_RULES),
            "example": _DEF_EXAMPLE,
        },
        "naming": _naming(),
    }
    gates = _gates()
    owned = {r["name"] for r in open_surface["operators"]["platform_owned"]}
    inventory = {r["name"] for r in open_surface["operators"]["registry_inventory"]}
    missing = owned - inventory
    if missing:
        raise RuntimeError(f"平台自有算子不在注册清单（注册链缺失?）: {sorted(missing)}")
    catalog = {
        "schema_version": SCHEMA_VERSION,
        "scope": "FactorLab 引擎读面活文档（日频数据面：行情 daily / 估值 daily_basic / 特殊 special / "
                 "属性 attributes 列 + 算子 + def 组合规范 + 命名类约定 + 关闭面三类墙 + 错误修复手册）。"
                 "数据全开放——目录是活文档不是校验门：任何真实存在的读面列/字段/新算子都自由，"
                 "无字段白名单（可用列随当前数据面实探），纪律只做名字类检查（未来前缀/内部保留名）",
        "source_ref": "docs/superpowers/specs/2026-09-06-factorlab-dsl-shape-design.md"
                      "（§5.3 关闭面三类墙、§5.4 活文档、§6 G4/G5 差距行、§7 已知近似）",
        "open_surface": open_surface,
        "closed_gates": [row for row in gates if row["category"] in WALLS],
        "error_handbook": gates,
        "known_approximations": [dict(row) for row in _KNOWN_APPROXIMATIONS],
    }
    validate_catalog(catalog)
    return catalog


def _iter_text(cat: dict):
    """遍历目录中所有字符串值，带 json 路径（叶子 str 产出）。"""
    def walk(node, path: str):
        if isinstance(node, dict):
            for k, v in node.items():
                yield from walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                yield from walk(v, f"{path}[{i}]")
        elif isinstance(node, str):
            yield path, node
    yield from walk(cat, "")


def validate_catalog(cat: dict) -> None:
    """描述完整性门：任何空字段 / 占位式（STUB_WORDS）描述都被点名拒绝。

    活文档不是"校验门"的例外不在此列——这里校验的是文档自身完整（不许省略式
    描述指向他处），不是约束用户数据面。
    """
    for path, text in _iter_text(cat):
        # probe 是触发代码（DB 无关门才有、DB 门为空）——不是描述字段，不查
        if path.endswith(".probe"):
            continue
        if not text.strip():
            raise ValueError(f"catalog.{path} 为空（目录要求事无巨细，不许空段）")
        for word in STUB_WORDS:
            if word in text:
                raise ValueError(
                    f"catalog.{path} 含省略式描述 {word!r}（目录是正文不是指针——"
                    f"description 必须事无巨细，不许指向他处省略）")
    # 结构与约束：顶层键 / 列行键 / 门行键 / closed_gates == 手册墙行过滤
    required_top = {"schema_version", "scope", "source_ref", "open_surface",
                    "closed_gates", "error_handbook", "known_approximations"}
    missing = required_top - set(cat)
    if missing:
        raise ValueError(f"catalog 缺顶层键: {sorted(missing)}")
    for i, row in enumerate(cat["open_surface"]["columns"]):
        for key in ("name", "table", "kind", "semantic", "unit", "timing"):
            if not row.get(key, "").strip():
                raise ValueError(f"catalog.open_surface.columns[{i}].{key} 为空")
    for i, row in enumerate(cat["error_handbook"]):
        for key in ("id", "category", "name", "rule", "trigger", "sample", "fix",
                    "tests", "probe"):
            if key not in row:
                raise ValueError(f"catalog.error_handbook[{i}] 缺键 {key}")
    for i, row in enumerate(cat["known_approximations"]):
        for key in ("id", "area", "statement", "mitigation"):
            if not row.get(key, "").strip():
                raise ValueError(f"catalog.known_approximations[{i}].{key} 为空")
    if cat["closed_gates"] != [r for r in cat["error_handbook"] if r["category"] in WALLS]:
        raise ValueError("catalog.closed_gates 必须 = error_handbook 中墙类别行的过滤（同源）")


# ---------------------------------------------------------------------------
# 导出：JSON（确定性）与正文（markdown）
# ---------------------------------------------------------------------------
def catalog_json(cat: dict | None = None) -> str:
    """机器可读 JSON（catalog dump）：ensure_ascii=False + sort_keys——确定性，
    两次导出逐字节一致，供写因子的 AI 开写前阅读。"""
    return json.dumps(cat if cat is not None else build_catalog(),
                      ensure_ascii=False, sort_keys=True, indent=2)


def render_catalog_markdown(cat: dict | None = None) -> str:
    """目录正文（docs/catalog.md 与之逐字节一致——活文档不陈旧）。"""
    cat = cat if cat is not None else build_catalog()
    ops = cat["open_surface"]["operators"]
    lines: list[str] = []
    ap = lines.append

    ap(f"# FactorLab 列/算子目录（活文档 v{cat['schema_version']}）\n")
    ap(f"- 范围：{cat['scope']}")
    ap(f"- 规格源：{cat['source_ref']}")
    ap("本文档由 `factorlab catalog dump`（JSON）与 `factorlab catalog docs`（本正文）"
       "同源生成——目录正文、机器可读 JSON 与运行时 schema 元数据（source 常量、"
       "engine.reserved、ast_gate、registry）共享单一事实源，防漂移。\n")

    # ---- 开放面 ----
    ap("## 一、开放面\n")
    ap("### 1. 字段（语义 / 单位 / 产生时点）\n")
    ap("| 列 | 类别 | 来源 | 语义 | 单位 | 产生时点 |")
    ap("|---|---|---|---|---|---|")
    for row in cat["open_surface"]["columns"]:
        ap(f"| `{row['name']}` | {row['kind']} | {row['table']} | {row['semantic']} | "
           f"{row['unit']} | {row['timing']} |")
    ap("\n> 列清单与读路径解析器单源一致（`source._PLATFORM_COLS`/`_DAILY_BASIC_MAP`/"
       "`_SPECIAL_COLS` 并集 = 读面供给集）；hint 行只进报错助手的映射提示，不供给公式。"
       "\n> 数据全开放：**无白名单**——任何真实存在的读面列都可用（可用列随当前数据面"
       "实探供给，收录不是前提），纪律只做名字类检查。\n")

    ap("### 2. 算子\n")
    ap("#### 平台自有算子（注册清单同源，owned ⊆ inventory）\n")
    for row in ops["platform_owned"]:
        ap(f"- `{row['name']}`（{row['kind']}）——{row['semantic']}")
        ap(f"  - 约束：{row['constraints']}")
    ap("\n#### 注册清单（`registry.list_ops()` 实时快照——别名无独立行）\n")
    for row in ops["registry_inventory"]:
        ap(f"- `{row['name']}`（{row['kind']} v{row['version']}）")
    ap("\n#### 元素级方法链白名单（与解析器单源一致——仅限这组可写 `.abs()` 形态）\n")
    ap("、".join(f"`{m}`" for m in ops["elementwise_methods"]))
    ap("\n#### 分区前缀族\n")
    for p in ops["partition_prefixes"]:
        ap(f"- {p}")
    ap("")

    ap("### 3. def 组合规范\n")
    for rule in cat["open_surface"]["def_composition"]["rules"]:
        ap(f"- {rule}")
    ap("\n```python\n" + cat["open_surface"]["def_composition"]["example"] + "\n```\n")

    ap("### 4. 命名类约定\n")
    naming = cat["open_surface"]["naming"]
    ap(f"- 未来前缀：{', '.join(f'`{p}*`' for p in naming['future_prefixes'])}；"
       f"未来精确名：{', '.join(f'`{n}`' for n in naming['future_names'])}"
       "——未来/标签类列名必须落在这组命名下（命名纪律 + 读面入库校验双锁）")
    ap(f"- 内部前缀：`{naming['internal_prefix']}*`；内部精确名："
       f"{', '.join(f'`{n}`' for n in naming['internal_names'])}——引擎运行时命名空间")
    ap("- 纪律：")
    for d in naming["disciplines"]:
        ap(f"  - {d}")
    ap("")

    # ---- 关闭面 ----
    ap("## 二、关闭面（三类墙）\n")
    ap("关闭面 = 引擎按规则拒绝的行为（不是字段黑名单——开放面外推自由，墙只拦这"
       "三类）。门表与对照测试逐条对应，报错文案样板逐字与源码一致（换文案即断链）。\n")
    for row in cat["closed_gates"]:
        _ap_gate(ap, row, intro=True)
    ap("")

    # ---- 错误修复手册 ----
    ap("## 三、错误修复手册（全量门表 + 触发示例 + 报错文案样板 + 修法）\n")
    ap("`probe` 非空的门：无需数据库即可真实触发（文案含样板）；`probe` 为空的门为"
       "数据侧/DB 路径门，其运行时一致性由 `tests` 引用的对照测试锁定。\n")
    for row in cat["error_handbook"]:
        _ap_gate(ap, row, intro=False)
    ap("")

    # ---- 已知近似 ----
    ap("## 四、已知近似\n")
    for row in cat["known_approximations"]:
        ap(f"- **{row['area']}**（{row['id']}）：{row['statement']}")
        ap(f"  - 缓解：{row['mitigation']}")
    return "\n".join(lines) + "\n"


def _ap_gate(ap, row: dict, intro: bool) -> None:
    ap(f"### {row['id']}（{row['name']}）")
    if intro:
        ap(f"- 墙：{row['category']}")
    ap(f"- 规则：{row['rule']}")
    ap(f"- 触发：{row['trigger']}")
    ap(f"- 报错文案样板：`{row['sample']}`")
    ap(f"- 修法：{row['fix']}")
    if row["probe"]:
        ap(f"- 触发探针：```python\n{row['probe']}\n```")
    if row["tests"]:
        ap(f"- 对照测试：{', '.join(f'`{t}`' for t in row['tests'])}")
    ap("")
