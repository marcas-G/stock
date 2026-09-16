# FactorLab 列/算子目录（活文档 v1）

- 范围：FactorLab 引擎读面活文档（日频数据面：行情 daily / 估值 daily_basic / 特殊 special / 属性 attributes 列 + 算子 + def 组合规范 + 命名类约定 + 关闭面三类墙 + 错误修复手册）。数据全开放——目录是活文档不是校验门：任何真实存在的读面列/字段/新算子都自由，无字段白名单（可用列随当前数据面实探），纪律只做名字类检查（未来前缀/内部保留名）
- 规格源：knowledge/design/platform/specs/2026-09-06-factorlab-dsl-shape-design.md（§5.3 关闭面三类墙、§5.4 活文档、§6 G4/G5 差距行、§7 已知近似）
本文档由 `factorlab catalog dump`（JSON）与 `factorlab catalog docs`（本正文）同源生成——目录正文、机器可读 JSON 与运行时 schema 元数据（source 常量、engine.reserved、ast_gate、registry）共享单一事实源，防漂移。

## 一、开放面

### 1. 字段（语义 / 单位 / 产生时点）

| 列 | 类别 | 来源 | 语义 | 单位 | 产生时点 |
|---|---|---|---|---|---|
| `open` | 行情 | daily | 开盘价——不复权原始价（当日第一笔成交价，不含集合竞价以外调整） | 元 | T 日开盘时点（09:30 前集合竞价撮合结果） |
| `high` | 行情 | daily | 最高价——不复权原始价（当日盘中最高成交价） | 元 | T 日盘中（15:00 前滚动更新，盘后定值） |
| `low` | 行情 | daily | 最低价——不复权原始价（当日盘中最低成交价） | 元 | T 日盘中（15:00 前滚动更新，盘后定值） |
| `close` | 行情 | daily | 收盘价——不复权原始价（当日最后一笔成交价，官方收盘口径；前复权由 qfq 基准折算，不复权是读面基准价） | 元 | T 日盘后时点（15:00 收盘定值，回看即已定） |
| `pre_close` | 行情 | daily | 前收盘价——不复权原始价（T-1 收盘；除权除息日为除权参考价，与当日真实昨收不同源） | 元 | T 日盘前（T-1 收盘定值） |
| `change` | 行情 | daily | 涨跌额——不复权原始价（close − pre_close） | 元 | T 日盘后时点 |
| `pct_chg` | 行情 | daily | 涨跌幅（%——(close/pre_close − 1)×100；除权除息日按除权参考价口径，跨除权的真实收益用前复权价自算） | % | T 日盘后时点 |
| `volume` | 行情 | daily | 成交量——股（2026-09-08 实测：daily.vol 对 bars_1m 按 (code, 交易日) 汇总比值 ≈ 1，与分钟面同单位） | 股 | T 日盘后（全天累计成交量） |
| `amount` | 行情 | daily | 成交额——元（2026-09-08 实测：daily.amount 对 bars_1m 按 (code, 交易日) 汇总比值 ≈ 1，与分钟面同单位） | 元 | T 日盘后（全天累计成交额） |
| `turnover` | 估值/交易指标 | daily_basic | 换手率（%——成交量/流通股本） | % | T 日盘后公布（含当日） |
| `total_mv` | 估值/交易指标 | daily_basic | 总市值——万元（未复权股本口径） | 万元 | T 日盘后公布（含当日） |
| `circ_mv` | 估值/交易指标 | daily_basic | 流通市值——万元（未复权股本口径） | 万元 | T 日盘后公布（含当日） |
| `pe_ttm` | 估值/交易指标 | daily_basic | 市盈率 TTM（倍——亏损股为负值，非缺失） | 倍 | T 日盘后公布（盈利取最近报告期外推 TTM） |
| `pb` | 估值/交易指标 | daily_basic | 市净率（倍——每股净资产取最近报告期） | 倍 | T 日盘后公布 |
| `dv_ratio` | 估值/交易指标 | daily_basic | 股息率（%——近 12 个月每股分红/股价） | % | T 日盘后公布 |
| `volume_ratio` | 估值/交易指标 | daily_basic | 量比（倍——当日每分钟均量/过去 5 日每分钟均量，衡量放量程度） | 倍 | T 日盘中实时（15:00 收盘定值） |
| `date` | 键 | special | 交易日键——YYYYMMDD 文本（解码后恒在；所有日频行按它对齐） | —（键列） | 恒在（T 日标识） |
| `code` | 键 | special | 股票代码键——ts_code 带后缀形态（000001.SZ；解码后恒在；与纯数字写法映射提示见 hint 行） | —（键列） | 恒在（个体标识） |
| `adj_factor` | 特殊 | special | 复权因子（后复权口径累积因子——除权除息日调整；前复权价 = 不复权价 × 因子 / 最新因子，qfq 基准见读路径折算） | 倍 | T 日盘后（与当日行情同步） |
| `idx_ret` | 特殊 | special | 指数日收益率——小数（基准指数当日 pct_chg/100；市场状态代理，默认中证 1000，cols 含 idx_ret 时 join） | 小数（0.01 = 1%） | T 日盘后（指数同日收盘） |
| `industry` | 属性 | attributes | 行业——当前分类值（申万行业最新归属，属性面按股票回填；非历史时点 PIT 值——回看样本分组按最新分类，近似见已知近似表） | —（分类文本） | 属性时点（最新快照，非逐日变更） |
| `vol` | hint | hint | 原始列名映射提示：vol → volume（平台库原始列不同名——公式写 vol 报「未知列名」并给此映射） | —（提示行） | —（提示行） |
| `ts_code` | hint | hint | 原始列名映射提示：ts_code → code（平台库原始列不同名——公式写 ts_code 报「未知列名」并给此映射） | —（提示行） | —（提示行） |
| `trade_date` | hint | hint | 原始列名映射提示：trade_date → date（平台库原始列不同名——公式写 trade_date 报「未知列名」并给此映射） | —（提示行） | —（提示行） |

