"""金样 fixtures 读取器（W1 冻结；W2 引擎 TDD 与真实切片单测共用）

- load_scenario(name): scenarios/{name}.csv → list[dict]（数值列转 int，'#' 注释行跳过）
- check_pins(): 全部金样文件 sha256 与 pins.sha256 逐一对账 → 失配列表（空=通过）

金样纪律: 编辑场景/切片必须同更 pin；防静默改变语义使 W2 断言失效仍绿。
"""
import glob, hashlib, os

FIX = os.path.dirname(os.path.abspath(__file__))
PIN_FILE = os.path.join(FIX, 'pins.sha256')

_CSV_COLS = ['kind', 'ms', 'side', 'price', 'qty', 'id', 'ref2', 'otype', 'note']


def load_scenario(name):
    """scenarios CSV → rows。kind/otype/note/side 保 str；ms/price/qty/id/ref2 → int"""
    rows = []
    with open(os.path.join(FIX, 'scenarios', f'{name}.csv'), encoding='utf-8') as f:
        header = None
        for line in f:
            line = line.rstrip('\n')
            if not line.strip() or line.startswith('#'):
                continue
            parts = line.split(',')
            if header is None:
                header = parts
                continue
            d = dict(zip(header, parts))
            for k in ('ms', 'price', 'qty', 'id'):
                d[k] = int(d[k])
            d['ref2'] = int(d['ref2']) if d['ref2'] else 0
            rows.append(d)
    return rows


def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def pinned_files():
    """pins.sha256 内容: 每行 'hexdigest  相对路径'（git 惯例格式）"""
    out = {}
    with open(PIN_FILE, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            h, rel = line.split(None, 1)
            out[rel] = h
    return out


def check_pins():
    """→ [(rel_path, 'missing'|'mismatch', expected, actual)]；空列表=全部通过"""
    bad = []
    for rel, expect in pinned_files().items():
        p = os.path.join(FIX, rel)
        if not os.path.exists(p):
            bad.append((rel, 'missing', expect, ''))
            continue
        actual = sha256(p)
        if actual != expect:
            bad.append((rel, 'mismatch', expect, actual))
    return bad
