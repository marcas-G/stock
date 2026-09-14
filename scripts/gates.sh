#!/usr/bin/env bash
# stock 工作区常驻门（单仓单树）
#
# 用法：bash scripts/gates.sh [--all|--structure|--dataiface]
# 设计：分两档——
#   [强制] 结构门：现在就必须绿，红了即失败退出；
#   [报告] 数据接口门：R4 收口前为"报告模式"（打印违规清单、不影响退出码），
#          R4 完成后把 REPORT_ONLY 里的三项移出即转为强制（脚本尾部有说明）。
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd); cd "$ROOT"
MODE=${1:---all}
FAIL=0
ok()   { echo "    ✓ $1"; }
bad()  { echo "    ✗ $1"; FAIL=1; }
info() { echo "    · $1"; }

PLATFORM=platform
RESEARCH=research
FROZEN=':!platform/docs/superpowers :!docs/verification :!research/tools/lob_fact/notes :!docs/specs'

# ── 结构门（强制）───────────────────────────────────────────────
structure() {
  echo "[G-COPY] 三方内容不互串 · 契约文档单副本"
  # 平台树不得出现研究目录；研究树不得出现平台目录
  n=$(git ls-files | grep -cE "^$PLATFORM/(factor|tools)/" || true)          # 仅顶层目录，避免 core/factor 误判
  [ "$n" = "0" ] && ok "platform/ 顶层无 factor|tools 目录" || bad "platform/ 含研究内容 $n 项"
  n=$(git ls-files | grep -cE "^$RESEARCH/(src|tests)/" || true)             # 仅顶层，research 内嵌 tests/ 属正常
  [ "$n" = "0" ] && ok "research/ 顶层无平台 src|tests" || bad "research/ 含平台内容 $n 项"
  # 契约 4 篇各只一份
  for f in interface.md catalog.md data-ops-playbook.md teajoin-guide.md; do
    cnt=$(git ls-files | grep -c "/docs/$f\$" || true)
    [ "$cnt" = "1" ] && ok "docs/$f 单副本" || bad "docs/$f 出现 $cnt 次（应为 1）"
  done

  echo "[G-BOUNDARY] platform/ 不得 import research/"
  n=$(grep -rn "research\.\|from research\|import research" "$PLATFORM/src" "$PLATFORM/tests" --include=*.py 2>/dev/null | grep -v "['\"]" | wc -l)   # 排除字符串字面量（门自匹配）
  [ "$n" = "0" ] && ok "无 platform→research 依赖" || { bad "发现 $n 处 platform→research 引用"; grep -rn "research\." "$PLATFORM/src" --include=*.py | grep -v "['\"]" | head -3 | sed 's/^/      /'; }

  echo "[G-LEGACY] 旧路径/旧仓库名残留 = 0（冻结文档豁免）"
  # 豁免：验证证据 / 平台与研究 spec 与笔记 / 门自身 / **三份带冻结横幅的历史文档** /
  # data-map 的归档行（记录当时真实的备份克隆名）
  n=$(git grep -nI -e "quant-platform-main" -e "quant-platform-research" -e "projects/quant-platform" -- . \
        ':!docs/verification' ':!platform/docs/superpowers' ':!research/tools/lob_fact/notes' ':!research/docs/superpowers' 2>/dev/null \
      | grep -vE '^scripts/gates.sh:' \
      | grep -vE '^docs/(workspace-p0p8|traceability-matrix|remote-cleanup-checklist)\.md:' \
      | grep -v 'local-backup-20260903（975M' | wc -l)
  if [ "$n" = "0" ]; then ok "活文件零残留（豁免：3 份历史文档 + data-map 归档行）";
  else bad "活文件仍有 $n 处旧路径"; git grep -nI -e "quant-platform-main" -e "quant-platform-research" -- . 2>/dev/null | grep -vE 'verification/|superpowers/|notes/|gates.sh|workspace-p0p8|traceability-matrix|remote-cleanup-checklist' | head -5 | sed 's/^/      /'; fi

  echo "[G-PATHS] pyproject / pytest 路径自洽"
  grep -q 'pythonpath = \["src"\]' "$PLATFORM/pyproject.toml" && ok "platform pythonpath=src" || bad "platform pythonpath 异常"
  [ -d "$PLATFORM/src/factorlab" ] && ok "platform/src/factorlab 存在" || bad "platform/src/factorlab 缺失"
  grep -q 'testpaths' "$RESEARCH/pyproject.toml" && ok "research 有 testpaths" || bad "research pyproject 缺 testpaths"
  [ -f "$ROOT/pyproject.toml" ] && bad "根目录不应有 pyproject.toml（防 rootdir 抢占）" || ok "根无 pyproject.toml"

  echo "[G-IMPORTS] 全仓 factorlab.* 导入可解析（含负向自检）"
  if "$PLATFORM/.venv/bin/python" scripts/check_imports.py --selftest >/dev/null 2>&1; then ok "自检通过（能抓到迁移遗漏）"; else bad "自检失败——门失效"; fi
  if out=$("$PLATFORM/.venv/bin/python" scripts/check_imports.py 2>&1); then ok "$(echo "$out" | tail -1)"; else bad "导入解析失败"; echo "$out" | head -8 | sed 's/^/      /'; fi

  echo "[G-INDEX] 因子索引与生成器一致"
  if out=$("$PLATFORM/.venv/bin/python" research/tools/factor_lib/build_index.py --check 2>&1); then ok "$out"; else bad "索引不一致：$out"; fi

  echo "[G-VENV] 平台 editable 落位断言"
  resolved=$("$PLATFORM/.venv/bin/python" -c "import factorlab;print(factorlab.__file__)" 2>/dev/null || echo "")
  case "$resolved" in
    "$ROOT/platform/src/factorlab/__init__.py") ok "factorlab → platform/src ✓" ;;
    "") bad "平台 venv 无法 import factorlab" ;;
    *) bad "factorlab 解析到 $resolved（应为 platform/src）" ;;
  esac
}