> 列清单与读路径解析器单源一致（`source._PLATFORM_COLS`/`_DAILY_BASIC_MAP`/`_SPECIAL_COLS` 并集 = 读面供给集）；hint 行只进报错助手的映射提示，不供给公式。
> 数据全开放：**无白名单**——任何真实存在的读面列都可用（可用列随当前数据面实探供给，收录不是前提），纪律只做名字类检查。

### 2. 算子

#### 平台自有算子（注册清单同源，owned ⊆ inventory）

- `returns`（ts）——单期收益率：close / pre_close − 1（当日对前收盘的简单收益；不复权口径，跨除权日失真见 close 语义）
  - 约束：无窗口/lookback 参数；注册算子，def 体内联被拒（见 gate_def_inner_window_op）
- `vwap`（ts）——成交量加权均价（累计式——自样本起点累计成交额/累计成交量）
  - 约束：累计式：起点敏感，非固定窗；注册算子，def 体内联被拒
- `adv20`（ts）——20 日均成交额/量（流动性与成交活跃度代理）
  - 约束：固定 20 日滚动窗（与 ts_mean 同窗语义）；注册算子，def 体内联被拒
- `gp_rank`（gp）——组内排名：gp_rank(key, x)——x 按 key 当日组内 rank（1..K，平均秩；null 不占名次）
  - 约束：参数顺序 (key, x)：第一参数为分组键（按交易日分组，键不参与掩码）；组缺失时组内余下成员互相竞争（独木 = 1.0）
- `gp_mean`（gp）——组内均值：gp_mean(key, x)——x 按 key 当日组内均值（null 不参与）
  - 约束：参数顺序 (key, x)：第一参数为分组键（按交易日分组）；组缺失时组内独木即其自身值
- `cs_stable_rank`（cs）——横截面 stable dense rank（并列同秩）——pct 输出 = 名次/(当日截面非 null 样本数 − 1)，K=1 → 0.0
  - 约束：逐日截面无跨日记忆；掩码外/缺失样本不占名次；cs_rank 是它的公式层别名（alias 可写，canonical 名在注册清单）

#### 注册清单（`registry.list_ops()` 实时快照——别名无独立行）

- `adv20`（ts v0.1.0）
- `cs_demean`（cs v0.1.0）
- `cs_mad_zscore`（cs v0.1.0）
- `cs_quantile`（cs v0.1.0）
- `cs_resid`（cs v0.1.0）
- `cs_scale`（cs v0.1.0）
- `cs_stable_rank`（cs v0.2.0）
- `cs_zscore`（cs v0.1.0）
- `day_first`（day v0.1.0）
- `day_last`（day v0.1.0）
- `day_max`（day v0.1.0）
- `day_mean`（day v0.1.0）
- `day_min`（day v0.1.0）
- `day_sum`（day v0.1.0）
- `gp_mean`（gp v0.2.0）
- `gp_rank`（gp v0.2.0）
- `im_delay`（im v0.1.0）
- `im_max`（im v0.1.0）
- `im_mean`（im v0.1.0）
- `im_median`（im v0.1.0）
- `im_min`（im v0.1.0）
- `im_std`（im v0.1.0）
- `im_sum`（im v0.1.0）
- `returns`（ts v0.1.0）
- `ts_ATR`（ta v0.1.0）
- `ts_BIAS`（ta v0.1.0）
- `ts_BOLL`（ta v0.1.0）
- `ts_CCI`（ta v0.1.0）
- `ts_KDJ`（ta v0.1.0）
- `ts_MACD`（ta v0.1.0）
- `ts_RSI`（ta v0.1.0）
- `ts_RSV`（ta v0.1.0）
- `ts_TRIX`（ta v0.1.0）
- `ts_WILLR`（ta v0.1.0）
- `ts_corr`（ts v0.1.0）
- `ts_count`（ts v0.1.0）
- `ts_covariance`（ts v0.1.0）
- `ts_cum_max`（ts v0.1.0）
- `ts_cum_min`（ts v0.1.0）
- `ts_cum_prod`（ts v0.1.0）
- `ts_cum_sum`（ts v0.1.0）
- `ts_delay`（ts v0.1.0）
- `ts_delta`（ts v0.1.0）
- `ts_kurtosis`（ts v0.1.0）
- `ts_max`（ts v0.1.0）
- `ts_mean`（ts v0.1.0）
- `ts_median`（ts v0.1.0）
- `ts_min`（ts v0.1.0）
- `ts_product`（ts v0.1.0）
- `ts_rank`（ts v0.1.0）
- `ts_skewness`（ts v0.1.0）
- `ts_std_dev`（ts v0.1.0）
- `ts_sum`（ts v0.1.0）
- `ts_zscore`（ts v0.1.0）
- `vwap`（ts v0.1.0）

