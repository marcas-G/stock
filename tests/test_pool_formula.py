"""M4（G2）：公式化股票池——`universe.formula` 布尔条件逐 (code, 交易日) 定池。

断言来源：design doc §4（分层模型：骨架 ∩ 公式条件；四选一互斥；求值顺序锁
骨架 → fill → view → 池公式 → in_universe = 骨架 ∧ 条件 → 主公式 masked；
主公式 CS/GP 只见动态池当日横截面——4.4 不变式：池外不进截面、同股池内/池外
CS 值不一致；labels 键 = 池成员 t）+ plan M4 行（池公式接受/布尔不可判定拒绝/
未来引用拒绝/未知列走报错助手；动态池 CS 不变式；静态模式零回归）。

池公式文法 v1：**单个布尔表达式**（裸表达式或 `signal = <expr>` 赋值形式）——
def/多语句不在 v1 池文法（报错给指引）；与主公式同门链（AST 门/保留名双门/
未来引用门/平台宏展开/分区校验）+ 额外布尔可判定门（静态：值含比较/布尔运算；
动态：结果列 dtype Bool）。

夹具：rf 2 股（A 000001.SZ base10、B 600519.SH base20，close = base+i+1，
i=0..5：A 11..16 / B 21..26）+ attr 5 股（银行/白酒/flag/industry null 与 ''
——见 test_attributes_face）。
"""

import datetime

import polars as pl
import pytest

from factorlab.engine.compute import run_factor
from factorlab.spec import load_spec
from test_attributes_face import _seed as attr_seed
from test_run_factor import _DATES, _ctx, _seed as rf_seed

# 2 股夹具日 close（i+1 从 1 起）：A = 10+i+1、B = 20+i+1
# 池 close>15：d1..d5 仅 B（A 15 于 d5 不 >15），d6 {A,B}（A 16）
_MEMBERS_GT15 = {d: {"600519.SH"} if i < 5 else {"000001.SZ", "600519.SH"}
                 for i, d in enumerate(_DATES)}


def _pool_spec(tmp_path, pool: str, name: str = "pool_demo",
               main: str = "signal = close",
               start: str = "2024-01-02", end: str = "2024-01-09",
               universe_extra: str = "") -> object:
    path = tmp_path / f"{name}.yaml"
    # YAML 双引号标量不能含真实换行（折叠为空格）——池文本换行须转义 \n
    pool_lit = pool.replace("\\", "\\\\").replace("\n", "\\n")
    path.write_text(f"""
name: {name}
category: custom
direction: 1
universe:
  formula: "{pool_lit}"
{universe_extra}
date:
  start: "{start}"
  end: "{end}"
formula: |
  {main}
""", encoding="utf-8")
    return load_spec(path)


def _panel_members(panel: pl.DataFrame, day: str) -> set[str]:
    """某交易日 panel 成员（code 集合）——signal 值行即池成员行。"""
    day_df = panel.filter(pl.col("date") == datetime.date.fromisoformat(day))
    return set(day_df["code"].to_list())


# ================================================================
# 1. 池公式接受：裸布尔表达式 / signal = 赋值 两形式等价；逐日动态成员
# ================================================================

def test_pool_bare_and_assign_forms_identical(env, tmp_path):
    """池公式两文法形式逐日成员集合一致（panel 等值）。"""
    rf_seed(env)
    bare = run_factor(_pool_spec(tmp_path, "close > 15", name="bare"),
                      _ctx(env, tmp_path / "out_bare"))
    assign = run_factor(_pool_spec(tmp_path, "signal = close > 15", name="assign"),
                        _ctx(env, tmp_path / "out_assign"))
    assert bare.panel.select(["date", "code", "signal"]).equals(
        assign.panel.select(["date", "code", "signal"]))
    # 禁止行为：成员集合必须等于条件集合（非全骨架、非空）
    for d, expected in _MEMBERS_GT15.items():
        assert _panel_members(bare.panel, d) == expected


def test_pool_dynamic_membership_across_days(env, tmp_path):
    """成员逐日动态：close 上穿阈值日 A 入池（d5 不入、d6 入）。"""
    rf_seed(env)
    panel = run_factor(_pool_spec(tmp_path, "close > 15"),
                       _ctx(env, tmp_path / "out")).panel
    assert _panel_members(panel, "2024-01-05") == {"600519.SH"}   # A 15 未上穿
    assert _panel_members(panel, "2024-01-08") == {"600519.SH"}
    assert _panel_members(panel, "2024-01-09") == {"000001.SZ", "600519.SH"}  # A 16
    # 禁止行为：无成员行（A d5）与池外行不得出现在面板
    d5 = panel.filter((pl.col("date") == datetime.date(2024, 1, 5))
                      & (pl.col("code") == "000001.SZ"))
    assert d5.height == 0


