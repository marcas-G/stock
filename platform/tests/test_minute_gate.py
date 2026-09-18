"""分钟 scope 静态门（engine/minute_gate.py）——映射 spec B2.4/B3.2/B3.3/B3.4 +
实现期修订（折日常数 fold 判定替代字面"根 day_* 包裹"；日频 scope 拒分钟算子）。

门在 compute_formula 变换链后（宏/def 展开完成）执行：
- B3.2 禁 ts_/ta_/cs_/gp_ 前缀调用（宏残余一并命中）
- outputs 必须折日常数表达式（day_*/注入列/常量/算术组合；im_* 直出或分钟序列
  组合 → 拒）
- B3.4 im_delay 位移 k<=0 拒；im_* 窗口参数 <1 拒（字面量/顶层常量间接）
- 日频 scope（scope 缺省 daily）含 im_*/day_* 调用 → 拒（分钟算子只在 bars_1m）
"""
import datetime as dt

import polars as pl
import pytest

from factorlab.core.engine.compute import compute_formula

_D = dt.date(2026, 8, 20)


def _min_df(**over):
    base = {"date": [_D], "code": ["000001"], "minute_index": [0],
            "close": [10.0], "volume": [1.0], "eod_close": [10.0]}
    base.update(over)
    return pl.DataFrame(base)


def _run(formula: str, *, scope: str = "bars_1m", outputs: list[str] | None = None,
         df=None):
    return compute_formula(df if df is not None else _min_df(), formula,
                           outputs=outputs, scope=scope)


def test_minute_scope_rejects_ts_cs_gp_ta_calls():
    for bad in ("signal = day_last(ts_mean(close, 5))",
                "signal = day_last(ts_delta(close, 1))",
                "signal = day_sum(cs_rank(close))",
                "signal = day_last(gp_mean(code, close))",
                "signal = day_last(ts_RSI(close, 10))"):
        with pytest.raises(ValueError, match="ts_|ta_|cs_|gp_|分钟"):
            _run(bad)


def test_minute_scope_rejects_platform_macro_residue():
    """returns/vwap/adv20 宏展开为 ts_ 后同样被拒（B3.2 文本检查在展开后）。"""
    with pytest.raises(ValueError, match="ts_"):
        _run("signal = day_last(returns(close))")


def test_minute_scope_rejects_sequence_outputs():
    """outputs 直出分钟序列 → 拒（折日语义：信号必须逐 (code, date) 常数）。"""
    with pytest.raises(ValueError, match="折日"):
        _run("signal = im_mean(close, 30)")
    with pytest.raises(ValueError, match="折日"):
        _run("signal = im_sum(close, 10) * 2")
    with pytest.raises(ValueError, match="折日"):
        _run("signal = close / eod_close - 1")          # 裸 bars 列 = 序列
    with pytest.raises(ValueError, match="折日"):
        _run("signal = day_last(close)\ns = im_mean(close, 5)",
             outputs=["signal", "s"])                    # 逐输出检查


def test_minute_scope_accepts_fold_constant_outputs():
    """折日常数组合放行：day_* 中间赋值共享、注入列与字面量元素级组合。"""
    formula = """
dl = day_last(close)
bias = dl / eod_close - 1
cond = day_sum(volume) * 2
"""
    res = _run(formula, outputs=["bias", "cond"])
    assert res.height == 1
    assert res["bias"][0] == pytest.approx(0.0, rel=1e-9)   # 10/10 - 1
    assert res["cond"][0] == pytest.approx(2.0, rel=1e-9)   # 1 行 × 2