#### 元素级方法链白名单（与解析器单源一致——仅限这组可写 `.abs()` 形态）

`abs`、`exp`、`floor`、`log`、`log1p`、`sign`、`sqrt`

#### 分区前缀族

- ts_——时序分区（按 (code, 交易日) 排序的滚动/累计窗口；窗口算子只在公式顶层）
- cs_——横截面分区（按交易日逐日截面；同日样本构成截面）
- gp_——组内分区（按交易日、按第一参数 key 分组：gp_rank(key, x)/gp_mean(key, x)）
- ta_——polars_ta 技术指标族（注册清单 kind=ta，如 ts_MACD/ts_RSI）

### 3. def 组合规范

- def 用于给一段可复用表达式命名参数化组合；体内只能写赋值与 return（中间变量用 _ 前缀，末尾 return 恰一条）
- 注册算子（窗口 ts_/截面 cs_/组 gp_/平台自有）不能写在 def 体内——def 按元素级黑盒执行，体内窗口会在全表上滚动、跨资产泄漏；把窗口语义写在调用处的公式顶层
- def 形参只允许位置参数（可带缺省值）；*args/**kwargs/kwonly 形参拒绝、调用带关键字参数拒绝（内联展开按位置形参表绑定）
- def 不能递归（直接或间接）——内联需要有限展开深度；def 名不能以 _ 开头（_ 是中间变量约定）
- 同一 def 多次调用各自实例化（中间变量不串、参数不串）；def 调 def 支持且不依赖源码定义顺序
- 顶层赋值常量（_d = -3 这类）会被负位移静态检查折叠——位移量请写成字面量或顶层常量，便于未来函数门检查（def 形参是未知变量，静态放行，见已知近似）

```python
# 元素级组合 + 缺省参数（def 内不得出现窗口/截面算子）
def scale_shift(x, n=2):
    _m = x * n
    return x / _m

# 窗口语义写在公式顶层（不在 def 体内）
signal = ts_mean(scale_shift(close), 20) - ts_delay(close, 5)
# 若某窗口结果要在 def 内复用：把窗口调用结果作为实参传入——
# windowed = ts_rank(close, 20); signal = scale_shift(windowed)  
```

### 4. 命名类约定

- 未来前缀：`forward_*`, `future_*`；未来精确名：`label`, `target`——未来/标签类列名必须落在这组命名下（命名纪律 + 读面入库校验双锁）
- 内部前缀：`__factorlab_*`；内部精确名：`in_universe`——引擎运行时命名空间
- 纪律：
  - 未来类列名（内容含未来信息的列、标签/目标列）必须落 forward_*/future_* 前缀或 target/label 精确名——这是平台命名类约定；读面表按构造即 PIT，未来列禁止进读面（数据入库校验 validate_engine_surface 与活文档双重锁）
  - 内部保留名（__factorlab_* 前缀与 in_universe 精确名）是引擎运行时命名空间：模板公式不可见、不可读写、不可绑定、不由用户数据占用
  - 数据全开放：任何真实存在的读面列/新算子/新字段都自由，无字段白名单——命名纪律只拦名字类（未来信息必须用未来前缀声明、内部名不占用），不设内容检查

## 二、关闭面（三类墙）

关闭面 = 引擎按规则拒绝的行为（不是字段黑名单——开放面外推自由，墙只拦这三类）。门表与对照测试逐条对应，报错文案样板逐字与源码一致（换文案即断链）。

### gate_unknown_operator（未知算子（未注册名/typo））
- 墙：syntax_efficiency
- 规则：公式中的算子调用名必须已注册（registry.has_op）或是本公式 def / 元素级函数名；未知名直接拒绝
- 触发：生成/手写公式引用不存在的算子名（拼写、大小写、未触发注册的 polars_ta 名、误以为 def 名可见）
- 报错文案样板：`未知算子: `
- 修法：对照本目录「注册清单」（catalog dump / factorlab op list）改正名字：平台算子名全小写下划线式（ts_delay 而非 tsDelay），新算子走 op 插件注册
- 触发探针：```python
from factorlab.core.engine.partitions import validate_partition_calls
validate_partition_calls('signal = no_such_op(close, 3)')
```
- 对照测试：`tests/test_partitions.py::test_rejects_unknown_operator`, `tests/test_compute.py::test_compute_rejects_unknown_operator`

### gate_def_inner_window_op（def 体内窗口/截面算子）
- 墙：syntax_efficiency
- 规则：注册算子（窗口 ts_/截面 cs_/组 gp_/平台自有）禁止写在 def 体内——def 按元素级黑盒执行，体内窗口会跨资产滚动泄漏
- 触发：def 体内出现 ts_mean/ts_rank 等注册算子（含 import 别名形态）
- 报错文案样板：`不能在 def 内使用，请直接写在公式顶层`
- 修法：把窗口语义提到公式顶层（def 只做元素级/参数化组合）；或把窗口结果作为实参传入 def
- 触发探针：```python
from factorlab.core.ops.polars_ta_wrappers import register_polars_ta_ops
register_polars_ta_ops()
from factorlab.core.engine.partitions import validate_partition_calls
validate_partition_calls('def f(x):\n    return ts_mean(x, 5)\nsignal = f(close)')
```
- 对照测试：`tests/test_partitions.py::test_rejects_window_op_inside_def`, `tests/test_partitions.py::test_import_alias_of_window_op_inside_def_rejected`

