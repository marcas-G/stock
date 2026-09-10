"""W5 收尾审计 TDD（先红后绿）：完整性 / 体积 / 失败分类 / 内存四问

断言源 = 计划文件 W5 验收行（断点续跑全完成、月 QA 摘要、总体积 ≤1.5×源、失败全分类、
内存审计全程在限）+ W4 实测定标（源 = tick_fact 4 表月体积 5,355.6MB@202608；
24GB normal / 32GB hard）。构造全为**真实文件**（真 parquet / 真 jsonl / 真 csv），
断言逐字节数与逐样本手算值 —— 任何"返回常量/汇总硬编码"的存根必败。
"""
import json
import os
import sys

import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import audit_w5 as AU

MB = 1 << 20


# ---------- 构造器（真实文件） ----------

def _write_parquet(path, n_rows=8, cols=('a', 'b')):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pl.DataFrame({c: list(range(n_rows)) for c in cols}).write_parquet(path)
    return os.path.getsize(path)


def _manifest(tmp_path, pairs):
    """pairs = [(code, 'YYYY-MM-DD'), ...] → 真 conversion_manifest.parquet"""
    p = tmp_path / 'man' / 'conversion_manifest.parquet'
    os.makedirs(p.parent, exist_ok=True)
    from datetime import date as _d
    pl.DataFrame({
        'code': [c for c, _ in pairs],
        'trade_date': [_d(int(d[:4]), int(d[5:7]), int(d[8:10])) for _, d in pairs],
    }).write_parquet(p)
    return str(p)


def _state(tmp_path, months):
    p = tmp_path / '_batch' / 'state.json'
    os.makedirs(p.parent, exist_ok=True)
    with open(p, 'w') as f:
        json.dump({'months': months}, f)
    return str(p.parent)


def _lob(tmp_path, layout):
    """layout = {'202608': {'lob_events': n, ...}} → 真 parquet（year/month 分区）"""
    root = tmp_path / 'lob'
    for month, tables in layout.items():
        for t, n in tables.items():
            _write_parquet(str(root / t / f'year={month[:4]}' / f'month={month[4:]}' /
                               'part-000.parquet'), n_rows=n)
    return str(root)


def _tick(tmp_path, layout):
    root = tmp_path / 'tick'
    for month, tables in layout.items():
        for t, n in tables.items():
            _write_parquet(str(root / t / f'year={month[:4]}' / f'month={month[4:]}' /
                               'part-000.parquet'), n_rows=n)
    return str(root)


def _day_rows(tmp_path, run_id, days):
    """days = [{'day': 'YYYYMMDD', 'recs': [rec, ...], ...}, ...]"""
    p = tmp_path / '_batch' / 'runs' / run_id
    os.makedirs(p, exist_ok=True)
    with open(p / 'day_rows.jsonl', 'w') as f:
        for d in days:
            f.write(json.dumps(d) + '\n')
    return p


def _rss(tmp_path, run_id, rows):
    """rows = [(t, pid, tag, rss_kb), ...]"""
    p = tmp_path / '_batch' / 'runs' / run_id
    os.makedirs(p, exist_ok=True)
    with open(p / 'rss_audit.csv', 'w') as f:
        f.write('t,pid,tag,rss_kb,mem_avail_kb\n')
        for t, pid, tag, rss in rows:
            f.write(f'{t},{pid},{tag},{rss},99000000\n')
    return p


def _rec(code, day, ok=True, vacuous=False, band=False, reasons=(), notes=()):
    return {'code': code, 'day': day, 'ok': ok,
            'gate': {'presence': 0.99, 'vacuous': vacuous, 'band': band,
                     'ok': ok, 'reasons': list(reasons), 'notes': list(notes)},
            'm4': {'conservation': 'PASS', 'orders': 0, 'counters_equal': True}}


# ---------- 1. 完整性: plan 与 done ----------

def test_plan_dates_lists_only_requested_month_sorted_unique(tmp_path):
    mp = _manifest(tmp_path, [
        ('000001.SZ', '2026-08-03'), ('000001.SZ', '2026-08-04'),
        ('600000.SH', '2026-08-04'), ('000001.SZ', '2025-08-12'),
    ])
    assert AU.plan_dates(mp, '202608') == ['20260803', '20260804']
    assert AU.plan_dates(mp, '202508') == ['20250812']
    assert AU.plan_dates(mp, '202607') == []


def test_month_status_reports_missing_extra_and_complete(tmp_path):
    plan = ['20260803', '20260804', '20260805']
    st = {'months': {'202608': {'done': ['20260803', '20260804', '20260809'],
                                'plan_n': 3}}}
    r = AU.month_status(st, plan, '202608')
    assert r['done_n'] == 3 and r['plan_n'] == 3
    assert r['missing'] == ['20260805']          # 计划有、未做
    assert r['extra'] == ['20260809']            # 做了但不在计划（防串月）
    assert r['complete'] is False
    st2 = {'months': {'202608': {'done': list(plan), 'plan_n': 3}}}
    r2 = AU.month_status(st2, plan, '202608')
    assert r2['complete'] is True and r2['missing'] == [] and r2['extra'] == []


# ---------- 2. 体积: lob 3 表 vs tick_fact 4 表（W4 侧定标源定义） ----------

def test_tree_bytes_sums_real_files_exactly(tmp_path):
    root = _lob(tmp_path, {'202608': {'lob_events': 10, 'lob_sweep_meta': 4}})
    got = AU.tree_bytes(root, AU.LOB_TABLES, '202608')
    want_ev = os.path.getsize(os.path.join(
        root, 'lob_events', 'year=2026', 'month=08', 'part-000.parquet'))
    want_sw = os.path.getsize(os.path.join(
        root, 'lob_sweep_meta', 'year=2026', 'month=08', 'part-000.parquet'))
    assert got['lob_events'] == want_ev > 0
    assert got['lob_sweep_meta'] == want_sw > 0
    assert got['lob_checkpoints'] == 0            # 缺表 → 0，不静默当"有"
    assert got['total'] == want_ev + want_sw


def test_volume_rows_ratio_uses_tick_fact_as_source_and_flags_budget(tmp_path):
    lob = _lob(tmp_path, {'202608': {'lob_events': 16, 'lob_sweep_meta': 16}})
    tick = _tick(tmp_path, {'202608': {'orders': 8, 'trades': 8, 'snapshots': 8,
                                       'cancels': 8}})
    rows = AU.volume_rows(lob, tick, ['202608'])
    assert len(rows) == 1
    r = rows[0]
    assert r['lob_bytes'] > 0 and r['src_bytes'] > 0
    assert abs(r['ratio'] - r['lob_bytes'] / r['src_bytes']) < 1e-9
    assert r['budget_ok'] is (r['ratio'] <= AU.BUDGET_RATIO)
    # 空源 → 不判"达标"，标记无源（免 0 除静默）
    rows0 = AU.volume_rows(lob, _tick(tmp_path / 'x', {}), ['202608'])
    assert rows0[0]['src_bytes'] == 0 and rows0[0]['budget_ok'] is False


# ---------- 3. 失败分类 ----------

def test_classify_code_day_four_kinds():
    assert AU.classify(_rec('000001.SZ', '20260803')) == 'clean'
    assert AU.classify(_rec('000001.SZ', '20260803', band=True,
                            notes=['m1a_delta_band'])) == 'delta_band'
    assert AU.classify(_rec('000001.SZ', '20260803', vacuous=True)) == 'vacuous'
    assert AU.classify(_rec('301308.SZ', '20260807', ok=False,
                            reasons=['m1a_presence'])) == 'hard'
    # band 与 hard 同现 → hard 优先（不得被 band 吸收）
    assert AU.classify(_rec('301308.SZ', '20260807', ok=False, band=True,
                            reasons=['m1a_presence'])) == 'hard'


def test_failure_report_rate_threshold_and_items():
    recs = {}
    for i in range(999):
        recs[('202608', f'00000{i}.SZ', '20260803')] = _rec(f'00000{i}.SZ', '20260803')
    recs[('202608', '301308.SZ', '20260807')] = _rec(
        '301308.SZ', '20260807', ok=False, reasons=['m1a_presence'])
    rep = AU.failure_report(recs)
    assert rep['n_total'] == 1000 and rep['n_hard'] == 1
    assert rep['hard_rate'] == 0.001 and rep['rate_ok'] is True   # ≤0.1% 边界含等号
    assert rep['items'] == [{'month': '202608', 'code': '301308.SZ',
                             'day': '20260807', 'reasons': ['m1a_presence']}]
    recs[('202608', '600000.SH', '20260804')] = _rec(
        '600000.SH', '20260804', ok=False, reasons=['m1a_presence'])
    rep2 = AU.failure_report(recs)
    assert rep2['n_hard'] == 2 and rep2['n_total'] == 1001
    assert rep2['hard_rate'] == 2 / 1001          # 全精度（不预先舍入）
    assert rep2['hard_rate'] > AU.FAIL_RATE_MAX   # 2/1001 > 0.1%
    assert rep2['rate_ok'] is False
    assert [i['code'] for i in rep2['items']] == ['301308.SZ', '600000.SH']  # 确定性序