# ── 数据接口门（R4 前为报告模式）────────────────────────────────
dataiface() {
  echo "[G-CONTRACT] 契约单点（factio 之外的表名/分区字面量）"
  n=$(grep -rn --include=*.py -E "'(stock_bars_1m|bars_1m|tick_orders|tick_trades|tick_snapshots|stock_basic|daily_basic|adj_factor|trade_cal|stk_limit)'" "$PLATFORM/src" 2>/dev/null | grep -v "core/factio/" | wc -l)
  info "平台 src 表名字面量（factio 外）: $n 处 —— R4 收敛目标 0"
  n=$(grep -rn --include=*.py -E "year=|\"month\"|/month=" "$RESEARCH/tools" 2>/dev/null | grep -vE "/tests/|/notes/|/diag/" | wc -l)
  info "研究侧分区拼接字面量: $n 处 —— R4 收敛到 core.factio.partitions"
  for f in research/docs/factors platform/docs/interface.md; do :; done
  n=$(grep -c "" "$PLATFORM/src/factorlab/core/factio/schema.py" 2>/dev/null || echo 0)
  info "列契约单点 core/factio/schema.py: $n 行（应保持唯一来源）"

  echo "[G-MARK] 完成标记形态（只允许 _SUCCESS + state JSON）"
  for pat in "_SUCCESS" "\.done" "_conversion\.json"; do
    n=$(grep -rn --include=*.py "$pat" "$RESEARCH/tools" 2>/dev/null | wc -l)
    info "形态 '$pat': $n 处"
  done
  info "—— R4 目标：ch_ingest 的 state.json/ 目录与 _conversion.json 并入统一 state"

  echo "[G-READ] 研究侧直读 tick/lob/bars parquet（应经平台单点）"
  # 2026-09-15 R6 复核：剩余直读**逐处人工核对**后均为合法——
  # manifest（conversion_manifest/cancels_manifest）、自有产物（1m merged/panel）、
  # 元数据（reconcile 的 num_rows）、流式灌库（ingest 的 iter_batches）以及 daily_fact 的小切片。
  # 转强制需 **AST 级判据**（区分「事实表读」与「manifest/自有产物读」，grep 做不到）——登记 pending #13。
  n=$(grep -rn --include=*.py -E "read_parquet\(|scan_parquet\(" "$RESEARCH/tools" 2>/dev/null | grep -vE "/tests/|/notes/|/diag/|fixtures" | wc -l)
  info "研究侧直读处数: $n —— R4 目标：tick/lob/bars 一律经 adapters.tick_read / adapters.lob_read"
  grep -rln --include=*.py -E "read_parquet\(|scan_parquet\(" "$RESEARCH/tools" 2>/dev/null | grep -vE "/tests/|/notes/|/diag/|fixtures" | sed 's/^/      /'
}

echo "== stock gates =="
case "$MODE" in
  --structure) structure ;;
  --dataiface) dataiface ;;
  *) structure; echo; dataiface ;;
esac
echo
if [ "$FAIL" = "0" ]; then echo "结构门：全绿"; else echo "结构门：有失败（见上）"; fi
echo "数据接口门：报告模式（R4 收口后转强制——届时把 dataiface 里的 info 改为 ok/bad 判定）"
exit $FAIL