### gate_def_varargs（def vararg/kwonly 形参）
- 墙：syntax_efficiency
- 规则：def 形参只允许位置参数（可带缺省值）；*args/**kwargs/kwonly 形参一律拒绝
- 触发：def f(x, *args)/f(x, **kwargs)/f(x, *, n)——vararg 数量任意、kwonly 与位置实参错位，内联展开无法绑定
- 报错文案样板：`不支持 *args/**kwargs 参数`
- 修法：形参全部改写为确定的位置参数表（可带缺省值）——内联展开需要确定形参绑定
- 触发探针：```python
from factorlab.core.ops.platform_ops import inline_defs
inline_defs('def f(x, *args):\n    return x + args\n\nsignal = f(close, 1)')
```
- 对照测试：`tests/test_inline_defs.py::test_inline_def_varargs_kwonly_rejected`

### gate_def_keyword_call（def 调用带关键字参数）
- 墙：syntax_efficiency
- 规则：def 调用一律按位置传参——关键字实参在展开后与绑定形参表错位
- 触发：def 调用写 f(x=close, n=20)
- 报错文案样板：`调用不支持关键字参数`
- 修法：def 调用按形参定义顺序给位置实参
- 触发探针：```python
from factorlab.core.ops.platform_ops import inline_defs
inline_defs('def f(x, n):\n    return x + n\n\nsignal = f(close, n=1)')
```
- 对照测试：`tests/test_inline_defs.py::test_inline_def_keyword_args_rejected`

### gate_def_recursion（递归 def）
- 墙：syntax_efficiency
- 规则：def 不能递归（直接或间接）——内联展开需要有限深度
- 触发：def 体内自调用，或 a→b→a 的间接递归环
- 报错文案样板：`递归 def 不支持内联: `
- 修法：改写为非递归结构（把递归体展开为有限层或改为顶层窗口组合）
- 触发探针：```python
from factorlab.core.ops.platform_ops import inline_defs
inline_defs('def f(x):\n    return f(x)\n\nsignal = f(close)')
```
- 对照测试：`tests/test_inline_defs.py::test_inline_recursive_def_rejected`, `tests/test_inline_defs.py::test_inline_indirect_recursion_rejected`

### gate_negative_shift（负位移（未来函数））
- 墙：future
- 规则：ts_delay/ts_delta 的位移参数（第 2 位置参数或关键字 d）必须 ≥ 0——负位移读取未来，公式按构造即 PIT
- 触发：负字面量 -2、可折叠表达式（1−2）、顶层常量间接（_d = -3 后 ts_delay(x, _d)）、import 别名（td(x, -2)）
- 报错文案样板：`不允许负位移（lookback 只能取过去）`
- 修法：位移参数改正/0；需要未来窗口收益的语义由评估运行时 forward 计算，不由公式读未来
- 触发探针：```python
from factorlab.core.engine.partitions import reject_future_shifts
reject_future_shifts('signal = ts_delay(close, -2)')
```
- 对照测试：`tests/test_partitions.py::test_rejects_negative_delay`, `tests/test_partitions.py::test_rejects_negative_delay_via_top_level_const`, `tests/test_partitions.py::test_rejects_negative_delay_via_alias_import`

### gate_future_column_input（公式引用未来/标签列）
- 墙：future
- 规则：公式输入不能引用未来列（forward_*/future_*/target/label）——未来数据只由评估运行时在内存计算，读面不供给未来列
- 触发：公式/池公式引用 forward_return_5d 这类列（读面没有——compute 收口拦截）
- 报错文案样板：`future/label inputs are forbidden in factor formula: `
- 修法：收益/未来窗口写成公式内对过去的引用（收益对齐与标签由评估框架 forward 计算，见已知近似）
- 触发探针：```python
from factorlab.core.engine.compute import _check_future_inputs
_check_future_inputs('x = close + forward_return_5d')
```
- 对照测试：`tests/test_signal_label_runtime.py::test_formula_future_input_guard`, `tests/test_pool_formula.py::test_pool_reject_future_input`

### gate_future_surface_discipline（读面表未来列（数据侧入库校验））
- 墙：future
- 规则：引擎读面表（ENGINE_SURFACE_TABLES 9 表：daily/daily_basic/adj_factor/index_daily/stock_basic/trade_cal/stock_st/stk_limit/suspend_d）禁止出现未来前缀列——入库校验 validate_engine_surface/validate_surface_columns 与活文档双重锁
- 触发：重建/入库（build_final_db 收口）时读面表含 forward_*/future_* 前缀或 target/label 精确名列
- 报错文案样板：`未来/标签命名（forward_*/future_* 前缀与 target/label 精确名）——读面按构造即 PIT，未来数据只允许由评估运行时在内存计算或研究侧落库，命名纪律见设计 §5.3-2 与目录命名类约定`
- 修法：请重命名或移出读面（未来/标签数据由运行时 forward 计算或研究侧落库）
- 对照测试：`tests/test_column_discipline.py::test_pure_fn_future_prefixed_columns_rejected`, `tests/test_column_discipline.py::test_build_final_db_rejects_violating_surface`