# ================================================================
# 2. 4.4 不变式：主公式 CS 只见动态池横截面（池外不进截面）
# ================================================================

def test_pool_cs_cross_section_excludes_outside(env, tmp_path):
    """同股同 close、两种池状态（A 池内/池外）→ B 的 CS 值不一致：
    池外状态 B 独木成截面 pct rank 0.0；池内状态 B rank 1.0（stable cs_rank
    归一语义 (level-1)/(K-1)，平台所有 cs_rank 一致）——池外股不进截面统计。
    若实现回退全骨架，B 两 run 恒同值，断言失败。"""
    rf_seed(env)
    lo = run_factor(_pool_spec(tmp_path, "close > 15", name="lo",
                               main="signal = cs_rank(close)"),
                    _ctx(env, tmp_path / "out_lo"))          # d6: {A,B}
    hi = run_factor(_pool_spec(tmp_path, "close > 16", name="hi",
                               main="signal = cs_rank(close)"),
                    _ctx(env, tmp_path / "out_hi"))          # d6: {B}
    lo_d6 = lo.panel.filter(pl.col("date") == datetime.date(2024, 1, 9))
    hi_d6 = hi.panel.filter(pl.col("date") == datetime.date(2024, 1, 9))
    b_lo = lo_d6.filter(pl.col("code") == "600519.SH")["signal"].to_list()
    b_hi = hi_d6.filter(pl.col("code") == "600519.SH")["signal"].to_list()
    # 两 run 同日 B close 相同（26）——CS 值差异只可能来自截面组成
    assert lo_d6.filter(pl.col("code") == "600519.SH")["close"].to_list() == \
        hi_d6.filter(pl.col("code") == "600519.SH")["close"].to_list() == [26.0]
    assert b_lo == [1.0]        # {A16, B26} 截面：B (2-1)/(2-1) = 1.0
    assert b_hi == [0.0]        # 池外 A 不进截面：B 独木 (1-1)/1 = 0.0
    # 池外股票无成员行（A 在 hi 的 d6 不存在）
    assert "000001.SZ" not in set(hi_d6["code"].to_list())
    # d5 两 run 均 {B}：B 值一致（同截面同值——对照组）
    lo_d5 = lo.panel.filter(pl.col("date") == datetime.date(2024, 1, 5))
    hi_d5 = hi.panel.filter(pl.col("date") == datetime.date(2024, 1, 5))
    assert lo_d5.filter(pl.col("code") == "600519.SH")["signal"].to_list() == \
        hi_d5.filter(pl.col("code") == "600519.SH")["signal"].to_list() == [0.0]


# ================================================================
# 3. ts_ 池公式（warmup 自动提取）+ FULL==CHUNK 位级一致
# ================================================================

def test_pool_ts_formula_full_chunk_parity(env, tmp_path):
    """池公式用 ts_（close > ts_mean(close,2)）：chunk 模式 warmup 自动含池
    公式窗口（未显式给 warmup_days）→ FULL 与 CHUNK panel 逐位一致。"""
    rf_seed(env, n_days=12)
    end = "2024-01-17"
    full = run_factor(_pool_spec(tmp_path, "close > ts_mean(close, 2)",
                                 name="pool_ts", end=end),
                      _ctx(env, tmp_path / "out_full"))
    chunked = run_factor(_pool_spec(tmp_path, "close > ts_mean(close, 2)",
                                    name="pool_ts_chunk", end=end),
                         _ctx(env, tmp_path / "out_chunk", chunk_days=2))
    assert full.panel.height > 0
    assert full.panel.select(["date", "code", "signal"]).equals(
        chunked.panel.select(["date", "code", "signal"]))
    # d1 ts_mean 窗口不足 → null → 非成员（成员从 d2 起存在）
    assert _panel_members(full.panel, "2024-01-02") == set()
    assert _panel_members(full.panel, "2024-01-03") == \
        {"000001.SZ", "600519.SH"}


# ================================================================
# 4. gp_ 池公式：属性面供给 + 全骨架同行业组（设计 §4.2 GP 开放）
# ================================================================

