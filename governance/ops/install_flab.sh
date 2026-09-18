#!/usr/bin/env bash
# install_flab.sh —— 安装研究员短入口 `flab`（R31）。
#
# 生成的 ~/.local/bin/flab 内容：
#   exec env FACTORLAB_DATA_BACKEND=ch <repo>/platform/.venv/bin/factorlab research "$@"
# 即后端固定 ch（spec §5 默认值）、命令面走 `factorlab research` 命令组；
# 重任务闸/内存护栏由 research._guard 在重命令内强制执行。
#
# 用法：bash governance/ops/install_flab.sh [--dry-run]
#   --dry-run 只打印将写入的脚本与目标路径，不落盘。
# 环境：FLAB_INSTALL_DIR 覆盖安装目录（缺省 $HOME/.local/bin）。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BIN_DIR="${FLAB_INSTALL_DIR:-$HOME/.local/bin}"
TARGET="$BIN_DIR/flab"
FACTORLAB_BIN="$REPO/platform/.venv/bin/factorlab"
DRY_RUN=0

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        -h|--help)
            echo "用法: install_flab.sh [--dry-run]（FLAB_INSTALL_DIR 覆盖安装目录）"
            exit 0
            ;;
        *)
            echo "install_flab: 未知参数 $arg（支持 --dry-run）" >&2
            exit 2
            ;;
    esac
done

SCRIPT_BODY="$(cat <<EOF
#!/usr/bin/env bash
# flab —— FactorLab 研究员入口（由 governance/ops/install_flab.sh 生成，勿手改）
exec env FACTORLAB_DATA_BACKEND=ch "$FACTORLAB_BIN" research "\$@"
EOF
)"

if [ "$DRY_RUN" = "1" ]; then
    echo "install_flab: dry-run 目标 $TARGET"
    printf '%s\n' "$SCRIPT_BODY"
    exit 0
fi

if [ ! -x "$FACTORLAB_BIN" ]; then
    echo "install_flab: 找不到可执行 $FACTORLAB_BIN（先装平台 venv）" >&2
    exit 1
fi
mkdir -p "$BIN_DIR"
printf '%s\n' "$SCRIPT_BODY" > "$TARGET"
chmod +x "$TARGET"
echo "flab 已安装: $TARGET"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "提示: $BIN_DIR 不在 PATH，可 export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac
