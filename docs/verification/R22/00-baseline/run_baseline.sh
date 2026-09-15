#!/usr/bin/env bash
# R22 代表 spec 值级回归运行器：对 6 个代表 spec 逐个 `factorlab run`（ch 后端），
# 原始输出与 summary.json 存档到 docs/verification/R22/<tag>/。
# 用法: bash run_baseline.sh <tag>   （tag=00-baseline 跑改动前；tag=10-regression 跑改动后）
# 前置: prepare_baseline_specs.sh（exclude_st 移除版等价 spec 在 00-baseline/specs/）。
set -uo pipefail
TAG="${1:?usage: run_baseline.sh <tag>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../../.." && pwd)"
OUT="$ROOT/docs/verification/R22/$TAG"
SPECS_DIR="$ROOT/docs/verification/R22/00-baseline/specs"
mkdir -p "$OUT"
: > "$OUT/run_all.log"

SPECS=(
  reversal_20d/reversal_20d.yaml
  momentum_20d/momentum_20d.yaml
  vol_run_energy/symrun.yaml
  volatility/low_vol_20d.yaml
  liquidity/accel.yaml
  reversal_20d/wcorr.yaml
)

rc_all=0
for rel in "${SPECS[@]}"; do
  spec="$SPECS_DIR/$rel"
  name="$(grep -m1 '^name:' "$spec" | cut -d' ' -f2)"
  log="$OUT/run_${name}.log"
  {
    echo "$ FACTORLAB_DATA_BACKEND=ch platform/.venv/bin/factorlab run docs/verification/R22/00-baseline/specs/$rel"
    echo "# cwd=$ROOT/platform  tag=$TAG  spec.name=$name"
  } > "$log"
  ( cd "$ROOT/platform" && FACTORLAB_DATA_BACKEND=ch timeout 3600 \
      .venv/bin/factorlab run "$spec" >> "$log" 2>&1 )
  rc=$?
  tail -2 "$log" >> "$OUT/run_all.log"
  echo "=== $rel -> $name (exit=$rc)" >> "$OUT/run_all.log"
  if [ "$rc" -eq 0 ] && [ -f "$ROOT/platform/results/$name/summary.json" ]; then
    cp "$ROOT/platform/results/$name/summary.json" "$OUT/$name.json"
    echo "OK $name (summary -> $OUT/$name.json)" | tee -a "$OUT/run_all.log"
  else
    echo "FAIL $name exit=$rc" | tee -a "$OUT/run_all.log"
    rc_all=1
  fi
done
exit "$rc_all"
