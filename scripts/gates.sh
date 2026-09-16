#!/usr/bin/env bash
# stock 工作区常驻门（单仓单树）
#
# 用法：bash scripts/gates.sh [--all|--structure|--topo|--dataiface]
# 设计：分两档——
#   [强制] 结构门：现在就必须绿，红了即失败退出；
#   [判定] 数据接口门：R8c 起由 `scripts/check_dataiface.py` 做 **AST 判据**
#          （grep 会把注释/文案/关键字实参算进去，计数不说明问题）：
#          ENFORCED 两项（研究侧分区字面量、标记路径构造）红了即失败；
#          REPORT 两项（平台表名、研究侧直读）打印未竟计数并指向 pending-items。
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd); cd "$ROOT"
MODE=${1:---all}
FAIL=0
ok()   { echo "    ✓ $1"; }
bad()  { echo "    ✗ $1"; FAIL=1; }
info() { echo "    · $1"; }

PLATFORM=platform
RESEARCH=research
FROZEN=':!knowledge/design/platform :!governance/evidence/verification :!platform/tools/lob_fact/notes :!research/tools/lob_fact/notes'

# ── 结构门（强制）───────────────────────────────────────────────
structure() {
  echo "[G-COPY] 三方内容不互串 · 契约文档单副本"
  # 平台树不得出现研究目录（factor/）；tools/ 自 R27 起为数据生产线工具集（用户拍板归位 platform/）
  n=$(git ls-files | grep -cE "^$PLATFORM/factor/" || true)                  # 仅顶层目录，避免 core/factor 误判
  [ "$n" = "0" ] && ok "platform/ 顶层无 factor 目录（tools/ 为数据生产线）" || bad "platform/ 含研究内容 $n 项"
  [ -d "$PLATFORM/tools" ] && ok "platform/tools 存在（数据生产线工具集）" || bad "platform/tools 缺失"
  n=$(git ls-files | grep -cE "^$RESEARCH/(src|tests)/" || true)             # 仅顶层，research 内嵌 tests/ 属正常
  [ "$n" = "0" ] && ok "research/ 顶层无平台 src|tests" || bad "research/ 含平台内容 $n 项"
  # 契约 4 篇各只一份（R24 起归 knowledge/contracts/）
  for f in interface.md catalog.md data-ops-playbook.md teajoin-guide.md; do
    cnt=$(git ls-files | grep -cE "^knowledge/contracts/$f\$" || true)
    [ "$cnt" = "1" ] && ok "knowledge/contracts/$f 单副本" || bad "knowledge/contracts/$f 出现 $cnt 次（应为 1）"
  done

  echo "[G-BOUNDARY] platform/ 不得 import research/"
  n=$(grep -rn "research\.\|from research\|import research" "$PLATFORM/src" "$PLATFORM/tests" "$PLATFORM/tools" --include=*.py --exclude-dir=notes 2>/dev/null | grep -v "['\"]" | wc -l)   # 排除字符串字面量（门自匹配）；notes/ 为历史诊断豁免
  [ "$n" = "0" ] && ok "无 platform→research 依赖" || { bad "发现 $n 处 platform→research 引用"; grep -rn "research\." "$PLATFORM/src" "$PLATFORM/tools" --include=*.py --exclude-dir=notes | grep -v "['\"]" | head -3 | sed 's/^/      /'; }

  echo "[G-LEGACY] 旧路径/旧仓库名残留 = 0（冻结文档豁免）"
  # 豁免：验证证据 / 平台与研究 spec 与笔记 / 门自身 / **三份带冻结横幅的历史文档** /
  # data-map 的归档行（记录当时真实的备份克隆名）/ **同行为删除记录的"活"文档行**。
  # 判据：所剩行里，**同一行同时出现 R17**的算删除记录（提旧名却不提 R17 的仍报红——
  # 活指针不会写 R17；写了就是自证造假，评审可抓）。2026-09-15 R17 起生效。
  n=$(git grep -nI -e "quant-platform-main" -e "quant-platform-research" -e "projects/quant-platform" -- . \
        ':!governance/evidence/verification' ':!docs/verification' ':!knowledge/design/platform' ':!platform/tools/lob_fact/notes' ':!research/tools/lob_fact/notes' ':!knowledge/design/research' 2>/dev/null \
      | grep -vE '^scripts/gates.sh:' \
      | grep -vE '^docs/(workspace-p0p8|traceability-matrix|remote-cleanup-checklist)\.md:' \
      | grep -v 'local-backup-20260903（975M' \
      | grep -vE 'R17' | wc -l)
  if [ "$n" = "0" ]; then ok "活文件零残留（豁免：3 份历史文档 + data-map 归档行 + R17 删除记录行）";
  else bad "活文件仍有 $n 处旧路径"; git grep -nI -e "quant-platform-main" -e "quant-platform-research" -- . 2>/dev/null | grep -vE 'verification/|superpowers/|notes/|gates.sh|workspace-p0p8|traceability-matrix|remote-cleanup-checklist|R17' | head -5 | sed 's/^/      /'; fi

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

  echo "[G-INDEX] 策略索引与生成器一致 · spec↔档案成对"
  if out=$("$PLATFORM/.venv/bin/python" research/tools/factor_lib/build_strategy_index.py --check 2>&1); then ok "$out"; else bad "策略索引不一致：$out"; fi

  echo "[G-ANNOTATE] 因子档案 snapshot 标注齐备（R21 约定）"
  # 脚本原位在 R21/EVID（证据即工具）；只做只读 --check，不写档案。
  if out=$(python3 docs/verification/R21/EVID/annotate_factor_archives.py --check 2>&1); then ok "$out"; else bad "$out"; fi

  echo "[G-LINT] 全库因子 spec lint（单进程批跑；挖矿在途 spec 一并计入）"
  # 失败行含具体 spec 路径——在途红与代码级红按文件区分，不误报为门故障。
  if out=$("$PLATFORM/.venv/bin/factorlab" lint --all 2>&1); then ok "$(echo "$out" | tail -1)"; else
    bad "lint 有失败（在途/存量按下列文件区分）"; echo "$out" | sed 's/^/      /'; fi

  echo "[G-VENV] 平台 editable 落位断言"
  resolved=$("$PLATFORM/.venv/bin/python" -c "import factorlab;print(factorlab.__file__)" 2>/dev/null || echo "")
  case "$resolved" in
    "$ROOT/platform/src/factorlab/__init__.py") ok "factorlab → platform/src ✓" ;;
    "") bad "平台 venv 无法 import factorlab" ;;
    *) bad "factorlab 解析到 $resolved（应为 platform/src）" ;;
  esac
  # quant_core：正向（落 platform/kernels/quant_core）+ **反向**（不得再指回 projects/*
  # —— R18 前它以 editable 写死 projects/quant_core_shim，残留 finder 会让正向也"看似"成立）。
  qc=$("$PLATFORM/.venv/bin/python" -c "import quant_core;print(quant_core.__file__)" 2>/dev/null || echo "")
  case "$qc" in
    "$ROOT/platform/kernels/quant_core/quant_core/__init__.py") ok "quant_core → platform/kernels ✓" ;;
    "") bad "平台 venv 无法 import quant_core（评估内核缺失 → test_eval_rust_ic 会红）" ;;
    *) bad "quant_core 解析到 $qc（应为 platform/kernels/quant_core）" ;;
  esac
  case "$qc" in *"/projects/"*) bad "quant_core 仍解析到 projects/ 下（旧 editable finder 残留）" ;; esac
}

