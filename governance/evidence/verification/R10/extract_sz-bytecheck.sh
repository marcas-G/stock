#!/usr/bin/env bash
# R10 证据：extract_sz_cancels 编排切到 BatchFlock 前后——同一批真实 SZ zip，
# 两个独立输出根，逐字节 + 全列排序后逐值对照。
set -euo pipefail
STOCK=/data/students/gaolei/stock
BASE=/tmp/r10_base                 # HEAD（切换前）的干净 checkout
BASEROOT=/tmp/r10_base_root
NEWROOT=/tmp/r10_new_root
DAY=${1:-20250812}
NCODES=${2:-30}

rm -rf "$BASE" "$BASEROOT" "$NEWROOT"
git -C "$STOCK" worktree add --detach "$BASE" HEAD >/dev/null 2>&1

SRC="$STOCK/data/raw/quark_downloaded/$DAY"
# 目录名 = 6 位 code；SZ 的 zip 为 <code>.SZ.zip（交易所由 zip 后缀定，不看前缀）
mapfile -t CODES < <(ls "$SRC" | head -400 | while read -r c; do
  [ -f "$SRC/$c/$c.SZ.zip" ] && echo "$c"; done | head -"$NCODES")
echo "SZ code 数: ${#CODES[@]}（$DAY）"
for R in "$BASEROOT" "$NEWROOT"; do
  D="$R/data/raw/quark_downloaded/$DAY"; mkdir -p "$D"
  for c in "${CODES[@]}"; do ln -s "$SRC/$c" "$D/$c"; done
done

echo "== 切换前（HEAD）=="
( cd "$BASE" && FACTORLAB_STOCK_ROOT="$BASEROOT" \
  "$STOCK/platform/.venv/bin/python" research/tools/lob_fact/pipeline/extract_sz_cancels.py \
  --only-day "$DAY" --workers 4 ) 2>&1 | tail -3
echo "== 切换后（当前工作树）=="
( cd "$STOCK" && FACTORLAB_STOCK_ROOT="$NEWROOT" \
  platform/.venv/bin/python research/tools/lob_fact/pipeline/extract_sz_cancels.py \
  --only-day "$DAY" --workers 4 ) 2>&1 | tail -3

echo "== parquet 清单与 sha256 =="
for R in "$BASEROOT" "$NEWROOT"; do
  ( cd "$R/data/fact/tick_fact" && find . -name '*.parquet' | sort | xargs sha256sum ) > "/tmp/r10_$(basename $R).sha"
done
if diff -q /tmp/r10_base_root.sha /tmp/r10_new_root.sha >/dev/null; then
  echo "PASS：$(wc -l < /tmp/r10_base_root.sha) 个 parquet sha256 全等（含行序）"
else
  echo "字节不同（预期：行序随完成顺序）→ 做全列排序逐值对照"
  "$STOCK/platform/.venv/bin/python" - "$BASEROOT" "$NEWROOT" <<'PY'
import sys, pathlib
import polars as pl
a_root, b_root = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
fa = sorted((a_root / "data/fact/tick_fact").rglob("*.parquet"))
fb = sorted((b_root / "data/fact/tick_fact").rglob("*.parquet"))
assert len(fa) == len(fb), (len(fa), len(fb))
bad = []
for x, y in zip(fa, fb):
    a, b = pl.read_parquet(x), pl.read_parquet(y)
    if a.height != b.height:
        bad.append((str(x), f"行数 {a.height} vs {b.height}")); continue
    if not a.sort(a.columns).equals(b.sort(b.columns)):
        bad.append((str(x), "全列排序后仍不等"))
    else:
        print(f"  ✓ 内容逐值相等（行序不同）: {x.relative_to(a_root)}  行数={a.height}")
print("FAIL" if bad else "PASS：内容逐值等价")
for x, why in bad:
    print(f"  [FAIL] {x}: {why}")
raise SystemExit(1 if bad else 0)
PY
fi
git -C "$STOCK" worktree remove --force "$BASE" >/dev/null 2>&1 || true
