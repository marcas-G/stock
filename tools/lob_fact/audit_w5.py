"""W5 全史批算收尾审计（只读；纯函数 + CLI）—— 计划 W5 验收四问单点

四问（对应计划文件 W5 验收行）：
  1. **完整性**：逐月 `state.json` 的 done vs 计划（计划 = conversion_manifest 该月
     去重 trade_date，与批算自身取计划同源）→ 缺失/越界（串月）逐条列出，不静默。
  2. **体积**：逐月 lob_fact 3 表 / tick_fact 4 表（**源 = tick_fact 月体积**，W4
     定标口径：5,355.6MB@202608）比率 ≤ 1.5；**源为 0 不判达标**（免 0 除静默）。
  3. **失败**：全量 code-day 分类 = clean / delta_band（δ 带，W4 双阶门吸收）/
     vacuous（无锚日）/ hard（gate.ok=False）；hard 率 ≤ 0.1% 且逐条列 code-day
     + reasons（"全分类"= 无未归类桶）。
  4. **内存**：全部 run 的 `rss_audit.csv` 三指标 —— 单 worker 峰 / 同刻 worker 和峰
     （W4 口径 22.7GB）/ 同刻全进程和峰；门 = 和峰 ≤48GB normal、全和峰 ≤64GB hard
     （**2026-09-11 用户决策翻倍**，原 24/32GB；翻倍后 worker 2→4）。

CLI::

    python audit_w5.py --batch-dir <lob_fact/_batch> --lob-root <lob_fact> \\
        --tick-root <tick_fact> [--manifest <conversion_manifest.parquet>] \\
        [--months 202508,202509] [--out audit.json]

exit 0 = 四问全过；1 = 有缺日/超预算/hard 超阈/内存超限（报告 JSON 恒落盘）。
"""
import argparse
import glob
import json
import os
import time
from collections import Counter, defaultdict

import polars as pl

LOB_TABLES = ('lob_events', 'lob_sweep_meta', 'lob_checkpoints')
SRC_TABLES = ('orders', 'trades', 'snapshots', 'cancels')
BUDGET_RATIO = 1.5                 # 计划: 总体积 ≤1.5×源
FAIL_RATE_MAX = 0.001              # 计划: 已知失败 <0.1% 有分类原因
# 用户硬约束 (2026-09-11 决策翻倍: 24/32GB → 48/64GB, 据此 worker 2→4;
# 前段 2-worker 实测峰 23.9/24.1GB 在原门内, 见 notes/w5_full_history_memo.md §5)
NORMAL_LIMIT_KB = 48 * 1024 * 1024  # 常态 ≤48GB
HARD_LIMIT_KB = 64 * 1024 * 1024    # 总驻留 ≤64GB


# ---------- 1. 完整性 ----------

def plan_dates(manifest_path, month):
    """计划日期 = conversion_manifest 该月去重 trade_date（批算同源）→ 升序 YYYYMMDD"""
    df = pl.read_parquet(manifest_path, columns=['trade_date'])
    want = df.filter(pl.col('trade_date').dt.strftime('%Y%m') == month)
    return sorted({d.strftime('%Y%m%d') for d in want['trade_date'].to_list()})


def read_state(batch_dir):
    p = os.path.join(batch_dir, 'state.json')
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return json.load(f)


def month_status(state, plan, month):
    """done vs plan：missing = 计划有未做（断点残留）；extra = 做了不在计划（串月）"""
    done = set((state.get('months') or {}).get(month, {}).get('done') or [])
    plan_s = set(plan)
    missing = sorted(plan_s - done)
    extra = sorted(done - plan_s)
    return dict(month=month, done_n=len(done), plan_n=len(plan_s),
                missing=missing, extra=extra,
                complete=(not missing and not extra))


# ---------- 2. 体积 ----------

def tree_bytes(root, tables, month):
    """逐表实际字节（apparent size）；缺表 = 0（显式，不当"有"）"""
    out = {}
    for t in tables:
        pat = os.path.join(root, t, f'year={month[:4]}', f'month={month[4:]}',
                           '**', '*.parquet')
        out[t] = sum(os.path.getsize(f) for f in glob.glob(pat, recursive=True))
    out['total'] = sum(out[t] for t in tables)
    return out


def volume_rows(lob_root, tick_root, months):
    rows = []
    for m in months:
        lob = tree_bytes(lob_root, LOB_TABLES, m)['total']
        src = tree_bytes(tick_root, SRC_TABLES, m)['total']
        ratio = (lob / src) if src else None
        rows.append(dict(month=m, lob_bytes=lob, src_bytes=src,
                         ratio=ratio,          # 全精度（展示层 render 再舍入）
                         budget_ok=bool(src and ratio <= BUDGET_RATIO)))
    return rows


# ---------- 3. 失败分类 ----------

