#!/usr/bin/env bash
# 重装 editable 安装（factorlab）——**不是 venv 重建脚本**。
#
# 为什么不是重建：平台 venv 里有大量 pyproject **未声明**的包（实测 pandas/openpyxl/plotly/
# black/numba…），且全仓无 lock 文件 → 从声明重建 venv **不可复现**，重建还会引入
# polars/duckdb 版本漂移（威胁 lob_fact 的字节级重跑）。那笔欠账登记在 governance/workspace/pending-items.md。
# 本脚本只做一件事：目录移动/搬迁之后，把两个 editable 指回新路径，并**断言落位**。
#
# 用法：bash governance/ops/reinstall_editable.sh [freeze-输出路径]
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd); cd "$ROOT"

UV=${UV:-$(command -v uv || echo /home/gaolei/.local/bin/uv)}
PY=platform/.venv/bin/python
[ -x "$PY" ] || { echo "✗ 平台 venv 不存在：$PY" >&2; exit 1; }

echo "[1/2] factorlab  ←  platform/"
"$UV" pip install --python "$PY" -e platform --no-deps --no-build-isolation 2>&1 | tail -2

echo "[2/2] 落位断言"
"$PY" - <<'PYEOF'
import factorlab
assert factorlab.__file__.endswith("platform/src/factorlab/__init__.py"), factorlab.__file__
print("  ✓ factorlab   →", factorlab.__file__)
PYEOF

if [ $# -ge 1 ]; then
  "$UV" pip freeze --python "$PY" > "$1"
  echo "  ✓ venv 包快照 → $1（重建不可复现的证据：快照里可见未声明包）"
fi

echo "完成。注：R27 单解释器化后唯一解释器为平台 venv——factorlab 由本脚本 [1/2]"
echo "以 editable 装入 platform/.venv；emb（T2）环境已退役，不再作为安装目标。"
echo "R30 Task 15（D12）：评估内核并入 factorlab.core.eval.kernel，独立 quant-core dist 已删除。"
