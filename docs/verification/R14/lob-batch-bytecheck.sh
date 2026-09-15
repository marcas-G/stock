#!/usr/bin/env bash
# R14 证据：run_lob_batch 编排切到 P-5（BatchFlock）前后——真实 tick 输入、单日批算，
# 两个独立 LOB 输出根，逐字节 + 全列排序逐值对照。
set -euo pipefail
STOCK=/data/students/gaolei/stock
BASE=/tmp/r14_base
BASEROOT=/tmp/r14_base_root
NEWROOT=/tmp/r14_new_root
MONTH=${1:-202608}
DAY=${2:-20260803}
WORKERS=${3:-2}

rm -rf "$BASE" "$BASEROOT" "$NEWROOT"
git -C "$STOCK" worktree add --detach "$BASE" HEAD >/dev/null 2>&1

for R in "$BASEROOT" "$NEWROOT"; do
  mkdir -p "$R/data/fact"
  ln -s "$STOCK/data/fact/tick_fact" "$R/data/fact/tick_fact"   # 只读输入（符号链接）
done
# 基线补自举（HEAD 的 run_lob_batch 直接跑不了：路径自举写在 docstring 里，见 R14 证据 §偏差）
python3 - "$BASE" <<'PYEOF'
import pathlib, sys
p = pathlib.Path(sys.argv[1]) / "research/tools/lob_fact/pipeline/run_lob_batch.py"
s = p.read_text(encoding="utf-8")
s = s.replace("import os\n\n# ---- 线程上限", "import os\nimport sys\n\n# ---- 线程上限", 1)
s = s.replace("from core.config import GATE_PRES",
              "sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"
              "sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(\n"
              "    os.path.abspath(__file__)))))\n\nfrom core.config import GATE_PRES", 1)
p.write_text(s, encoding="utf-8")
print("基线已补自举")
PYEOF

run() {  # $1=代码目录 $2=根
  ( cd "$1" && FACTORLAB_STOCK_ROOT="$2" \
    "$STOCK/platform/.venv/bin/python" research/tools/lob_fact/pipeline/run_lob_batch.py \
    --month "$MONTH" --only-day "$DAY" --workers "$WORKERS" ) 2>&1 | tail -4
}

echo "== 切换前（HEAD：自建循环）=="; run "$BASE" "$BASEROOT"
echo "== 切换后（BatchFlock 单点）=="; run "$STOCK" "$NEWROOT"

echo "== 对照 =="
( cd "$BASEROOT/data/fact/lob_fact" && find . -name '*.parquet' | sort | xargs sha256sum ) > /tmp/r14_old.sha
( cd "$NEWROOT/data/fact/lob_fact" && find . -name '*.parquet' | sort | xargs sha256sum ) > /tmp/r14_new.sha
if diff -q /tmp/r14_old.sha /tmp/r14_new.sha >/dev/null; then
  echo "PASS：$(wc -l < /tmp/r14_old.sha) 个 parquet sha256 全等（含行序）"
else
  echo "sha256 不同 → 全列排序逐值对照："
  "$STOCK/platform/.venv/bin/python" - "$BASEROOT" "$NEWROOT" <<'PY'
import sys, pathlib
import polars as pl
a_root, b_root = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
fa = sorted((a_root / "data/fact/lob_fact").rglob("*.parquet"))
fb = sorted((b_root / "data/fact/lob_fact").rglob("*.parquet"))
assert len(fa) == len(fb), (len(fa), len(fb))
bad = []
for x, y in zip(fa, fb):
    a, b = pl.read_parquet(x), pl.read_parquet(y)
    if a.height != b.height:
        bad.append((str(x), f"行数 {a.height} vs {b.height}")); continue
    if not a.sort(a.columns).equals(b.sort(b.columns)):
        bad.append((str(x), "全列排序后仍不等"))
    else:
        print(f"  ✓ 内容逐值相等: {x.relative_to(a_root)}  行数={a.height}")
print("FAIL" if bad else "PASS：内容逐值等价")
for x, why in bad:
    print(f"  [FAIL] {x}: {why}")
raise SystemExit(1 if bad else 0)
PY
fi
echo "== 审计/状态文件存在性（元数据，不必逐字节）=="
for R in "$BASEROOT" "$NEWROOT"; do
  n=$(find "$R/data/fact/lob_fact/_batch" -maxdepth 1 -name 'state.json' | wc -l)
  echo "  $(basename "$R"): state.json=$n  SUCCESS=$(ls "$R/data/fact/lob_fact/_batch" 2>/dev/null | grep -c '^SUCCESS_' || true)  审计行=$(wc -l < "$(find "$R/data/fact/lob_fact/_batch/runs" -name rss_audit.csv | head -1)" 2>/dev/null || echo 0)"
done
git -C "$STOCK" worktree remove --force "$BASE" >/dev/null 2>&1 || true
