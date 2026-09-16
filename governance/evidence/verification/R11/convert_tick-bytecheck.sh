#!/usr/bin/env bash
# R10 证据：convert_tick 编排切到 BatchFlock 前后——同一批真实 zip，逐字节 + 全列排序逐值对照。
set -euo pipefail
STOCK=/data/students/gaolei/stock
BASE=/tmp/r10c_base
BASEROOT=/tmp/r10c_base_root
NEWROOT=/tmp/r10c_new_root
DAY=${1:-20250812}
NCODES=${2:-20}

rm -rf "$BASE" "$BASEROOT" "$NEWROOT"
git -C "$STOCK" worktree add --detach "$BASE" HEAD >/dev/null 2>&1

SRC="$STOCK/data/raw/quark_downloaded/$DAY"
mapfile -t CODES < <(ls "$SRC" | head -"$NCODES")
echo "code 数: ${#CODES[@]}（$DAY）"
for R in "$BASEROOT" "$NEWROOT"; do
  D="$R/data/raw/quark_downloaded/$DAY"; mkdir -p "$D"
  for c in "${CODES[@]}"; do ln -s "$SRC/$c" "$D/$c"; done
done

for TAG in "切换前:$BASE:$BASEROOT" "切换后:$STOCK:$NEWROOT"; do
  NAME=${TAG%%:*}; DIR=${TAG#*:}; DIR=${DIR%:*}; ROOT=${TAG##*:}
  echo "== $NAME =="
  ( cd "$DIR" && FACTORLAB_STOCK_ROOT="$ROOT" \
    "$STOCK/platform/.venv/bin/python" research/tools/converters/convert_tick_to_parquet.py \
    --only-day "$DAY" --workers 6 ) 2>&1 | tail -2
done

echo "== 对照 =="
( cd "$BASEROOT/data/fact/tick_fact" && find . -name '*.parquet' | sort | xargs sha256sum ) > /tmp/r10c_old.sha
( cd "$NEWROOT/data/fact/tick_fact" && find . -name '*.parquet' | sort | xargs sha256sum ) > /tmp/r10c_new.sha
if diff -q /tmp/r10c_old.sha /tmp/r10c_new.sha >/dev/null; then
  echo "PASS：$(wc -l < /tmp/r10c_old.sha) 个 parquet sha256 全等（含行序）"
else
  echo "sha256 不同（行序随完成顺序）→ 全列排序逐值对照："
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
