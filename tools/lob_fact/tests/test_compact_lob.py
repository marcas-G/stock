"""W5 体积重打包 TDD（先红后绿）: 逐 date 文件按目标行组几何/zstd 级流式重写,
内容摘要 (行数 + 批级 sha256 链) 校验通过才原子替换。

断言源 = 计划 W5 验收行 "总体积 ≤1.5×源" + 工程不变量 "重打包不得改内容":
  - 摘要是**内容**判据: 同内容异编码 → 同摘要; 改一个值/截断 → 摘要变 (存根必败);
  - 替换是原子的: 校验失败 → 原文件与 tmp 都不动 (不静默损坏);
  - 批算运行中 (flock 被占) 拒绝重打包 —— 单写者纪律。
"""
import json
import os
import sys

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import compact_lob as CL


def _mk(path, n=300, rgr=100, level=1, shift=0):
    """真实 parquet: 两列 (i 递增, s 单字符循环) → 行组几何可见"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    t = pa.table({'i': pa.array(range(shift, shift + n), pa.int64()),
                  's': pa.array([chr(97 + (i % 5)) for i in range(n)],
                                pa.large_string())})
    pq.write_table(t, path, row_group_size=rgr, compression='zstd',
                   compression_level=level)
    return path


def _rgs(path):
    md = pq.ParquetFile(path).metadata
    return [md.row_group(k).num_rows for k in range(md.num_row_groups)]


# ---------- 1. 内容摘要 (等价判据) ----------

def test_file_digest_ignores_encoding_but_catches_content_change(tmp_path):
    """同内容异行组/异压缩级 → 同摘要; 改一个值 / 截断一批 → 摘要必变。"""
    a = _mk(str(tmp_path / 'a.parquet'), n=300, rgr=100, level=1)
    b = _mk(str(tmp_path / 'b.parquet'), n=300, rgr=64, level=9)      # 异编码
    c = _mk(str(tmp_path / 'c.parquet'), n=300, rgr=100, level=1, shift=1)  # 值变
    d = _mk(str(tmp_path / 'd.parquet'), n=299, rgr=100, level=1)     # 截断
    da, na = CL.file_digest(a)
    db, nb = CL.file_digest(b)
    dc, _ = CL.file_digest(c)
    dd, nd = CL.file_digest(d)
    assert na == nb == 300 and nd == 299
    assert da == db                     # 编码无关: 内容等价 ⟺ 摘要相同
    assert dc != da and dd != da        # 内容变/截断必被抓
    assert _rgs(a) != _rgs(b)           # 前置: 两者确实是不同几何


# ---------- 2. 重写 + 校验 + 原子替换 ----------

def test_compact_file_rewrites_geometry_and_preserves_content(tmp_path):
    """重打包: 几何按目标 rgr 落 (含尾组), 行数/内容摘要与源全等, 无 tmp 残留。"""
    src = _mk(str(tmp_path / 'x' / '20260803.parquet'), n=300, rgr=100, level=1)
    src_digest, src_rows = CL.file_digest(src)
    info = CL.compact_file(src, rgr=64, level=9)
    assert info['rows'] == src_rows == 300
    assert info['digest'] == src_digest
    assert info['before_bytes'] > 0 and info['after_bytes'] > 0
    assert _rgs(src) == [64, 64, 64, 64, 44]           # 目标几何 + 尾组
    assert CL.file_digest(src) == (src_digest, src_rows)
    assert [f for f in os.listdir(os.path.dirname(src)) if '.tmp.' in f] == []


def test_compact_file_aborts_on_verify_mismatch_original_untouched(tmp_path):
    """校验不过 (注入损坏) → 抛 CompactError, 原文件字节不变, tmp 已清。

    注入 `_mutate` = 测试专用故障钩子 (写完 tmp、校验前调用), 模拟磁盘/编码异常。"""
    src = _mk(str(tmp_path / 'x' / '20260803.parquet'), n=300, rgr=100, level=1)
    before = open(src, 'rb').read()
    mtime = os.path.getmtime(src)

    def _corrupt(tmp):
        os.truncate(tmp, os.path.getsize(tmp) // 2)     # 截断: 行数/摘要必不符

    try:
        CL.compact_file(src, rgr=64, level=9, _mutate=_corrupt)
        raise AssertionError('应抛 CompactError')
    except CL.CompactError as e:
        assert '校验' in str(e)
    assert open(src, 'rb').read() == before            # 原文件未被替换
    assert os.path.getmtime(src) == mtime
    assert [f for f in os.listdir(os.path.dirname(src)) if '.tmp.' in f] == []


# ---------- 3. 文件清单 + 守卫 + CLI ----------

def test_plan_files_covers_tables_months_and_skips_missing(tmp_path):
    root = str(tmp_path / 'lob')
    _mk(f'{root}/lob_events/year=2026/month=08/20260803.parquet', n=10)
    _mk(f'{root}/lob_sweep_meta/year=2026/month=08/20260803.parquet', n=10)
    _mk(f'{root}/lob_events/year=2025/month=08/20250812.parquet', n=10)
    got = CL.plan_files(root, ['202608', '202509'])
    assert [os.path.basename(p) for _, p in got] == ['20260803.parquet',
                                                     '20260803.parquet']
    assert sorted(t for t, _ in got) == ['lob_events', 'lob_sweep_meta']
    assert CL.plan_files(root, ['202509']) == []       # 缺月 → 空清单 (不虚构)


def test_main_refuses_when_batch_lock_held(tmp_path):
    """批算占 flock 时拒绝重打包 (单写者): 返回非 0 且不改任何文件。"""
    root = str(tmp_path / 'lob')
    f = _mk(f'{root}/lob_events/year=2026/month=08/20260803.parquet', n=300)
    before = os.path.getsize(f)
    lock_p = tmp_path / '_batch' / '.lock'
    os.makedirs(lock_p.parent, exist_ok=True)
    import fcntl
    lf = open(lock_p, 'w')
    fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    rc = CL.main(['--root', root, '--months', '202608',
                  '--lock', str(lock_p), '--rgr', '64', '--level', '9'])
    assert rc == 3                                     # 3 = 锁占用拒绝
    assert os.path.getsize(f) == before


def test_main_dry_run_lists_without_touching(tmp_path):
    root = str(tmp_path / 'lob')
    f = _mk(f'{root}/lob_events/year=2026/month=08/20260803.parquet', n=300,
            rgr=100, level=1)
    before = open(f, 'rb').read()
    out = tmp_path / 'compact.json'
    rc = CL.main(['--root', root, '--months', '202608', '--lock',
                  str(tmp_path / 'nolock'), '--rgr', '64', '--level', '9',
                  '--dry-run', '--out', str(out)])
    assert rc == 0
    assert open(f, 'rb').read() == before              # 未动文件
    d = json.loads(out.read_text())
    assert d['dry_run'] is True and len(d['files']) == 1
    assert d['files'][0]['path'].endswith('20260803.parquet')


def test_main_compacts_and_records_before_after(tmp_path):
    """真实重打包: 报告含逐文件 before/after 字节与摘要相等标志; exit 0。"""
    root = str(tmp_path / 'lob')
    f = _mk(f'{root}/lob_events/year=2026/month=08/20260803.parquet', n=3000,
            rgr=100, level=1)
    out = tmp_path / 'compact.json'
    rc = CL.main(['--root', root, '--months', '202608', '--lock',
                  str(tmp_path / 'nolock'), '--rgr', '256', '--level', '9',
                  '--out', str(out)])
    assert rc == 0
    d = json.loads(out.read_text())
    e = d['files'][0]
    assert e['rows'] == 3000 and e['digest_equal'] is True
    assert e['after_bytes'] > 0
    assert _rgs(f) == [256] * 11 + [184]
    assert d['n_files'] == 1
    assert d['bytes_before'] == e['before_bytes'] > 0
    assert d['bytes_after'] == e['after_bytes'] > 0
    assert d['locked'] is False and d['dry_run'] is False


# ---------- 1b. null 槽底层值 (Arrow 未定义区) 不得进入内容判据 ----------

def _null_slot_table(null_val=0, n=200):
    """`i` 列 null 槽 (每 3 行第 2 行) 的底层值 = null_val —— 经 from_buffers 直构,
    绕开 pa.array(mask=) 的归零 (真实文件的 null 槽底层值就是这种未定义残留)。"""
    mask = [False, True, False] * (n // 3) + [False] * (n % 3)
    nv = [1, null_val, 3] * (n // 3) + [7] * (n % 3)
    validity = pa.array([not m for m in mask], pa.bool_()).buffers()[1]
    i = pa.Array.from_buffers(pa.int64(), n,
                              [validity, pa.array(nv, pa.int64()).buffers()[1]])
    s = pa.array(['a', 'ZZZ', 'c'] * (n // 3) + ['d'] * (n % 3), pa.large_string(),
                 mask=mask)
    return pa.table({'i': i, 's': s})


def test_canon_batch_ignores_undefined_null_slots_but_keeps_mask():
    """内容判据 = 逻辑内容: null 槽底层值是 Arrow **未定义区** (读回随编码器/路径变)
    → 同掩码同非 null 值 ⟹ 同判据; 掩码变或非 null 值变 ⟹ 判据必变。

    真实案例 (修复前误报): lob_events 20260803 `id` 列 692 个 null 槽
    src 底层 = 243683 / 重写件 = 0 → 摘要不等 → 重打包被拒 (属误报)。"""
    a, b = _null_slot_table(999), _null_slot_table(0)
    assert a['i'].chunk(0).buffers()[1].to_pybytes() != \
           b['i'].chunk(0).buffers()[1].to_pybytes()      # 前置: 未定义区确实不同
    assert a['i'].null_count == b['i'].null_count == 66
    assert CL._canon_batch(a) == CL._canon_batch(b)       # 仅未定义区不同 → 同判据
    c = pa.table({'i': pa.array([2, 999, 3] * 66 + [7, 7], pa.int64(),
                                mask=[False, True, False] * 66 + [False] * 2),
                  's': a['s']})
    assert CL._canon_batch(c) != CL._canon_batch(a)       # 非 null 值 1→2 → 判据变
    d = pa.table({'i': pa.array([1, 0, 3] * 66 + [7, 7], pa.int64()),
                  's': pa.array(['a', 'ZZZ', 'c'] * 66 + ['d', 'd'], pa.large_string())})
    assert CL._canon_batch(d) != CL._canon_batch(a)       # null 槽转非 null → 判据变