def test_pool_gp_over_industry_uses_full_skeleton_and_attrs(env, tmp_path, monkeypatch):
    """池公式 gp_rank(industry, close) == 1.0：industry 属性由池公式引用供给
    （主公式未引用属性——属性读取只应因池公式发生）；组统计在全骨架同行业内
    （银行 A1/C2——C 落选、null 组 D1/E2——E 落选）。"""
    attr_seed(env)
    import factorlab.engine.compute as compute_mod
    calls = []
    real = getattr(compute_mod, "load_code_attributes", None)
    if real is None:
        raise AssertionError("load_code_attributes 未实现——红阶段预期")
    monkeypatch.setattr(
        compute_mod, "load_code_attributes",
        lambda *a, **k: calls.append(1) or real(*a, **k))
    panel = run_factor(_pool_spec(tmp_path, "gp_rank(industry, close) == 1.0",
                                  name="pool_gp"),
                       _ctx(env, tmp_path / "out")).panel
    # d1：银行组 A=1/C=2、白酒 B=1、null 组 D=1/E=2 → 成员 = 各组 rank1
    members = _panel_members(panel, "2024-01-02")
    assert members == {"000001.SZ", "600519.SH", "600036.SH"}
    assert _panel_members(panel, "2024-01-09") == \
        {"000001.SZ", "600519.SH", "600036.SH"}   # C/E 恒 rank2 落选
    # 禁止行为：组内统计不得只有候选成员（若只算池内则组键无意义）——
    # C（组内 rank2）不存在即证明组含 C；空属性 D 参与 null 组互组不污染真实组
    assert calls == [1, 1]     # signal runtime + label runtime 各恰一次全量供给


# ================================================================
# 5. 池公式门链：布尔可判定 / 未来引用 / 未知列报错助手 / 保留名
# ================================================================

def test_pool_reject_non_boolean_expression(env, tmp_path):
    """布尔不可判定：无比较/布尔运算的池公式静态拒绝（不落库求值）。"""
    rf_seed(env)
    for pool in ("signal = close + 1", "signal = ts_mean(close, 2)"):
        with pytest.raises(ValueError, match="布尔"):
            run_factor(_pool_spec(tmp_path, pool, name="nb"),
                       _ctx(env, tmp_path / "out_nb"))


def test_pool_reject_dtype_not_bool(env, tmp_path):
    """含比较但结果为数值 dtype（if_else 数值分支）→ 动态 dtype 拒绝。"""
    rf_seed(env)
    with pytest.raises(ValueError, match="Bool"):
        run_factor(_pool_spec(tmp_path,
                              "signal = if_else(close > 15, close, 0.0)",
                              name="dtype"),
                   _ctx(env, tmp_path / "out_dtype"))


def test_pool_reject_future_input(env, tmp_path):
    """池公式与主公式同未来引用门：forward_* 显式引用 → fail fast。"""
    rf_seed(env)
    with pytest.raises(ValueError, match="future/label inputs are forbidden"):
        run_factor(_pool_spec(tmp_path, "signal = close > forward_return_5d",
                              name="future"),
                   _ctx(env, tmp_path / "out_future"))


def test_pool_reject_reserved_read(env, tmp_path):
    """池公式与主公式同保留名读门：in_universe 读取 → fail fast（读取侧文案
    与主公式同源——FactorDSLError「内部保留名」，非绑定门「reserved internal
    name」；绑定侧由 test_pool_reject_reserved_binding 锁）。"""
    rf_seed(env)
    with pytest.raises(ValueError, match="内部保留名"):
        run_factor(_pool_spec(tmp_path, "signal = close > in_universe",
                              name="reserved"),
                   _ctx(env, tmp_path / "out_reserved"))


def test_pool_reject_reserved_binding(env, tmp_path):
    """池公式同保留名绑定门：赋值名会被归一掉（不参与语义），但 in_universe
    作为绑定入口仍必须在归一前被门挡下（内部名不是用户命名空间的一部分）。"""
    rf_seed(env)
    with pytest.raises(ValueError, match="reserved internal name"):
        run_factor(_pool_spec(tmp_path, "in_universe = close > 15",
                              name="reserved_bind"),
                   _ctx(env, tmp_path / "out_reserved_bind"))