# ---------- 4. 内存审计 ----------

def test_memory_report_peaks_single_and_concurrent(tmp_path):
    _rss(tmp_path, 'r1', [
        (1, 100, 'worker', 8_000_000), (1, 101, 'worker', 9_000_000),
        (1, 99, 'parent', 200_000),
        (2, 100, 'worker', 12_000_000), (2, 99, 'parent', 210_000),
    ])
    rep = AU.memory_report(str(tmp_path / '_batch' / 'runs'))
    assert rep['n_samples'] == 3                       # 仅 worker 行参与峰值
    assert rep['peak_worker_kb'] == 12_000_000         # 单 worker 峰 (t=2)
    assert rep['peak_concurrent_kb'] == 17_000_000     # t=1 同刻两 worker 和
    assert rep['peak_total_kb'] == 17_200_000          # t=1 含 parent (W4 口径外延)
    assert rep['normal_ok'] is True and rep['hard_ok'] is True
    # 用户 2026-09-11 决策: 内存预算翻倍 (常态 24→48GB / 硬 32→64GB) → worker 2→4
    _rss(tmp_path, 'r2', [(5, 200, 'worker', 48 * 1024 * 1024)])
    rep2 = AU.memory_report(str(tmp_path / '_batch' / 'runs'))
    assert rep2['peak_worker_kb'] == 48 * 1024 * 1024
    assert rep2['normal_ok'] is True                   # 恰等 48GB → 含等号在限
    _rss(tmp_path, 'r3', [(6, 300, 'worker', 48 * 1024 * 1024 + 1)])
    assert AU.memory_report(str(tmp_path / '_batch' / 'runs'))['normal_ok'] is False
    # 冻结新门: 改小 = 静默放松验收 (存根式改动必败)
    assert AU.NORMAL_LIMIT_KB == 48 * 1024 * 1024
    assert AU.HARD_LIMIT_KB == 64 * 1024 * 1024


def test_render_gate_labels_track_constants_not_hardcoded(tmp_path, monkeypatch):
    """render 的 (≤XGB) 标注必须取自 NORMAL/HARD_LIMIT_KB 常量 —— 预算翻倍 (2026-09-11)
    后硬编码旧标签会把 37GB 实测峰显示成"超 24GB 门"的假象（显示与判据脱节；
    硬编码标签的实现在本测试下必败）。"""
    _rss(tmp_path, 'r1', [(1, 100, 'worker', 37 * 1024 * 1024)])
    rep = dict(generated_at='t', ok=True, completeness_ok=True, months=[],
               volume_ok=True, volume=[],
               failures=dict(rate_ok=True, n_hard=0, n_total=0, hard_rate=0.0,
                             kinds={}, items=[]),
               month_qa=[],
               memory=AU.memory_report(str(tmp_path / '_batch' / 'runs')))
    monkeypatch.setattr(AU, 'NORMAL_LIMIT_KB', 48 * 1024 * 1024)
    monkeypatch.setattr(AU, 'HARD_LIMIT_KB', 64 * 1024 * 1024)
    out = AU.render(rep)
    assert '(≤48GB)' in out and '(≤64GB)' in out          # 标注 == 判据常量
    assert '24GB' not in out and '32GB' not in out        # 旧门标签不得残留
    assert rep['memory']['normal_ok'] is True             # 37GB ≤48GB 在限
    assert rep['memory']['hard_ok'] is True


# ---------- 5. 端到端 + CLI ----------