def test_im_delay_shift_discipline():
    """B3.4：im_delay k<0（未来）/k=0（无意义）拒——字面量/常量算术折叠
    （2-3 → -1，若不折叠 = codegen 静默 shift(-1) 取未来行）/顶层常量间接/kw d。"""
    for bad in ("signal = day_last(im_delay(close, -1))",
                "signal = day_last(im_delay(close, 0))",
                "signal = day_last(im_delay(close, 2 - 3))"):
        with pytest.raises(ValueError, match="im_delay"):
            _run(bad)
    with pytest.raises(ValueError, match="im_delay"):
        _run("_k = -2\nsignal = day_last(im_delay(close, _k))")
    with pytest.raises(ValueError, match="im_delay"):
        _run("signal = day_last(im_delay(close, d=-1))")
    # 正位移合法且组内生效
    res = _run("signal = day_last(im_delay(close, 1))")
    assert res["signal"].null_count() == 1                   # 组首 null（单行组）


def test_im_window_parameter_min_one():
    """im_* 窗口参数必须 >= 1（字面量/常量间接）。"""
    for bad in ("signal = day_sum(im_mean(close, 0))",
                "signal = day_sum(im_mean(close, -5))",
                "signal = day_last(im_std(close, 0))"):
        with pytest.raises(ValueError, match="窗口"):
            _run(bad)
    with pytest.raises(ValueError, match="窗口"):
        _run("_w = 0\nsignal = day_sum(im_mean(close, _w))")


def test_daily_scope_rejects_minute_ops():
    """日频 scope（缺省）含 im_*/day_* → 明确 ValueError（非 NameError 深层崩）。"""
    daily = pl.DataFrame({"date": [_D, _D], "code": ["A", "B"], "close": [1.0, 2.0]})
    for bad in ("signal = day_last(close)", "signal = im_mean(close, 5)"):
        with pytest.raises(ValueError, match="bars_1m|分钟"):
            compute_formula(daily, bad)


def test_daily_scope_rejects_aliased_minute_ops():
    """R02-C1：日频 scope 经 import alias 调 im_* 同样拒（否则 codegen 里 import
    语句照常执行，分钟算子静默进日频公式）。"""
    daily = pl.DataFrame({"date": [_D, _D], "code": ["A", "B"], "close": [1.0, 2.0]})
    with pytest.raises(ValueError, match="bars_1m|分钟"):
        compute_formula(
            daily,
            "from factorlab.core.ops.minute_ops import im_mean as imn\n"
            "signal = imn(close, 5)")


def test_minute_scope_rejects_aliased_future_shift_e2e():
    """R02-C1 e2e：computed 面板入口上 alias 形态的负位移/零窗口必须拒，
    不得执行出未来分钟折日值（probe7/11 实测此前 ACCEPTED）。"""
    for bad in (
        "from factorlab.core.ops.minute_ops import im_delay as imd\n"
        "signal = day_sum(imd(close, -1))",
        "from factorlab.core.ops.minute_ops import im_mean as imn\n"
        "signal = day_sum(imn(close, 0))",
        "signal = day_sum(im_delay(close, 2 ** 2 - 5))",
        "signal = day_sum(im_mean(close, 2 ** 0 - 1))",
    ):
        with pytest.raises(ValueError, match="im_delay|窗口|分钟"):
            _run(bad)


def test_minute_scope_forbids_universe_mask():
    """minute scope 装配面无 CS mask（截面掩码是日频机制）→ 显式拒。"""
    with pytest.raises(ValueError, match="universe_mask"):
        compute_formula(_min_df(), "signal = day_last(close)",
                        universe_mask="__factorlab_universe_active",
                        scope="bars_1m")


# ---------------------------------------------------------------- gate 直调层
# 下列为 validate_minute_scope/折叠判定分支的直调补测（compute_formula 变换链会
# 先期常量折叠/改写，部分折叠子分支只在此层可达）——行为锚点仍为 B2.4/B3.4。
from factorlab.core.engine.minute_gate import validate_minute_scope


