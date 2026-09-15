#!/usr/bin/env bash
# 重装 editable 安装（factorlab + quant_core）——**不是 venv 重建脚本**。
#
# 为什么不是重建：平台 venv 里有大量 pyproject **未声明**的包（实测 pandas/openpyxl/plotly/
# black/numba…），且全仓无 lock 文件 → 从声明重建 venv **不可复现**，重建还会引入
# polars/duckdb 版本漂移（威胁 lob_fact 的字节级重跑）。那笔欠账登记在 docs/pending-items.md。
# 本脚本只做一件事：目录移动/搬迁之后，把两个 editable 指回新路径，并**断言落位**。
#
# 用法：bash scripts/reinstall_editable.sh [freeze-输出路径]
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd); cd "$ROOT"

UV=${UV:-$(command -v uv || echo /home/gaolei/.local/bin/uv)}
PY=platform/.venv/bin/python
[ -x "$PY" ] || { echo "✗ 平台 venv 不存在：$PY" >&2; exit 1; }

echo "[1/3] factorlab  ←  platform/"
"$UV" pip install --python "$PY" -e platform --no-deps --no-build-isolation 2>&1 | tail -2

echo "[2/3] quant_core  ←  platform/kernels/quant_core/"
"$UV" pip install --python "$PY" -e platform/kernels/quant_core --no-deps --no-build-isolation 2>&1 | tail -2

echo "[3/3] 落位断言（正向 + 反向）"
"$PY" - <<'PYEOF'
import factorlab, quant_core
assert factorlab.__file__.endswith("platform/src/factorlab/__init__.py"), factorlab.__file__
assert "platform/kernels/quant_core/" in quant_core.__file__, quant_core.__file__
assert "/projects/" not in quant_core.__file__, quant_core.__file__   # 旧 editable 残留即失败
print("  ✓ factorlab   →", factorlab.__file__)
print("  ✓ quant_core  →", quant_core.__file__)
PYEOF

if [ $# -ge 1 ]; then
  "$UV" pip freeze --python "$PY" > "$1"
  echo "  ✓ venv 包快照 → $1（重建不可复现的证据：快照里可见未声明包）"
fi

echo "完成。注：emb（T2）**不装** quant_core（research 侧零消费者；需要时："
echo "  /data/students/gaolei/anaconda3/envs/emb/bin/python -m pip install -e platform/kernels/quant_core --no-deps）"