def read_day_recs(runs_dir, months=None):
    """跨 run 聚合逐 code-day 记录（后写 run 覆盖前写；断点续跑语义）"""
    recs = {}
    for rp in sorted(glob.glob(os.path.join(runs_dir, '*', 'day_rows.jsonl'))):
        with open(rp) as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                p = json.loads(ln)
                day = p.get('day') or ''
                if months is not None and day[:6] not in months:
                    continue
                for r in p.get('recs') or []:
                    recs[(day[:6], r.get('code'), r.get('day') or day)] = r
    return recs


def classify(rec):
    """四类；hard 优先（band 标记不得吸收真失败）"""
    g = rec.get('gate') or {}
    if not rec.get('ok', True) or not g.get('ok', True):
        return 'hard'
    if g.get('vacuous'):
        return 'vacuous'
    if g.get('band'):
        return 'delta_band'
    return 'clean'


def failure_report(recs):
    kinds = Counter(classify(r) for r in recs.values())
    items = [dict(month=m, code=c, day=d, reasons=list(
        (recs[(m, c, d)].get('gate') or {}).get('reasons') or []))
        for (m, c, d), r in sorted(recs.items()) if classify(r) == 'hard']
    n_hard = len(items)
    n_total = len(recs)
    rate = (n_hard / n_total) if n_total else 0.0
    return dict(n_total=n_total, n_hard=n_hard, hard_rate=rate,  # 全精度（render 舍入）
                rate_ok=(rate <= FAIL_RATE_MAX),
                kinds={k: kinds.get(k, 0)
                       for k in ('clean', 'delta_band', 'vacuous', 'hard')},
                items=items)


# ---------- 4. 内存 ----------

def memory_report(runs_dir):
    """三指标（rss_audit.csv: t,pid,tag,rss_kb,mem_avail_kb; tag ∈ worker|parent）"""
    per_t = defaultdict(lambda: [0, 0])       # t → [worker_sum, all_sum]
    n_samples = 0
    peak_worker = 0
    for rp in sorted(glob.glob(os.path.join(runs_dir, '*', 'rss_audit.csv'))):
        with open(rp) as f:
            for i, ln in enumerate(f):
                if i == 0 and ln.startswith('t,'):
                    continue
                parts = ln.strip().split(',')
                if len(parts) < 4:
                    continue
                try:
                    t, rss = float(parts[0]), int(parts[3])
                except ValueError:
                    continue
                tag = parts[2]
                per_t[t][1] += rss
                if tag == 'worker':
                    per_t[t][0] += rss
                    n_samples += 1
                    peak_worker = max(peak_worker, rss)
    peak_conc = max([v[0] for v in per_t.values()], default=0)
    peak_total = max([v[1] for v in per_t.values()], default=0)
    return dict(n_samples=n_samples, n_timestamps=len(per_t),
                peak_worker_kb=peak_worker, peak_concurrent_kb=peak_conc,
                peak_total_kb=peak_total,
                normal_ok=(peak_conc <= NORMAL_LIMIT_KB),
                hard_ok=(peak_total <= HARD_LIMIT_KB))


# ---------- 5. 月 QA 摘要 ----------

def month_qa_rows(batch_dir, months):
    """逐月 QA 摘要 (计划 W5 验收行 "月 QA 摘要"): 月门 (`month_gate_{m}.json`:
    代码日/锚定/vacuous/band/池化 SZ·SH) + state 完成度 (done/plan/hard_days 数) +
    最近一次 run 的 parity (summary.json 按 run_id 字典序取最后 = 断点续跑语义)。
    缺件显式 None + qa_present=False (不静默补零)。"""
    state = read_state(batch_dir)
    runs_dir = os.path.join(batch_dir, 'runs')
    rows = []
    for m in months:
        p = os.path.join(batch_dir, f'month_gate_{m}.json')
        mg = None
        if os.path.exists(p):
            with open(p) as f:
                mg = json.load(f)
        g = (mg or {}).get('month_gate') or {}
        gate = g.get('gate') or {}
        st = (state.get('months') or {}).get(m) or {}
        parity, n_err = None, None
        for rp in sorted(glob.glob(os.path.join(runs_dir, '*', 'summary.json'))):
            try:
                with open(rp) as f:
                    s = json.load(f)
            except Exception:
                continue
            if s.get('month') == m:
                parity = (s.get('parity') or {}).get('ok')
                n_err = s.get('n_errors')
        rows.append(dict(
            month=m, qa_present=mg is not None,
            n_code_day=(mg or {}).get('n_code_day'),
            n_anchored=g.get('n_anchored'), n_vacuous=g.get('n_vacuous'),
            n_band=g.get('n_band'), sz=gate.get('sz'), sh=gate.get('sh'),
            gate_ok=gate.get('ok'),
            done_n=len(st.get('done') or []), plan_n=st.get('plan_n'),
            hard_n=len(st.get('hard_days') or []),
            parity_ok=parity, n_errors=n_err))
    return rows


# ---------- 汇总 ----------

