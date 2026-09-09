"""raw zip CSV → 归一化事件流（W1 校准与 W2 引擎共用; 纯函数, 逐行映射）

时间换算（2026-09-10 schema 侦察钉死）: raw 时间列 = HHMMSSmmm 整数、各部分
前导零省略 → 统一 pad 9 位再换算 ms-of-day（config 阶段常量同刻度）。

SZ（schema 侦察实证）:
- 逐笔委托: 交易所委托号 = 逐单 id（委托编号恒 0 无用）; 委托类型 ∈ {0,'1',U};
  委托代码 ∈ {B,S} = 侧别; 价已 ×10000 整数刻度 → add 事件
- 逐笔成交: 成交代码 'C' + 空 BS = 撤单（ref=叫买/叫卖单侧, qty=撤量, price=0）
  → cancel 事件; 成交代码 '0' + BS B/S = 成交（叫买/叫卖双侧 ref 都消费）
  → fill×2; 双侧 ref=0 → guard n_zero_ref（撤单双侧 0 同计数）
SH:
- 逐笔委托: 类型 A → add（委托代码 B/S=侧别）; D → cancel（量=全撤剩余量,
  语义与 C 同构入同通道）; S → aux 杂项计数（委托代码 I/C/J/O/S... 域待校准表）
- 逐笔成交: 成交代码空; 叫买/叫卖 ref = 双侧 resting 对手单 → fill×2 逐 ref
  可解析才发（taker 先成交后报缺 add → 该侧 fill unknown 落账本计数）
确定性: 每行事件发射顺序固定; 时间戳换算单调保持。
"""
import pandas as pd

KIND_ADD, KIND_FILL, KIND_CANCEL = 'add', 'fill', 'cancel'


def hms_to_ms(v: int) -> int:
    """HHMMSSmmm（前导零省略）→ ms-of-day。91500020→33300020"""
    s = f'{v:09d}'
    hh, mm, ss, mmm = int(s[:2]), int(s[2:4]), int(s[4:6]), int(s[6:9])
    return hh * 3600000 + mm * 60000 + ss * 1000 + mmm


# ---------- SZ ----------

def sz_orders_events(df: pd.DataFrame):
    """SZ 逐笔委托 → (add 事件列表, guards)。行域: 类型 0/1/U 全收（价格语义
    '1'/价0/U价0 永不进簿由引擎裁决, 流内保留）"""
    evs, guards = [], dict(n_bad_row=0)
    for r in df.itertuples(index=False):
        oid, qty, price = r.交易所委托号, int(r.委托数量), int(r.委托价格)
        if qty <= 0:
            guards['n_bad_row'] += 1
            continue
        evs.append(dict(kind=KIND_ADD, ms=hms_to_ms(int(r.时间)), id=oid,
                        side=str(r.委托代码).strip(), price=price, qty=qty,
                        otype=str(r.委托类型).strip()))
    return evs, guards


def sz_trades_events(df: pd.DataFrame):
    """SZ 逐笔成交 → (事件, guards)。撤单 C → cancel; 成交 '0' → fill×2(双侧 ref);
    双侧 ref=0 → guard（不产事件, 价=0 撤单行的 ref 单侧恒在）"""
    evs, guards = [], dict(n_zero_ref=0)
    for r in df.itertuples(index=False):
        code = str(r.成交代码).strip()
        bid, ask, qty = int(r.叫买序号), int(r.叫卖序号), int(r.成交数量)
        ms = hms_to_ms(int(r.时间))
        if code == 'C':
            # 撤单: 单侧 ref（撤单价=0, 量=撤量）
            if bid > 0:
                evs.append(dict(kind=KIND_CANCEL, ms=ms, id=bid, qty=qty,
                                side='B'))
            elif ask > 0:
                evs.append(dict(kind=KIND_CANCEL, ms=ms, id=ask, qty=qty,
                                side='S'))
            else:
                guards['n_zero_ref'] += 1
        else:
            # 成交: 双侧 ref 消费（taker 缺侧 = ref 0 → 不发该侧）
            if bid == 0 and ask == 0:
                guards['n_zero_ref'] += 1
                continue
            px = int(r.成交价格)                  # M3: fill 附成交价 (打印合法性输入)
            if bid > 0:
                evs.append(dict(kind=KIND_FILL, ms=ms, id=bid, qty=qty,
                                side='B', price=px))
            if ask > 0:
                evs.append(dict(kind=KIND_FILL, ms=ms, id=ask, qty=qty,
                                side='S', price=px))
    return evs, guards


# ---------- SH ----------

def sh_orders_events(df: pd.DataFrame):
    """SH 逐笔委托 → (事件, aux)。A→add; D→cancel(全撤剩余量); S→aux 杂项分类
    （委托代码域 I/C/J/O/S... 由校准表记录, 不进簿）"""
    evs, aux = [], dict(S=0, S_codes={})
    for r in df.itertuples(index=False):
        t = str(r.委托类型).strip()
        if t == 'A':
            evs.append(dict(kind=KIND_ADD, ms=hms_to_ms(int(r.时间)),
                            id=int(r.交易所委托号), side=str(r.委托代码).strip(),
                            price=int(r.委托价格), qty=int(r.委托数量),
                            otype='A'))
        elif t == 'D':
            evs.append(dict(kind=KIND_CANCEL, ms=hms_to_ms(int(r.时间)),
                            id=int(r.交易所委托号), qty=int(r.委托数量),
                            side=str(r.委托代码).strip()))
        elif t == 'S':
            aux['S'] += 1
            c = str(r.委托代码).strip()
            aux['S_codes'][c] = aux['S_codes'].get(c, 0) + 1
    return evs, aux


def sh_trades_events(df: pd.DataFrame):
    """SH 逐笔成交 → (事件, guards)。双侧 ref 都是 resting 对手单（成交代码域空）;
    逐 ref 消费; 某侧 ref=0 仅不发该侧（先成交后报缺 add 属未知消费, 由账本计数）"""
    evs, guards = [], dict(n_zero_ref=0)
    for r in df.itertuples(index=False):
        bid, ask, qty = int(r.叫买序号), int(r.叫卖序号), int(r.成交数量)
        ms = hms_to_ms(int(r.时间))
        if bid == 0 and ask == 0:
            guards['n_zero_ref'] += 1
            continue
        px = int(r.成交价格)                      # M3: fill 附成交价
        if bid > 0:
            evs.append(dict(kind=KIND_FILL, ms=ms, id=bid, qty=qty, side='B',
                            price=px))
        if ask > 0:
            evs.append(dict(kind=KIND_FILL, ms=ms, id=ask, qty=qty, side='S',
                            price=px))
    return evs, guards