# ── 数据接口门（R4 前为报告模式）────────────────────────────────
topo() {
  # R11：工具拓扑（依赖方向 / 横向耦合）。判据与自检见 scripts/check_tool_layering.py。
  PY=${PLATFORM}/.venv/bin/python
  [ -x "$PY" ] || PY=python3
  "$PY" scripts/check_tool_layering.py || FAIL=1
  "$PY" scripts/check_tool_layering.py --selftest || FAIL=1
}

dataiface() {
  # R8c：从"报告模式 grep 计数"升级为 AST 判定——
  #   ENFORCED：工具树分区字面量（`year=` 只许 partitions 产出）、标记路径构造（只许 writekit）；
  #   REPORT  ：平台表名字面量（未竟 #12①:460 处 SQL）、工具树直读（未竟 #13）。
  # 由 Python 侧统一输出（含负向自检：--selftest 保证门不是死的）。
  PY=${PLATFORM}/.venv/bin/python
  [ -x "$PY" ] || PY=python3
  if ! "$PY" scripts/check_dataiface.py; then
    FAIL=1
  fi
  "$PY" scripts/check_dataiface.py --selftest || FAIL=1
}

echo "== stock gates =="
case "$MODE" in
  --structure) structure ;;
  --dataiface) dataiface ;;
  --topo) topo ;;
  *) structure; echo; topo; echo; dataiface ;;
esac
echo
if [ "$FAIL" = "0" ]; then echo "结构门：全绿"; else echo "结构门：有失败（见上）"; fi
echo "数据接口门：ENFORCED 判定（AST）+ REPORT 未竟计数（见 scripts/check_dataiface.py）"
exit $FAIL
