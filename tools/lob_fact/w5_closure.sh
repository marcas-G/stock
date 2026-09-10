#!/bin/bash
# W5 收口 runbook a-e（备忘 tools/lob_fact/notes/w5_full_history_memo.md §6 的可执行形态）
# 前置：driver 打印 ALL DONE 且无 run_lob_batch 进程（单写者 flock 纪律）。
# 日志：/tmp/w5_closure.log；产物：/tmp/w5_compact.json、/tmp/w5_audit.json
set -u
cd /data/students/gaolei/stock/quant-platform-research/tools/lob_fact || exit 1
PY=/data/students/gaolei/anaconda3/envs/emb/bin/python
LOG=/tmp/w5_closure.log
exec >>"$LOG" 2>&1
echo "=== closure start $(date '+%F %T')"

# ---- 前置守卫 ----
if ! grep -q "ALL DONE" /tmp/w5_driver.log 2>/dev/null; then
    echo "ABORT: /tmp/w5_driver.log 未见 ALL DONE"; exit 2
fi
if pgrep -f "run_lob_batch" >/dev/null 2>&1; then
    echo "ABORT: run_lob_batch 进程仍在（单写者纪律：compact 前必须全退）"; exit 3
fi
if [ -f /tmp/w5_PAUSED_DISK.flag ]; then
    echo "ABORT: 磁盘守护暂停中（先清空间再 resume）"; exit 4
fi

# ---- a. 202608 补跑（todo={20260807} → hard 入账 done 15/15；跨 run 月门聚合首次真实演练）----
nice -n 19 $PY run_lob_batch.py --month 202608 --workers 1
echo "--- a: 202608 catch-up exit=$?"

# ---- b. 断点证据（应打印 plan=15 done=15 todo=0）----
$PY run_lob_batch.py --month 202608 --dry-run
echo "--- b: dry-run exit=$?"

# ---- c. 体积收口 202508+202608 → zstd9（87 文件 / 14,720.2 MiB 预核；内容摘要不等即拒）----
nice -n 19 $PY compact_lob.py --months 202508,202608 --rgr 1048576 --level 9 \
    --lock /data/students/gaolei/stock/lob_fact/_batch/.lock --out /tmp/w5_compact.json
echo "--- c: compact exit=$?"

# ---- d. 终审四问（exit 0 = 全 PASS；报告恒落盘）----
$PY audit_w5.py --out /tmp/w5_audit.json
echo "--- d: audit exit=$?"

# ---- e. 20260807 内容摘要复算（lv3 重写 lv9 后应与 /tmp/w5_20260807_pre.json 全等）----
$PY - <<'PYEOF'
import json, os, compact_lob as C
pre = json.load(open('/tmp/w5_20260807_pre.json'))['files']
root = '/data/students/gaolei/stock/lob_fact'
post, ok = {}, True
for t in C.LOB_TABLES:
    p = os.path.join(root, t, 'year=2026', 'month=08', '20260807.parquet')
    d, n = C.file_digest(p)
    same = (d == pre[t]['digest'] and n == pre[t]['rows'])
    ok = ok and same
    post[t] = dict(rows=n, bytes=os.path.getsize(p), digest=d)
    print(f'{t:18s} rows={n:>12,} digest={d[:16]}…  vs-pre: {"EQUAL" if same else "DIFF"}')
json.dump(dict(pre=pre, post=post, content_equal=ok),
          open('/tmp/w5_20260807_post.json', 'w'), ensure_ascii=False, indent=1)
print(f'20260807 补跑后内容摘要 vs 前置: {"全等 PASS" if ok else "不等 FAIL"}')
PYEOF
echo "--- e: digest exit=$?"
echo "=== closure done $(date '+%F %T')  (a/b/c/d/e 各步 exit 见上)"
