"""W1 qa/streams raw→事件 归一化 TDD（红→绿）

断言来源 = 设计规格 §2 数据语义 + 本会话 schema 侦察（2026-09-10 钉死）:
- raw zip 时间列 = HHMMSSmmm（'91500020'=09:15:00.020, 前导零省略 → pad 9 位）
- SZ 委托: 交易所委托号=逐单 id（委托编号恒 0 无用）; 委托类型 ∈ {0,'1',U};
  委托代码 ∈ {B,S}=侧别; 价/量已在 ×10000 整数刻度
- SZ 成交: 成交代码 'C' 空BS=撤单（ref=叫买/叫卖单侧）; '0' BS B/S=成交（双侧 ref）
- SH 委托: A=加(委托代码 B/S 侧) / D=撤(量=全撤剩余) / S=杂项
- SH 成交: 成交代码空; BS标志=主买/主卖 → maker=对侧 ref 单侧消费
存根击穿: 每函数逐行事件输出逐值断言, 恒返回空/固定行的存根必 FAIL。
"""
import pandas as pd
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qa import streams as S


def test_hms_to_ms_of_day():
    """HHMMSSmmm(前导零省略, pad 9) → ms-of-day"""
    assert S.hms_to_ms(91500020) == 33300020    # 09:15:00.020
    assert S.hms_to_ms(93000000) == 34200000    # 09:30:00.000
    assert S.hms_to_ms(113000000) == 41400000   # 11:30:00.000
    assert S.hms_to_ms(130000000) == 46800000   # 13:00:00.000
    assert S.hms_to_ms(150000000) == 54000000   # 15:00:00.000
    assert S.hms_to_ms(80000000) == 28800000    # 08:00:00.000 (预开帧)
    assert S.hms_to_ms(145959670) == 53999670


def _df(cols_rows, cols):
    return pd.DataFrame({c: [r[i] for r in cols_rows] for i, c in enumerate(cols)})


SZ_O = ['时间', '交易所委托号', '委托类型', '委托代码', '委托价格', '委托数量']


def test_sz_orders_adds_mapping():
    """SZ 委托 → add 事件: id=交易所委托号, side=委托代码, otype=str(委托类型)"""
    df = _df([
        (91500020, 684, 0, 'B', 120000, 1000),   # 限价买单
        (91500020, 747, 0, 'S', 130400, 2300),   # 限价卖单
        (93000010, 99001, '1', 'B', 0, 200),     # 市价
        (93000020, 99002, 'U', 'S', 0, 100),     # 本方最优价0
        (93000030, 99003, 'U', 'B', 121900, 300),  # 本方最优带价
    ], SZ_O)
    evs, g = S.sz_orders_events(df)
    assert [e['id'] for e in evs] == [684, 747, 99001, 99002, 99003]
    assert [e['kind'] for e in evs] == ['add'] * 5
    assert evs[0]['ms'] == 33300020 and evs[3]['ms'] == 34200020
    assert evs[0]['side'] == 'B' and evs[1]['side'] == 'S'
    assert evs[0]['price'] == 120000 and evs[0]['qty'] == 1000
    assert evs[2]['otype'] == '1' and evs[2]['price'] == 0
    assert evs[4]['otype'] == 'U' and evs[4]['price'] == 121900


def test_sz_trades_cancel_and_fill_events():
    """SZ 成交 → 撤单/成交事件: C 空BS=1 cancel(ref 侧); '0'=双侧 fill"""
    df = _df([
        ('93000000', 36485, 'C', ' ', 0, 200, 0, 36335),     # B 侧撤单
        ('93000100', 36486, 'C', ' ', 0, 50, 4456, 0),       # S 侧撤单
        ('93000200', 36487, '0', 'B', 122800, 100, 4456, 36335),  # 成交双侧
        ('93000300', 36488, '0', 'S', 122900, 300, 0, 0),    # 双侧 ref 0
    ], ['时间', '成交编号', '成交代码', 'BS标志', '成交价格', '成交数量', '叫卖序号', '叫买序号'])
    evs, g = S.sz_trades_events(df)
    # 撤单 2 条 + 成交 1 条双侧(2 fill) + 全0 ref 行 → 无事件但 guard
    assert [e['kind'] for e in evs] == ['cancel', 'cancel', 'fill', 'fill']
    assert g['n_zero_ref'] == 1
    assert evs[0]['id'] == 36335 and evs[0]['qty'] == 200 and evs[0]['ms'] == 34200000
    assert evs[1]['id'] == 4456
    assert (evs[2]['id'], evs[3]['id']) == (36335, 4456)  # fill 双侧
    assert evs[2]['qty'] == 100
    # M3 前提: fill 事件携带成交价格 (打印价合法性分桶的输入)
    assert evs[2]['price'] == 122800 and evs[3]['price'] == 122800
    assert 'price' not in evs[0]               # 撤单事件无价 (取消行价=0 不上传)


def test_sz_trades_guards_count():
    """映射守卫: 成交行双侧 ref=0 → n_zero_ref; 撤单行双0 → n_zero_ref_cancel"""
    df = _df([
        ('93000000', 1, 'C', ' ', 0, 200, 0, 0),
        ('93000100', 2, '0', 'B', 122800, 100, 0, 0),
    ], ['时间', '成交编号', '成交代码', 'BS标志', '成交价格', '成交数量', '叫卖序号', '叫买序号'])
    evs, g = S.sz_trades_events(df)
    assert evs == []
    assert g['n_zero_ref'] == 2


def test_sh_orders_add_delete_aux():
    """SH 委托: A→add(side=委托代码), D→cancel(量=全撤剩余), S→aux 分类"""
    df = _df([
        (91500020, 865, 'A', 'B', 177300, 100),
        (93000000, 900, 'A', 'S', 178000, 200),
        (93000100, 901, 'D', 'B', 177300, 100),   # 全撤 100
        (91500000, 0, 'S', 'I', 0, 0),            # 杂项(开盘参考类)
        (93002000, 0, 'S', 'C', 177500, 0),       # 杂项(成交参考?)
    ], ['时间', '交易所委托号', '委托类型', '委托代码', '委托价格', '委托数量'])
    evs, aux = S.sh_orders_events(df)
    assert [e['kind'] for e in evs] == ['add', 'add', 'cancel']
    assert evs[0]['side'] == 'B' and evs[0]['otype'] == 'A'
    assert evs[1]['ms'] == 34200000 and evs[2]['ms'] == 34200100
    assert evs[2]['id'] == 901 and evs[2]['qty'] == 100
    assert aux['S'] == 2
    assert aux['S_codes'] == {'I': 1, 'C': 1}  # 杂项委托代码域校准计数


def test_sh_trades_both_refs_consumed():
    """SH 成交: 双侧 ref 都是 resting 对手单（41% 不可解析=taker 先成交后报缺 add,
    消费侧不影响簿消费核算; 逐 ref 可解析才发事件）"""
    df = _df([
        (93000000, 294143, 'B', 160000, 100, 233327, 216713),  # 双 ref
        (93000100, 294144, 'S', 160100, 50, 0, 216714),        # ask ref=0 → 仅 bid
    ], ['时间', '成交编号', 'BS标志', '成交价格', '成交数量', '叫卖序号', '叫买序号'])
    evs, g = S.sh_trades_events(df)
    assert [(e['kind'], e['id'], e['qty']) for e in evs] == [
        ('fill', 216713, 100), ('fill', 233327, 100), ('fill', 216714, 50)]
    assert [e['price'] for e in evs] == [160000, 160000, 160100]  # M3 输入
    assert g['n_zero_ref'] == 0