### gate_surface_internal_column（读面表内部保留列（数据侧））
- 墙：internal
- 规则：引擎读面表禁止内部保留名列（__factorlab_* 前缀 / in_universe 精确名）——读面列注入/join 与引擎内部列碰撞毒化面板
- 触发：重建/入库（build_final_db 收口）或读面探测（validate_engine_surface）发现读面表含内部名列
- 报错文案样板：`引擎内部保留名（__factorlab_* 前缀与 in_universe）——读面列注入/join 会与引擎内部列碰撞毒化面板，属设计 §5.3-3 内部墙的数据侧`
- 修法：请重命名或移出读面（内部名是引擎运行时命名空间，不由用户数据占用）
- 对照测试：`tests/test_column_discipline.py::test_pure_fn_internal_name_rejected`, `tests/test_column_discipline.py::test_engine_surface_reports_violations_via_rd`

### gate_reserved_binding（绑定/写入保留名）
- 墙：internal
- 规则：内部保留名（__factorlab_* / in_universe）不可被用户公式赋值、作参数、作 import 别名
- 触发：公式写 in_universe = 1 / def 参数名撞保留名 / import 别名撞保留名
- 报错文案样板：`（平台内部保留：`
- 修法：改名——内部名是引擎运行时命名空间（__factorlab_* 前缀 / in_universe），不由模板占用
- 触发探针：```python
from factorlab.core.ops.universe_masking import validate_reserved_bindings
validate_reserved_bindings('in_universe = 1')
```
- 对照测试：`tests/test_universe_masking_hardening.py::test_reserved_assignment_fails`, `tests/test_input_surface.py::test_in_universe_binding_rejected`

### gate_reserved_read（读取内部列/标记）
- 墙：internal
- 规则：模板公式不可读取引擎内部列/标记（in_universe/__factorlab_*）——模板可见面只有用户数据列
- 触发：公式引用 in_universe（成员状态由运行时维护，模板读不到）
- 报错文案样板：`（引擎内部列/标记，模板公式不可见）`
- 修法：成员条件由池公式声明；模板只写数据列，不写运行时内部状态
- 触发探针：```python
from factorlab.core.engine.reserved import validate_internal_reads
validate_internal_reads('x = close + in_universe')
```
- 对照测试：`tests/test_input_surface.py::test_run_factor_rejects_reading_in_universe`, `tests/test_pool_formula.py::test_pool_reject_reserved_read`


## 三、错误修复手册（全量门表 + 触发示例 + 报错文案样板 + 修法）

`probe` 非空的门：无需数据库即可真实触发（文案含样板）；`probe` 为空的门为数据侧/DB 路径门，其运行时一致性由 `tests` 引用的对照测试锁定。

### gate_unknown_operator（未知算子（未注册名/typo））
- 规则：公式中的算子调用名必须已注册（registry.has_op）或是本公式 def / 元素级函数名；未知名直接拒绝
- 触发：生成/手写公式引用不存在的算子名（拼写、大小写、未触发注册的 polars_ta 名、误以为 def 名可见）
- 报错文案样板：`未知算子: `
- 修法：对照本目录「注册清单」（catalog dump / factorlab op list）改正名字：平台算子名全小写下划线式（ts_delay 而非 tsDelay），新算子走 op 插件注册
- 触发探针：```python
from factorlab.core.engine.partitions import validate_partition_calls
validate_partition_calls('signal = no_such_op(close, 3)')
```
- 对照测试：`tests/test_partitions.py::test_rejects_unknown_operator`, `tests/test_compute.py::test_compute_rejects_unknown_operator`

### gate_def_inner_window_op（def 体内窗口/截面算子）
- 规则：注册算子（窗口 ts_/截面 cs_/组 gp_/平台自有）禁止写在 def 体内——def 按元素级黑盒执行，体内窗口会跨资产滚动泄漏
- 触发：def 体内出现 ts_mean/ts_rank 等注册算子（含 import 别名形态）
- 报错文案样板：`不能在 def 内使用，请直接写在公式顶层`
- 修法：把窗口语义提到公式顶层（def 只做元素级/参数化组合）；或把窗口结果作为实参传入 def
- 触发探针：```python
from factorlab.core.ops.polars_ta_wrappers import register_polars_ta_ops
register_polars_ta_ops()
from factorlab.core.engine.partitions import validate_partition_calls
validate_partition_calls('def f(x):\n    return ts_mean(x, 5)\nsignal = f(close)')
```
- 对照测试：`tests/test_partitions.py::test_rejects_window_op_inside_def`, `tests/test_partitions.py::test_import_alias_of_window_op_inside_def_rejected`