def _fixture(tmp_path, lob_n=16, src_n=8, hard=(), band=(), missing=()):
    mp = _manifest(tmp_path, [('000001.SZ', '2026-08-03'),
                              ('000001.SZ', '2026-08-04'),
                              ('000001.SZ', '2026-08-05')])
    _state(tmp_path, {'202608': {'done': ['20260803', '20260804'],
                                 'plan_n': 3}})
    lob = _lob(tmp_path, {'202608': {'lob_events': lob_n, 'lob_sweep_meta': 4,
                                     'lob_checkpoints': 4}})
    tick = _tick(tmp_path, {'202608': {'orders': src_n, 'trades': src_n,
                                       'snapshots': src_n, 'cancels': src_n}})
    recs = []
    for c, d, rs in hard:
        recs.append(_rec(c, d, ok=False, reasons=rs))
    for c, d in band:
        recs.append(_rec(c, d, band=True, notes=['m1a_delta_band']))
    for c, d in missing:
        recs.append(_rec(c, d, vacuous=True))
    if recs:
        _day_rows(tmp_path, 'r1', [{'day': '20260803', 'ok': True,
                                    'errors': [], 'n_codes': len(recs),
                                    'recs': recs}])
    _rss(tmp_path, 'r1', [(1, 100, 'worker', 1_000_000)])
    return dict(batch_dir=str(tmp_path / '_batch'),
                lob_root=lob, tick_root=tick, manifest=mp,
                months=['202608'])


def test_audit_end_to_end_flags_missing_dates_and_hard_failures(tmp_path):
    cfg = _fixture(tmp_path,
                   hard=[('301308.SZ', '20260807', ['m1a_presence'])],
                   band=[('000155.SZ', '20260803')])
    rep = AU.audit(**cfg)
    assert rep['months'][0]['month'] == '202608'
    assert rep['months'][0]['missing'] == ['20260805']      # 计划 3 做 2
    assert rep['completeness_ok'] is False
    assert rep['failures']['n_hard'] == 1
    assert rep['failures']['kinds']['delta_band'] == 1
    assert rep['ok'] is False


def test_audit_clean_tree_is_ok_and_cli_exit_zero(tmp_path):
    cfg = _fixture(tmp_path)
    # 补足第 3 日 → 完整性过
    _state(tmp_path, {'202608': {'done': ['20260803', '20260804', '20260805'],
                                 'plan_n': 3}})
    out = tmp_path / 'audit.json'
    rc = AU.main(['--batch-dir', cfg['batch_dir'], '--lob-root', cfg['lob_root'],
                  '--tick-root', cfg['tick_root'], '--manifest', cfg['manifest'],
                  '--months', '202608', '--out', str(out)])
    assert rc == 0
    d = json.loads(out.read_text())
    assert d['ok'] is True and d['completeness_ok'] is True
    assert d['volume'][0]['ratio'] > 0
    # 无失败记录 → 0 硬失败（不虚报）
    assert d['failures']['n_hard'] == 0


def test_cli_exit_one_when_hard_failure_or_over_budget(tmp_path):
    # (a) 硬失败 → exit 1
    cfg = _fixture(tmp_path / 'a',
                   hard=[('301308.SZ', '20260807', ['m1a_presence'])])
    _state(tmp_path / 'a', {'202608': {'done': ['20260803', '20260804',
                                                '20260805'], 'plan_n': 3}})
    rc = AU.main(['--batch-dir', cfg['batch_dir'], '--lob-root', cfg['lob_root'],
                  '--tick-root', cfg['tick_root'], '--manifest', cfg['manifest'],
                  '--months', '202608', '--out', str(tmp_path / 'a.json')])
    assert rc == 1
    d = json.loads((tmp_path / 'a.json').read_text())
    assert d['ok'] is False and d['failures']['n_hard'] == 1

    # (b) 体积超预算（lob 巨大 vs 源极小）→ exit 1，且 budget_ok 明确 False
    cfg2 = _fixture(tmp_path / 'b', lob_n=3000, src_n=8)
    _state(tmp_path / 'b', {'202608': {'done': ['20260803', '20260804',
                                                '20260805'], 'plan_n': 3}})
    rc2 = AU.main(['--batch-dir', cfg2['batch_dir'], '--lob-root', cfg2['lob_root'],
                   '--tick-root', cfg2['tick_root'], '--manifest', cfg2['manifest'],
                   '--months', '202608', '--out', str(tmp_path / 'b.json')])
    d2 = json.loads((tmp_path / 'b.json').read_text())
    assert d2['volume'][0]['ratio'] > AU.BUDGET_RATIO
    assert d2['volume'][0]['budget_ok'] is False
    assert d2['volume_ok'] is False and d2['ok'] is False
    assert rc2 == 1                          # 退出码与报告一致（不脱钩）


# ---------- 6. 月 QA 摘要 (W5 验收 "月 QA 摘要") ----------