def audit(batch_dir, lob_root, tick_root, manifest, months=None):
    state = read_state(batch_dir)
    if months is None:
        months = sorted((state.get('months') or {}))
    mrows = []
    for m in months:
        mrows.append(month_status(state, plan_dates(manifest, m), m))
    vol = volume_rows(lob_root, tick_root, months)
    runs_dir = os.path.join(batch_dir, 'runs')
    recs = read_day_recs(runs_dir, set(months) if months else None)
    fail = failure_report(recs)
    mem = memory_report(runs_dir)
    mqa = month_qa_rows(batch_dir, months)
    completeness_ok = all(r['complete'] for r in mrows)
    volume_ok = all(r['budget_ok'] for r in vol)
    ok = bool(completeness_ok and volume_ok and fail['rate_ok']
              and mem['normal_ok'] and mem['hard_ok'] and mrows)
    return dict(generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                batch_dir=batch_dir, months=mrows, volume=vol, failures=fail,
                memory=mem, month_qa=mqa, completeness_ok=completeness_ok,
                volume_ok=volume_ok, ok=ok)


def render(rep):
    L = [f'W5 收尾审计 {rep["generated_at"]} — ok={rep["ok"]}']
    L.append(f'[完整性] {"PASS" if rep["completeness_ok"] else "FAIL"}')
    for r in rep['months']:
        miss = f' 缺 {",".join(r["missing"])}' if r['missing'] else ''
        extra = f' 越界 {",".join(r["extra"])}' if r['extra'] else ''
        L.append(f'  {r["month"]}: done {r["done_n"]}/{r["plan_n"]}'
                 f'{" COMPLETE" if r["complete"] else " 未完成"}{miss}{extra}')
    L.append(f'[体积] {"PASS" if rep["volume_ok"] else "FAIL"} (预算 ≤'
             f'{BUDGET_RATIO}×源 = tick_fact)')
    for v in rep['volume']:
        rt = f'{v["ratio"]:.4f}×' if v['ratio'] is not None else '无源'
        L.append(f'  {v["month"]}: lob {v["lob_bytes"] / 2**20:.1f}MB vs 源 '
                 f'{v["src_bytes"] / 2**20:.1f}MB = {rt}'
                 f'{" ✓" if v["budget_ok"] else " ✗"}')
    f = rep['failures']
    L.append(f'[失败] {"PASS" if f["rate_ok"] else "FAIL"} hard {f["n_hard"]}/'
             f'{f["n_total"]} = {f["hard_rate"]:.4%} (阈 ≤{FAIL_RATE_MAX:.1%}) '
             f'分类 {f["kinds"]}')
    for it in f['items']:
        L.append(f'  {it["code"]}@{it["day"]} reasons={it["reasons"]}')
    L.append('[月 QA] 逐月 (代码日/锚定/vacuous/band/池化 SZ·SH/done·plan/hard/parity)')
    for r in rep['month_qa']:
        if not r['qa_present']:
            L.append(f'  {r["month"]}: 无月门文件 (未完成或未生成)')
            continue
        L.append(f'  {r["month"]}: cd={r["n_code_day"]} 锚定={r["n_anchored"]} '
                 f'vac={r["n_vacuous"]} band={r["n_band"]} SZ={r["sz"]} SH={r["sh"]} '
                 f'done={r["done_n"]}/{r["plan_n"]} hard={r["hard_n"]} '
                 f'parity={r["parity_ok"]} err={r["n_errors"]}')
    m = rep['memory']
    L.append(f'[内存] normal {"PASS" if m["normal_ok"] else "FAIL"} / hard '
             f'{"PASS" if m["hard_ok"] else "FAIL"} — 单 worker 峰 '
             f'{m["peak_worker_kb"] / 2**20:.1f}GB, 同刻 worker 和峰 '
             f'{m["peak_concurrent_kb"] / 2**20:.1f}GB (≤24GB), 同刻全和峰 '
             f'{m["peak_total_kb"] / 2**20:.1f}GB (≤32GB), {m["n_samples"]} 样本')
    return '\n'.join(L)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--batch-dir',
                    default='/data/students/gaolei/stock/lob_fact/_batch')
    ap.add_argument('--lob-root', default='/data/students/gaolei/stock/lob_fact')
    ap.add_argument('--tick-root', default='/data/students/gaolei/stock/tick_fact')
    ap.add_argument('--manifest', default=None,
                    help='conversion_manifest.parquet（默认 <tick-root>/_manifest/）')
    ap.add_argument('--months', default=None, help='逗号分隔 YYYYMM（默认 state 全月）')
    ap.add_argument('--out', default=None, help='报告 JSON 路径')
    a = ap.parse_args(argv)
    manifest = a.manifest or os.path.join(
        a.tick_root, '_manifest', 'conversion_manifest.parquet')
    months = a.months.split(',') if a.months else None
    rep = audit(a.batch_dir, a.lob_root, a.tick_root, manifest, months)
    print(render(rep))
    if a.out:
        with open(a.out, 'w') as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
        print(f'report -> {a.out}')
    return 0 if rep['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
