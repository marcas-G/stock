#!/usr/bin/env bash
# R22 baseline: generate runnable spec copies for the representative specs.
#
# Reason: CH-only 数据面无 stock_st 表，而 `--universe` override 不传播到
# resolve_universe_frame（run.py:450/452）——所有含 exclude_st 的 spec 直接
# ValueError（probe_exclude_st_failure.txt）。故文本移除 `exclude_st: true, `
# （formula/date/params 逐字不变），得到"同 spec 等价代表"。
#
# 数据面补充限制（00-baseline/README.md）：daily_basic 仅 total_mv/turnover_rate
# 有值（pb/pe_ttm/circ_mv/dv_ratio/volume_ratio 全 null）、index_daily 空表——
# value/bp（pb）、crash_bottom_leader（circ_mv）、adv20（idx_ret）信号全 null。
# 实证后替换：value/bp → reversal_20d/wcorr（多算子组合覆盖）；
# crash_bottom_leader → liquidity/accel（turnover + 双 ts_mean 窗口）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
BASE="$ROOT/docs/verification/R22/00-baseline"
for rel in \
  reversal_20d/reversal_20d.yaml \
  momentum_20d/momentum_20d.yaml \
  vol_run_energy/symrun.yaml \
  volatility/low_vol_20d.yaml \
  liquidity/accel.yaml \
  reversal_20d/wcorr.yaml ; do
  mkdir -p "$BASE/specs/$(dirname "$rel")"
  python3 - "$ROOT/research/factor/$rel" "$BASE/specs/$rel" <<'PY'
import sys
src, dst = sys.argv[1], sys.argv[2]
text = open(src, encoding="utf-8").read()
before = text
text = text.replace("exclude_st: true, ", "")
assert text != before, f"exclude_st 未出现: {src}"
open(dst, "w", encoding="utf-8").write(text)
PY
  echo "wrote $BASE/specs/$rel"
done