def _summary(tmp_path, run_id, month, parity_ok=None, n_err=0):
    p = tmp_path / '_batch' / 'runs' / run_id
    os.makedirs(p, exist_ok=True)
    with open(p / 'summary.json', 'w') as f:
        json.dump({'month': month, 'plan_n': 3, 'n_done_run': 3,
                   'n_errors': n_err, 'errors': [], 'hard_days': [],
                   'parity': {'compared': parity_ok is not None,
                              'mismatch_dates': [], 'ok': parity_ok}}, f)
    return p


def _month_gate(tmp_path, month, sz, sh, n_band, n_cd=100, n_vac=0):
    p = tmp_path / '_batch' / f'month_gate_{month}.json'
    os.makedirs(p.parent, exist_ok=True)
    with open(p, 'w') as f:
        json.dump({'month': month, 'n_code_day': n_cd,
                   'month_gate': {'n_days': n_cd, 'n_anchored': n_cd - n_vac,
                                  'n_vacuous': n_vac, 'n_band': n_band,
                                  'gate': {'sz': sz, 'sh': sh, 'ok': True},
                                  'ok': True},
                   'n_dates_done': 3, 'plan_n': 3}, f)
    return str(p)


def test_month_qa_rows_reads_gate_state_and_parity(tmp_path):
    """月 QA 摘要逐月: 月门 (代码日/锚定/vacuous/band/池化 SZ·SH) + state 完成度
    (含 hard_days 数) + 最近一次 run 的 parity。数值逐条手算, 缺件显式 None。"""
    _month_gate(tmp_path, '202508', 0.97531, 0.99012, 7, n_cd=2343, n_vac=2)
    _state(tmp_path, {'202508': {'done': ['20250812', '20250813'],
                                 'plan_n': 2,
                                 'hard_days': [dict(day='20250814', n_fail=1,
                                                    codes=['301308.SZ'],
                                                    reasons=['m1a_presence'])]}})
    _summary(tmp_path, '20260910_110712_5974', '202508', parity_ok=True)
    rows = AU.month_qa_rows(str(tmp_path / '_batch'), ['202508', '202509'])
    assert len(rows) == 2
    r = rows[0]
    assert r['month'] == '202508' and r['qa_present'] is True
    assert r['n_code_day'] == 2343 and r['n_anchored'] == 2341
    assert r['n_vacuous'] == 2 and r['n_band'] == 7
    assert r['sz'] == 0.97531 and r['sh'] == 0.99012
    assert r['done_n'] == 2 and r['plan_n'] == 2 and r['hard_n'] == 1
    assert r['parity_ok'] is True and r['n_errors'] == 0
    r2 = rows[1]
    assert r2['month'] == '202509' and r2['qa_present'] is False
    assert r2['sz'] is None and r2['n_code_day'] is None and r2['parity_ok'] is None


def test_month_qa_rows_take_latest_run_parity(tmp_path):
    """同月多 run: 取字典序最后 (最近) 的 summary.json — 断点续跑语义下 parity
    看最后一次; 缺 summary 的 run 不参与 (不虚报 None 为 True)。"""
    _month_gate(tmp_path, '202608', 0.98602, 0.99175, 426)
    _summary(tmp_path, '20260910_054209_7521', '202608', parity_ok=None)
    _summary(tmp_path, '20260910_082834_33436', '202608', parity_ok=True)
    rows = AU.month_qa_rows(str(tmp_path / '_batch'), ['202608'])
    assert rows[0]['parity_ok'] is True
    _summary(tmp_path, '20260910_120000_999', '202608', parity_ok=False)
    rows2 = AU.month_qa_rows(str(tmp_path / '_batch'), ['202608'])
    assert rows2[0]['parity_ok'] is False


def test_audit_report_includes_month_qa_table(tmp_path):
    """审计报告含月 QA 段 (plan 验收 "月 QA 摘要"), 且 CLI 输出渲染出来。"""
    cfg = _fixture(tmp_path)
    _state(tmp_path, {'202608': {'done': ['20260803', '20260804', '20260805'],
                                 'plan_n': 3}})
    _month_gate(tmp_path, '202608', 0.98, 0.99, 3)
    _summary(tmp_path, 'r9', '202608', parity_ok=True)
    rep = AU.audit(**cfg)
    assert rep['month_qa'][0]['month'] == '202608'
    assert rep['month_qa'][0]['n_band'] == 3
    txt = AU.render(rep)
    assert '月 QA' in txt and '0.98' in txt