### gate_def_varargs（def vararg/kwonly 形参）
- 规则：def 形参只允许位置参数（可带缺省值）；*args/**kwargs/kwonly 形参一律拒绝
- 触发：def f(x, *args)/f(x, **kwargs)/f(x, *, n)——vararg 数量任意、kwonly 与位置实参错位，内联展开无法绑定
- 报错文案样板：`不支持 *args/**kwargs 参数`
- 修法：形参全部改写为确定的位置参数表（可带缺省值）——内联展开需要确定形参绑定
- 触发探针：```python
from factorlab.core.ops.platform_ops import inline_defs
inline_defs('def f(x, *args):\n    return x + args\n\nsignal = f(close, 1)')
```
- 对照测试：`tests/test_inline_defs.py::test_inline_def_varargs_kwonly_rejected`

### gate_def_keyword_call（def 调用带关键字参数）
- 规则：def 调用一律按位置传参——关键字实参在展开后与绑定形参表错位
- 触发：def 调用写 f(x=close, n=20)
- 报错文案样板：`调用不支持关键字参数`
- 修法：def 调用按形参定义顺序给位置实参
- 触发探针：```python
from factorlab.core.ops.platform_ops import inline_defs
inline_defs('def f(x, n):\n    return x + n\n\nsignal = f(close, n=1)')
```
- 对照测试：`tests/test_inline_defs.py::test_inline_def_keyword_args_rejected`

### gate_def_recursion（递归 def）
- 规则：def 不能递归（直接或间接）——内联展开需要有限深度
- 触发：def 体内自调用，或 a→b→a 的间接递归环
- 报错文案样板：`递归 def 不支持内联: `
- 修法：改写为非递归结构（把递归体展开为有限层或改为顶层窗口组合）
- 触发探针：```python
from factorlab.core.ops.platform_ops import inline_defs
inline_defs('def f(x):\n    return f(x)\n\nsignal = f(close)')
```
- 对照测试：`tests/test_inline_defs.py::test_inline_recursive_def_rejected`, `tests/test_inline_defs.py::test_inline_indirect_recursion_rejected`

### gate_negative_shift（负位移（未来函数））
- 规则：ts_delay/ts_delta 的位移参数（第 2 位置参数或关键字 d）必须 ≥ 0——负位移读取未来，公式按构造即 PIT
- 触发：负字面量 -2、可折叠表达式（1−2）、顶层常量间接（_d = -3 后 ts_delay(x, _d)）、import 别名（td(x, -2)）
- 报错文案样板：`不允许负位移（lookback 只能取过去）`
- 修法：位移参数改正/0；需要未来窗口收益的语义由评估运行时 forward 计算，不由公式读未来
- 触发探针：```python
from factorlab.core.engine.partitions import reject_future_shifts
reject_future_shifts('signal = ts_delay(close, -2)')
```
- 对照测试：`tests/test_partitions.py::test_rejects_negative_delay`, `tests/test_partitions.py::test_rejects_negative_delay_via_top_level_const`, `tests/test_partitions.py::test_rejects_negative_delay_via_alias_import`

### gate_future_column_input（公式引用未来/标签列）
- 规则：公式输入不能引用未来列（forward_*/future_*/target/label）——未来数据只由评估运行时在内存计算，读面不供给未来列
- 触发：公式/池公式引用 forward_return_5d 这类列（读面没有——compute 收口拦截）
- 报错文案样板：`future/label inputs are forbidden in factor formula: `
- 修法：收益/未来窗口写成公式内对过去的引用（收益对齐与标签由评估框架 forward 计算，见已知近似）
- 触发探针：```python
from factorlab.core.engine.compute import _check_future_inputs
_check_future_inputs('x = close + forward_return_5d')
```
- 对照测试：`tests/test_signal_label_runtime.py::test_formula_future_input_guard`, `tests/test_pool_formula.py::test_pool_reject_future_input`

### gate_future_surface_discipline（读面表未来列（数据侧入库校验））
- 规则：引擎读面表（ENGINE_SURFACE_TABLES 9 表：daily/daily_basic/adj_factor/index_daily/stock_basic/trade_cal/stock_st/stk_limit/suspend_d）禁止出现未来前缀列——入库校验 validate_engine_surface/validate_surface_columns 与活文档双重锁
- 触发：重建/入库（build_final_db 收口）时读面表含 forward_*/future_* 前缀或 target/label 精确名列
- 报错文案样板：`未来/标签命名（forward_*/future_* 前缀与 target/label 精确名）——读面按构造即 PIT，未来数据只允许由评估运行时在内存计算或研究侧落库，命名纪律见设计 §5.3-2 与目录命名类约定`
- 修法：请重命名或移出读面（未来/标签数据由运行时 forward 计算或研究侧落库）
- 对照测试：`tests/test_column_discipline.py::test_pure_fn_future_prefixed_columns_rejected`, `tests/test_column_discipline.py::test_build_final_db_rejects_violating_surface`

### gate_surface_internal_column（读面表内部保留列（数据侧））
- 规则：引擎读面表禁止内部保留名列（__factorlab_* 前缀 / in_universe 精确名）——读面列注入/join 与引擎内部列碰撞毒化面板
- 触发：重建/入库（build_final_db 收口）或读面探测（validate_engine_surface）发现读面表含内部名列
- 报错文案样板：`引擎内部保留名（__factorlab_* 前缀与 in_universe）——读面列注入/join 会与引擎内部列碰撞毒化面板，属设计 §5.3-3 内部墙的数据侧`
- 修法：请重命名或移出读面（内部名是引擎运行时命名空间，不由用户数据占用）
- 对照测试：`tests/test_column_discipline.py::test_pure_fn_internal_name_rejected`, `tests/test_column_discipline.py::test_engine_surface_reports_violations_via_rd`