def test_pool_reject_unknown_column_error_helper(env, tmp_path):
    """池公式未知列走 M1 报错助手（可用列清单 + 最相似候选）。"""
    rf_seed(env)
    with pytest.raises(ValueError) as exc:
        run_factor(_pool_spec(tmp_path, "signal = close > clsoe", name="typo"),
                   _ctx(env, tmp_path / "out_typo"))
    msg = str(exc.value)
    assert "未知列名" in msg and "最接近的列" in msg and "close" in msg


def test_pool_reject_multi_statement(env, tmp_path):
    """v1 池文法：多语句/def 拒绝（指引单布尔表达式）。"""
    rf_seed(env)
    with pytest.raises(ValueError, match="池公式"):
        run_factor(_pool_spec(tmp_path, "x = close > 15\nsignal = x",
                              name="multi"),
                   _ctx(env, tmp_path / "out_multi"))


# ================================================================
# 6. spec 互斥四选一 + 公式模式市场骨架
# ================================================================

def test_universe_formula_mutually_exclusive_with_others(tmp_path):
    """universe 四选一互斥：formula 与 codes/rules/ref 同现 → 加载期报错
    （pydantic 忽略 extra 的隐患：若不实现公式分支，formula+codes 会静默按
    codes 跑——本测试锁死报错）。"""
    err = "universe 必须且只能提供 ref / codes / rules / formula 之一"
    formula = '  formula: "close > 1"\n'
    clashes = {
        "codes": ('  codes: ["000001.SZ"]\n', ""),
        "rules": ("  rules: {exclude_st: true}\n", ""),
        "ref": ("  ref: hs300\n", ""),
        "only_formula": ("", err),           # 合法组合——不触发该错误
    }
    for name, (second_line, expected) in clashes.items():
        path = tmp_path / f"u_{name}.yaml"
        body = f"""
name: demo
category: custom
direction: 1
universe:
{formula}{second_line}date:
  start: "2024-01-02"
  end: "2024-01-09"
formula: |
  signal = close
"""
        path.write_text(body, encoding="utf-8")
        if name == "only_formula":
            assert load_spec(path).universe.formula == "close > 1"
        else:
            with pytest.raises(ValueError, match=err):
                load_spec(path)


def test_pool_formula_mode_market_skeleton(env, tmp_path):
    """formula 分支无 codes：骨架 = 全部 canonical 证券（SSE/SZSE）——
    池条件在全市场上求值（universe_count == 全市场）。close>1 全成员——
    若实现错把 codes 当成员或池条件当名单，universe_count 或成员断言失败。"""
    rf_seed(env)
    spec = _pool_spec(tmp_path, "close > 1", name="market")
    result = run_factor(spec, _ctx(env, tmp_path / "out_market"))
    summary_path = tmp_path / "out_market" / "summary.json"
    import json
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["universe_count"] == 2   # 全市场 2 只都进骨架（候选集规模）
    assert _panel_members(result.panel, "2024-01-09") == \
        {"000001.SZ", "600519.SH"}          # 两股都满足条件 → 都入池


# ================================================================
# 7. labels 键 = 池成员 t（复权口径与 signal runtime 同基准）
# ================================================================

def test_pool_label_keys_match_signal_membership_qfq_basis(env, tmp_path):
    """labels 只对池成员 t 生成键；复权下（A d3 除权 adj 1.5）池条件以 qfq
    视图判定——若 label runtime 误用 raw close，A d1（raw 11 vs qfq 7.33）会
    被误判入池、键集合即不一致。"""
    rf_seed(env, ex_date=True)   # A：raw d1 11 / d3 8、adj 1.5 起 d3 → qfq 7.33
    spec = _pool_spec(tmp_path, "close > 7.5", name="qfq_keys")
    result = run_factor(spec, _ctx(env, tmp_path / "out_qfq"))
    panel_keys = set(zip(result.panel["date"].to_list(),
                         result.panel["code"].to_list()))
    lab = result.label_artifact.frame
    lab_keys = set(zip(lab["date"].to_list(), lab["code"].to_list()))
    assert panel_keys == lab_keys
    # d1：A qfq 7.33 ≤ 7.5 池外（raw 11 在池内——若 raw 误判键含 A 即失败）
    d1_keys = {code for d, code in panel_keys if d == datetime.date(2024, 1, 2)}
    assert d1_keys == {"600519.SH"}
    d3_keys = {code for d, code in panel_keys if d == datetime.date(2024, 1, 4)}
    assert d3_keys == {"000001.SZ", "600519.SH"}   # qfq 8 > 7.5


# ================================================================
# 8. 空池：整体无成员 fail fast；部分日无成员容错
# ================================================================