def test_gate_direct_window_shift_arithmetic_folds():
    """窗口/位移参数的算术折叠（B3.4 门漏 = codegen 静默生成未来行）：四则/Pow/
    IfExp/abs/int 常量形态折叠为负/零位移必拒（R02-C1：此前 Pow/IfExp/Call 不折叠
    → 2**2-5 静默 shift(-1) 取未来分钟）。"""
    for bad in ("signal = day_last(im_delay(close, 1 + 2 - 4))",   # Add+Sub → -1
                "signal = day_last(im_delay(close, 3 - 4))",       # Sub → -1
                "signal = day_last(im_delay(close, 2 * 3 - 6))",   # Mult → 0
                "signal = day_last(im_delay(close, 7 % 2 - 8))",   # Mod+Sub → -7
                "signal = day_last(im_delay(close, 6 // 7))",      # FloorDiv → 0
                "signal = day_last(im_delay(close, 10 / 5 - 2))",  # Div → 0
                "signal = day_last(im_delay(close, d=0))",         # kw d → 0
                "_k = -3\nsignal = day_last(im_delay(close, d=_k))"):
        with pytest.raises(ValueError, match="im_delay"):
            validate_minute_scope(bad, ["signal"])
    # 折叠为正位移/正窗口 → 放行
    validate_minute_scope(
        "signal = day_last(im_delay(close, 7 % 2))\n"
        "s2 = day_sum(im_mean(close, 2 * 3))", ["signal", "s2"])
    validate_minute_scope("signal = day_last(im_delay(close, 2 ** 4))",
                          ["signal"])


def test_gate_direct_pow_ifexp_call_folds():
    """R02-C1（实测绕过：`2**2-5` 过门且 == im_delay(-1)；`2**0-1` 窗口 0 静默
    输出 0.0）：Pow/IfExp/abs/int 常量折叠必须进静态门——否则 codegen 静默生成
    未来行/空窗口。"""
    for bad in ("signal = day_last(im_delay(close, 2 ** 2 - 5))",   # Pow → -1
                "signal = day_last(im_delay(close, 2 ** 0 - 1))",   # Pow → 0
                "signal = day_last(im_delay(close, 0 ** 2))",       # Pow → 0
                "signal = day_last(im_delay(close, -abs(1)))",      # Call+USub → -1
                "signal = day_last(im_delay(close, -abs(-1)))",     # Call+USub → -1
                "signal = day_last(im_delay(close, int(-1)))",      # Call → -1
                "signal = day_last(im_delay(close, -1 if True else 1))",
                "signal = day_last(im_delay(close, -1 if 1 > 0 else 1))"):
        with pytest.raises(ValueError, match="im_delay"):
            validate_minute_scope(bad, ["signal"])
    for bad in ("signal = day_sum(im_mean(close, 2 ** 0 - 1))",     # Pow → 0
                "signal = day_sum(im_mean(close, 2 ** 2 - 5))",     # Pow → -1
                "signal = day_sum(im_mean(close, int(0)))",         # Call → 0
                "signal = day_sum(im_mean(close, -abs(0)))"):       # Call → 0
        with pytest.raises(ValueError, match="窗口"):
            validate_minute_scope(bad, ["signal"])
    # 正参数形态不误伤
    validate_minute_scope(
        "signal = day_last(im_delay(close, abs(-3)))\n"
        "s2 = day_sum(im_mean(close, int(30)))", ["signal", "s2"])


def test_gate_resolves_import_aliases():
    """R02-C1：`from ...minute_ops import im_delay as imd` 不得绕过 B3.4 门；
    ts_/cs_ 等跨层别名同样按原名判（B3.2）。"""
    validate_minute_scope  # noqa: B018 —— 显式声明本测试直调门
    for bad in (
        "from factorlab.core.ops.minute_ops import im_delay as imd\n"
        "signal = day_sum(imd(close, -1))",
        "from factorlab.core.ops.minute_ops import im_delay as imd\n"
        "signal = day_sum(imd(close, 2 ** 2 - 5))",
        "from factorlab.core.ops.minute_ops import im_mean as imn\n"
        "signal = day_sum(imn(close, 0))",
        "from polars_ta.prefix.wq import ts_mean as tm\n"
        "signal = day_last(tm(close, 5))",
    ):
        with pytest.raises(ValueError, match="im_delay|窗口|ts_|分钟"):
            validate_minute_scope(bad, ["signal"])
    # 别名指向合法参数仍放行
    validate_minute_scope(
        "from factorlab.core.ops.minute_ops import im_delay as imd\n"
        "signal = day_sum(imd(close, 3))", ["signal"])