### gate_reserved_binding（绑定/写入保留名）
- 规则：内部保留名（__factorlab_* / in_universe）不可被用户公式赋值、作参数、作 import 别名
- 触发：公式写 in_universe = 1 / def 参数名撞保留名 / import 别名撞保留名
- 报错文案样板：`（平台内部保留：`
- 修法：改名——内部名是引擎运行时命名空间（__factorlab_* 前缀 / in_universe），不由模板占用
- 触发探针：```python
from factorlab.core.ops.universe_masking import validate_reserved_bindings
validate_reserved_bindings('in_universe = 1')
```
- 对照测试：`tests/test_universe_masking_hardening.py::test_reserved_assignment_fails`, `tests/test_input_surface.py::test_in_universe_binding_rejected`

### gate_reserved_read（读取内部列/标记）
- 规则：模板公式不可读取引擎内部列/标记（in_universe/__factorlab_*）——模板可见面只有用户数据列
- 触发：公式引用 in_universe（成员状态由运行时维护，模板读不到）
- 报错文案样板：`（引擎内部列/标记，模板公式不可见）`
- 修法：成员条件由池公式声明；模板只写数据列，不写运行时内部状态
- 触发探针：```python
from factorlab.core.engine.reserved import validate_internal_reads
validate_internal_reads('x = close + in_universe')
```
- 对照测试：`tests/test_input_surface.py::test_run_factor_rejects_reading_in_universe`, `tests/test_pool_formula.py::test_pool_reject_reserved_read`

### gate_spec_universe_exclusive（universe 成员条件源互斥）
- 规则：universe 的 ref / codes / rules / formula 四选一：恰一个成员条件源（空对象同样拒绝）
- 触发：spec 同时给 codes 与 rules / codes 与 formula / 空 universe 对象
- 报错文案样板：`universe 必须且只能提供 ref / codes / rules / formula 之一`
- 修法：成员条件只保留一种形态（静态 codes、规则公式 rules 或 formula 化成员条件，按 M4 文法）
- 触发探针：```python
import factorlab.core.spec as spec
spec.UniverseSpec.model_validate({})
```
- 对照测试：`tests/test_spec.py::test_rejects_universe_both_codes_and_rules`, `tests/test_pool_formula.py::test_universe_formula_mutually_exclusive_with_others`

### gate_spec_outputs_reserved（outputs 用未来保留名）
- 规则：FactorSpec.outputs 不得使用未来保留名（forward_*/future_* 前缀 / target/label 精确名）——为数据侧未来列命名纪律，公式输出不可用
- 触发：spec.outputs 写 forward_return_5d / target 等
- 报错文案样板：`为数据侧未来列命名纪律，公式输出不可用）`
- 修法：输出名避开未来前缀（forward_*/future_*/target/label 由评估运行时 forward 计算占用）
- 触发探针：```python
import factorlab.core.spec as spec
spec.FactorSpec.model_validate({'name': 'f', 'category': 'custom', 'direction': 1, 'universe': {'codes': ['000001']}, 'formula': 'x = close', 'outputs': ['forward_return_5d']})
```
- 对照测试：`tests/test_outputs_multi.py::test_outputs_reserved_names_rejected`, `tests/test_outputs_multi.py::test_outputs_structural_collision_rejected`

### gate_spec_outputs_duplicate（outputs 重复）
- 规则：FactorSpec.outputs 全局唯一（同 spec 内重复拒绝）
- 触发：outputs: ['a', 'a']——第 1 与第 2 位置冲突
- 报错文案样板：`outputs 重复: `
- 修法：去掉重复输出名（同一表达式要双份请起不同名）
- 触发探针：```python
import factorlab.core.spec as spec
spec.FactorSpec.model_validate({'name': 'f', 'category': 'custom', 'direction': 1, 'universe': {'codes': ['000001']}, 'formula': 'x = close', 'outputs': ['a', 'a']})
```
- 对照测试：`tests/test_outputs_multi.py::test_outputs_duplicate_rejected`, `tests/test_outputs_multi.py::test_outputs_empty_rejected`

### gate_pool_v1_grammar（池公式 v1 文法（单条布尔））
- 规则：池公式 v1 语法只接受单个布尔表达式（裸表达式或单条赋值）——def/多语句不在 v1 文法
- 触发：池公式写 def f(): … / 多条语句 / 多条赋值
- 报错文案样板：`池公式 v1 语法只接受单个布尔表达式（裸表达式或单条赋值）`
- 修法：成员条件写成一条布尔表达式（裸表达式或单条赋值——def 内联后仍是单条可接受）
- 触发探针：```python
from factorlab.core.engine.compute import _normalize_pool_formula
_normalize_pool_formula('def f():\n    pass\nx = 1')
```
- 对照测试：`tests/test_pool_formula.py::test_pool_reject_multi_statement`