def test_pool_overall_empty_membership_fail_fast(env, tmp_path):
    """条件全期不满足 → fail fast（不产出空 artifact 静默成功）。"""
    rf_seed(env)
    with pytest.raises(ValueError, match="无成员"):
        run_factor(_pool_spec(tmp_path, "close > 1000", name="empty"),
                   _ctx(env, tmp_path / "out_empty"))


def test_pool_partially_empty_days_ok(env, tmp_path):
    """仅 d6 B 入池（close>25）——前 5 日无成员不报错，面板只含成员日行。"""
    rf_seed(env)
    panel = run_factor(_pool_spec(tmp_path, "close > 25", name="sparse"),
                       _ctx(env, tmp_path / "out_sparse")).panel
    assert panel.height == 1
    row = panel.row(0, named=True)
    assert (row["date"], row["code"]) == (datetime.date(2024, 1, 9), "600519.SH")


# ================================================================
# 9. override 语义：codes/ref 作 dry-run 白名单（仅限骨架）
# ================================================================

def test_pool_override_restricts_skeleton_only(env, tmp_path):
    """RunContext.universe_override 限制骨架（dry-run 白名单），池条件照常判定
    ——override 000001 时骨架仅 A：A d6 close 16 > 15 入池、其余日无成员。"""
    rf_seed(env)
    ctx = _ctx(env, tmp_path / "out_ovr", universe_override="000001")
    panel = run_factor(_pool_spec(tmp_path, "close > 15", name="ovr"),
                       ctx).panel
    assert set(zip(panel["date"].to_list(), panel["code"].to_list())) == \
        {(datetime.date(2024, 1, 9), "000001.SZ")}
    assert panel["signal"].to_list() == [16.0]


# ================================================================
# 10. 动态池 gp 组缺失（G5 语义回归网）：池使某行业组整组缺失——
#     组统计只在该组**池内成员**上算，缺员不改组成、池外同组股不进统计
# ================================================================

def test_pool_gp_group_missing_semantics(env, tmp_path):
    """池 close > 20 剔除银行组 A（11..16）留 C（31..36）——银行组每日缺 A；
    主公式 gp_rank（裸秩语义：组内升序 1..K）若泄漏到全骨架同行业组，C 会按
    {A,C} 组统计得秩 2.0 / mean 23.0，断言 1.0 / 33.0 锁死「组统计只见池内
    成员」（组缺失股不进统计也不出成员行）。白酒 B 单员组、null 组 D/E 整组
    在池——D/E 互组语义在池内截面同样成立（秩 1/2）。"""
    attr_seed(env)
    # d3（2024-01-04）close：A13 C33 B53 D43 E66 → 池内 {C,B,D,E}
    day = datetime.date(2024, 1, 4)

    rank_panel = run_factor(
        _pool_spec(tmp_path, "close > 20", name="gp_miss_rk",
                   main="signal = gp_rank(industry, close)"),
        _ctx(env, tmp_path / "out_gp_miss_rk")).panel
    rk = rank_panel.filter(pl.col("date") == day)
    assert set(rk["code"].to_list()) == \
        {"600000.SH", "600519.SH", "600036.SH", "601988.SH"}  # A 整组缺失无行
    by_code = {c: v for c, v in zip(rk["code"].to_list(), rk["signal"].to_list())}
    assert by_code["600000.SH"] == 1.0   # C 独木成银行组：裸秩 1
    assert by_code["600036.SH"] == 1.0 and by_code["601988.SH"] == 2.0  # null 组 D/E 池内互秩
    # 禁止行为：全骨架泄漏（A 进银行组统计）→ C 秩 2.0 ≠ 1.0
    assert "000001.SZ" not in by_code

    mean_panel = run_factor(
        _pool_spec(tmp_path, "close > 20", name="gp_miss_mn",
                   main="signal = gp_mean(industry, close)"),
        _ctx(env, tmp_path / "out_gp_miss_mn")).panel
    mn = mean_panel.filter(pl.col("date") == day)
    mn_by_code = {c: v for c, v in zip(mn["code"].to_list(), mn["signal"].to_list())}
    assert mn_by_code["600000.SH"] == 33.0   # 池内 C 自组均值（泄漏 → (13+33)/2=23）
    assert mn_by_code["600519.SH"] == 53.0   # 白酒 B 整组在池——自组均值不变
    assert mn_by_code["600036.SH"] == 54.5   # null 组 D/E 池内互均 (43+66)/2