def test_gate_alias_does_not_treat_import_name_as_call():
    """import 语句本身不得被误判为调用（`import x as im_delay` 不是 im_* 调用）。"""
    validate_minute_scope(
        "from factorlab.core.ops.minute_ops import day_sum as _ds\n"
        "signal = _ds(close)", ["signal"])


def test_at_minute_k_static_validation():
    """R09-PERF-I2：at_minute k 静态门——必须 int ∈ 0..239（字面量/常量折叠/
    顶层常量间接）；bool/float/负/越界拒。非折叠形态静态放行（运行时硬校验）。"""
    for bad in ("signal = at_minute(close, -1)",
                "signal = at_minute(close, 240)",
                "signal = at_minute(close, True)",
                "signal = at_minute(close, 1.5)",
                "signal = at_minute(close, 120.0)",
                "signal = at_minute(close, 241 - 1)",
                "_k = 250\nsignal = at_minute(close, _k)"):
        with pytest.raises(ValueError, match="at_minute"):
            validate_minute_scope(bad, ["signal"])
    # 合法边界/常量折叠/顶层常量间接放行；at_minute 是折日常数（可直出）
    validate_minute_scope("a = at_minute(close, 0)\nb = at_minute(close, 239)\n"
                          "c = at_minute(close, 2 * 60)\n"
                          "_k = 120\nd = at_minute(close, _k)",
                          ["a", "b", "c", "d"])


def test_at_minute_bad_k_rejected_e2e():
    """at_minute 非法 k 经 compute_formula 全链报错（静态门主防线），不得静默
    生成空/错误取值。"""
    for bad in ("signal = at_minute(close, -1)",
                "signal = at_minute(close, 240)",
                "signal = at_minute(close, True)",
                "signal = at_minute(close, 1.5)"):
        with pytest.raises(ValueError, match="at_minute"):
            _run(bad)


def test_gate_direct_fold_combinators():
    """折日 fold 判定组合子：未知列名=序列（拒）、一元负序列（拒）、BoolOp 含
    序列（拒）、Compare 对序列（拒）、一元保常数函数与 if_else 全常数（放行）、
    未注册调用（拒）——B2.4 折日语义的逐分支锚点。"""
    for bad in ("signal = day_last(close) + mystery_col",
                "signal = -im_sum(close, 5)",
                "signal = (day_last(close) > 0) & (close > 1)",
                "signal = day_last(close) > close",
                "signal = day_sum(amount) + 1\n"
                "s2 = mystery_fn(day_last(close))",
                "signal = if_else(mystery_col > 1, day_sum(amount), 0)"):
        with pytest.raises(ValueError, match="折日"):
            validate_minute_scope(bad, ["signal", "s2"] if "s2" in bad
                                  else ["signal"])
    validate_minute_scope(
        "a = abs(day_sum(amount))\n"
        "b = if_else(day_last(close) > 10, day_sum(amount), a)",
        ["a", "b"])


def test_gate_direct_annotated_and_missing_outputs():
    """顶层 AnnAssign（类型注解赋值）入 assigns 表；declared output 未在公式产生
    → continue（缺列核对留给 compute_formula 声明不符报错，门不越位）。"""
    validate_minute_scope("signal: float = day_last(close)", ["signal"])
    validate_minute_scope("signal = day_last(close)", ["signal", "nosuch"])
    with pytest.raises(ValueError, match="im_delay"):
        validate_minute_scope("_k = 0\nsignal: float = day_last(im_delay("
                              "close, _k))", ["signal"])