### gate_pool_boolean_required（池公式非布尔（语法判定））
- 规则：池公式必须布尔可判定——表达式须含比较（>/</==/!=）；多条件用嵌套 if_else（and/or/& 不可用），逐 (code, 交易日) 得真/假
- 触发：池公式写纯数值/窗口表达式（signal = 1 + 2）作成员条件
- 报错文案样板：`池公式必须布尔可判定（表达式含比较 `>`/`<`/`==`/`!=`；多条件用嵌套`
- 修法：成员条件补比较；多条件用嵌套 if_else（如 if_else(close > 20, volume > 100, False)）
- 触发探针：```python
from factorlab.core.engine.compute import _require_boolean_pool
_require_boolean_pool('signal = 1 + 2')
```
- 对照测试：`tests/test_pool_formula.py::test_pool_reject_non_boolean_expression`

### gate_pool_bool_dtype（池公式求值非 Bool（dtype 判定））
- 规则：池公式求值结果列 dtype 必须 Bool——if_else 等数值分支表达式不是成员条件
- 触发：池公式通过语法门但求值 dtype 为 Int/Float（如 (close > 15) * 1）
- 报错文案样板：`分支表达式不可作 v1 池公式`
- 修法：成员条件去掉算术尾巴——写成比较式（如 if_else(close > 15, volume > 100, False)）
- 触发探针：```python
from factorlab.core.engine.compute import _pool_cond_frame
import polars as pl
df = pl.DataFrame({'date': ['2024-01-02', '2024-01-02'], 'code': ['000001', '600519'], 'close': [10.0, 20.0]})
_pool_cond_frame(df, 'signal = (close > 15) * 1')
```
- 对照测试：`tests/test_pool_formula.py::test_pool_reject_dtype_not_bool`

### gate_unknown_processor（未知处理器）
- 规则：process 链的处理器名必须已注册（get_processor）——错误文案带可用名清单
- 触发：process 链写 no_such_processor
- 报错文案样板：`未知处理器: `
- 修法：按错误文案的可用清单改正处理器名（clip/csranknorm/fillna/neutralize/robustzscore/standardize/winsorize/zscore）
- 触发探针：```python
from factorlab.core.process.registry import get_processor
get_processor('no_such_processor')
```
- 对照测试：`tests/test_process.py::test_unknown_processor_rejected`

### gate_chain_parse_order（process 链解析顺序（关键字后位置参数））
- 规则：process 链项解析：关键字参数出现后不允许再接位置参数（chain item 是串行管道语法）
- 触发：写 clip(a=1, 2)——关键字后跟位置参数
- 报错文案样板：`关键字参数后不允许位置参数: `
- 修法：参数按位置参数在前、关键字在后排列（clip(2, a=1)）
- 触发探针：```python
from factorlab.core.process.registry import parse_chain_item
parse_chain_item('clip(a=1, 2)')
```
- 对照测试：`tests/test_process.py::test_parse_chain_item_keyword_before_positional_rejected`, `tests/test_process.py::test_parse_chain_item_invalid`

### gate_unknown_column_helper（未知列名（报错助手））
- 规则：公式列名按当前数据面实探供给；未知列名报「未知列名」并给最接近候选 + 目录指针——无字段白名单，可用列随当前数据面变化
- 触发：公式写错列名（typo/混淆原始名与引擎名/字段当前数据面没有）
- 报错文案样板：`；列/算子目录见 knowledge/contracts/interface.md（无字段白名单——可用列随当前数据面变化）`
- 修法：按错误文案的候选提示改正；原始列名（vol/ts_code/trade_date）查映射提示行改用引擎列名
- 对照测试：`tests/test_input_surface.py::test_typo_col_error_suggests_closest`, `tests/test_pool_formula.py::test_pool_reject_unknown_column_error_helper`, `tests/test_attributes_face.py::test_unknown_attribute_like_column_helpful_error`

### gate_raw_name_mapping_hint（原始列名映射提示（helper））
- 规则：平台库原始列名（vol/ts_code/trade_date）与引擎列名不同——公式写原始名报未知列名并附「平台库原始列 … 已映射为引擎列 …」提示
- 触发：公式写 vol / ts_code / trade_date（tushare 原始命名习惯）
- 报错文案样板：`；平台库原始列 `
- 修法：改用引擎列名（vol → volume、ts_code → code、trade_date → date，hint 行列语义有逐条映射）
- 对照测试：`tests/test_input_surface.py::test_raw_hidden_name_gets_mapping_hint`


## 四、已知近似

- **属性面 industry**（approx_industry_current_value）：industry 是当前分类值而非 PIT 历史值：属性面按股票最新申万行业归属回填，历史时点分组可能与当时实际行业不同
  - 缓解：需要历史行业归属的研究自行维护行业变更表；读面按构造即 PIT，属性面只保证当前值
- **未来函数静态门**（approx_unknown_param_static_pass）：未知变量（def 形参等）无法静态判断未来性：位移/窗口参数若由 def 形参绑定，静态门放行（保守方向——宁放行不漏杀）
  - 缓解：写因子时把位移量写成字面量或顶层常量（顶层赋值会被静态折叠检查）；def 形参只做元素级组合
- **未来列命名纪律**（approx_future_naming_prefix_only）：未来列前缀纪律是名字类约束（无字段白名单）：读面不校验某列是否真的是未来数据，只按构造即 PIT 约定禁止未来列进读面表
  - 缓解：未来/标签数据落 forward_*/future_* 前缀名后由评估运行时（engine/forward.compute_forward_returns 内存计算）或研究侧数据表承载并显式命名
