#!/usr/bin/env bash
# ci_env.sh —— 云端（GitHub-hosted CI）与宿主 deep 共用的**唯一环境/钉版点**。
#
# 用法：bash governance/ops/ci_env.sh [--dir DIR]      # DIR 默认 = 本仓根
# 幂等：platform/.venv 存在且钉版/editable 落位满足 → 原样复用（不调安装器）；
#       否则建 venv（Python>=3.13）+ `-e ./platform[dev]` + 钉版安装。
# 退出码：0=就绪；2=环境不满足（无 Python>=3.13、安装失败）。
#
# 唯一钉版点（改版本只改这里；workflow / verify.sh 不得再写版本号）：
#   httpx==0.28.1         starlette TestClient 运行时依赖（pyproject 未声明，pending #18②）
#   polars==1.44.1        与已提交 _generated_polars_methods.py 版本头一致
#   typer/rich/click      CLI 渲染钉版（--help 文本断言对版本敏感）
#   pytest-timeout==2.4.0 verify.sh 的 --timeout 依赖（pyproject 未声明）
set -uo pipefail

DIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DIR=${2:-}; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "[ci_env] 未知参数：$1" >&2; exit 2 ;;
  esac
done
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
DIR=${DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}
DIR=$(cd "$DIR" 2>/dev/null && pwd) || { echo "[ci_env] 目录不存在：$DIR" >&2; exit 2; }
[ -d "$DIR/platform" ] || { echo "[ci_env] 不是 stock 仓根（缺 platform/）：$DIR" >&2; exit 2; }

VENV="$DIR/platform/.venv"
PY="$VENV/bin/python"
PINS=(httpx==0.28.1 polars==1.44.1 typer==0.27.2 rich==15.0.0 click==8.5.0 pytest-timeout==2.4.0)

pins_ok() {
  "$PY" - "$DIR" <<'PY' 2>/dev/null
import importlib.metadata as md
import pathlib
import sys

root = pathlib.Path(sys.argv[1]).resolve()
pins = {
    "httpx": "0.28.1",
    "polars": "1.44.1",
    "typer": "0.27.2",
    "rich": "15.0.0",
    "click": "8.5.0",
    "pytest-timeout": "2.4.0",
}
for name, want in pins.items():
    try:
        got = md.version(name)
    except md.PackageNotFoundError:
        sys.exit(1)
    if got != want:
        sys.exit(1)
import factorlab

want_file = (root / "platform" / "src" / "factorlab" / "__init__.py").resolve()
if pathlib.Path(factorlab.__file__).resolve() != want_file:
    sys.exit(1)
PY
}

find_base_python() {
  # 优先显式覆盖，其次 PATH 里的 3.13+，最后 uv 托管的 3.13（宿主 uv venv 场景）。
  local c found
  for c in ${FACTORLAB_CI_PYTHON:-} python3.13 python3 python; do
    [ -n "$c" ] || continue
    command -v "$c" >/dev/null 2>&1 || continue
    "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 13) else 1)' 2>/dev/null \
      && { command -v "$c"; return 0; }
  done
  local uv_bin
  uv_bin=$(command -v uv 2>/dev/null || true)
  [ -n "$uv_bin" ] || { [ -x "$HOME/.local/bin/uv" ] && uv_bin="$HOME/.local/bin/uv"; }
  [ -n "$uv_bin" ] || return 1
  found=$("$uv_bin" python find 3.13 2>/dev/null) || return 1
  [ -x "$found" ] && { printf '%s\n' "$found"; return 0; }
  return 1
}

install_pins() {
  local specs=(-e "./platform[dev]" "${PINS[@]}")
  if "$PY" -m pip --version >/dev/null 2>&1; then
    ( cd "$DIR" && "$PY" -m pip install "${specs[@]}" )
  elif command -v uv >/dev/null 2>&1; then
    # uv venv（宿主）不带 pip：走 uv，避免为装 pip 多改 venv。
    ( cd "$DIR" && uv pip install --python "$PY" "${specs[@]}" )
  else
    "$PY" -m ensurepip --upgrade >/dev/null 2>&1 || {
      echo "[ci_env] 无 pip 且 ensurepip 失败——请装 pip 或 uv 后重试" >&2; return 2; }
    ( cd "$DIR" && "$PY" -m pip install "${specs[@]}" )
  fi
}

if [ -x "$PY" ] && pins_ok; then
  echo "[ci_env] 复用现有 venv（钉版满足）：$VENV"
  echo "[ci_env] python=$("$PY" --version 2>&1)；factorlab=$("$PY" -c 'import factorlab;print(factorlab.__file__)' 2>/dev/null)"
  exit 0
fi

if [ ! -x "$PY" ]; then
  BASE_PY=$(find_base_python) || {
    echo "[ci_env] 找不到 Python>=3.13（platform requires-python）。" >&2
    echo "[ci_env] 指引：装 python3.13 或 uv（uv python install 3.13），或设 FACTORLAB_CI_PYTHON=<解释器>。" >&2
    exit 2
  }
  echo "[ci_env] 建 venv：$BASE_PY -m venv $VENV"
  "$BASE_PY" -m venv "$VENV" || { echo "[ci_env] venv 建置失败" >&2; exit 2; }
else
  echo "[ci_env] 现有 venv 钉版不满足 → 补装/校正：$VENV"
fi

echo "[ci_env] 安装：-e ./platform[dev] + 钉版（httpx/polars/typer/rich/click/pytest-timeout）"
if ! install_pins; then
  echo "[ci_env] 安装失败（网络/索引？）" >&2
  exit 2
fi
pins_ok || { echo "[ci_env] 安装后仍不满足钉版/落位断言" >&2; exit 2; }
echo "[ci_env] 就绪：python=$("$PY" --version 2>&1)；factorlab=$("$PY" -c 'import factorlab;print(factorlab.__file__)')"
