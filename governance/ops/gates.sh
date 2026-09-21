#!/usr/bin/env bash
# stock 工作区常驻门（单仓单树）
#
# 用法：bash governance/ops/gates.sh [--all|--structure|--topo|--dataiface|--ref] [--offline]
# 设计：分两档——
#   [强制] 结构门：现在就必须绿，红了即失败退出；
#   [判定] 数据接口门：R8c 起由 `governance/ops/check_dataiface.py` 做 **AST 判据**
#          （grep 会把注释/文案/关键字实参算进去，计数不说明问题）：
#          ENFORCED 两项（研究侧分区字面量、标记路径构造）红了即失败；
#          REPORT 两项（平台表名、研究侧直读）打印未竟计数并指向 pending-items。
# --offline（R38）：云端/干净 checkout 子集——跳过依赖研究产物区/台账的门
#   （G-INDEX 产品索引、G-ANNOTATE、G-REVIEWS、G-REF sidecar；G-LINT 无产物区时 SKIP，
#   G-LOCKBOX 离线只做声明格式校验、台账交叉核对 SKIP），
#   其余静态门照跑；每条 SKIP 打印 [G-OFFLINE] 及原因。不得借此放宽实质检查。
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd); cd "$ROOT"
MODE="--all"
OFFLINE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --offline) OFFLINE=1 ;;
    --all|--structure|--topo|--dataiface|--ref) MODE=$1 ;;
    *) echo "未知参数：$1（用法见文件头）" >&2; exit 2 ;;
  esac
  shift
done
FAIL=0
PRODUCT_ROOT="${QUANTRESEARCH_ROOT:-/data/students/gaolei/quantresearch}"
ok()   { echo "    ✓ $1"; }
bad()  { echo "    ✗ $1"; FAIL=1; }
info() { echo "    · $1"; }
skip_offline() { echo "    [G-OFFLINE] SKIP $1 —— $2"; }

PLATFORM=platform
RESEARCH=research
# 注：历史 `FROZEN` 变量（豁免 pathspec 草稿）自 R24 起零引用、路径已过时——
# R06-M6 删除；实际豁免以各判据行内 pathspec 为准（G-LEGACY 两段）。

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
  # R31：判据目标 = 仓库根 `research/` 树；平台自己的门面包 `factorlab.research`
  # 是 platform/src 内容（R31 研究员 API），不属本门对象——排除避免误伤。
  n=$(grep -rn "research\.\|from research\|import research" "$PLATFORM/src" "$PLATFORM/tests" "$PLATFORM/tools" --include=*.py --exclude-dir=notes 2>/dev/null | grep -v "['\"]" | grep -v "factorlab\.research" | wc -l)   # 排除字符串字面量（门自匹配）；notes/ 为历史诊断豁免
  [ "$n" = "0" ] && ok "无 platform→research 依赖" || { bad "发现 $n 处 platform→research 引用"; grep -rn "research\." "$PLATFORM/src" "$PLATFORM/tools" --include=*.py --exclude-dir=notes | grep -v "['\"]" | grep -v "factorlab\.research" | head -3 | sed 's/^/      /'; }

  echo "[G-LEGACY] 旧路径/旧仓库名残留 = 0（冻结文档豁免）"
  # 豁免：验证证据 / 平台与研究 spec 与笔记 / 门自身 / **三份带冻结横幅的历史文档** /
  # data-map 的归档行（记录当时真实的备份克隆名）/ **同行为删除记录的"活"文档行**。
  # 判据：所剩行里，**同一行同时出现 R17**的算删除记录（提旧名却不提 R17 的仍报红——
  # 活指针不会写 R17；写了就是自证造假，评审可抓）。2026-09-15 R17 起生效。
  #
  # R07-GATE-I3（2026-09-16）判据加固（findings.md；证据 R24/17-r07-fixes）：
  #   ① 扫描面 = tracked + **untracked**（untracked 同为"真实存活"引用，此前漏网）。
  #      本机 git 2.17 无 `git grep --untracked`，以 `git ls-files -o --exclude-standard`
  #      + grep 等价实现；
  #   ② 裸 `results/`（无 `platform/` 前缀）纳入判据：PCRE 负向后顾排除 `platform/results`
  #      （另有字面量判据）、`runs/results`、`test_results` 等词形；要求后随具体名字
  #      （`<name>` 占位、`results/ 口径` 一类纯文本不算）；
  #   ③ 补 R24/R27 已迁路径：`research/tools/lib/`、`docs/factors/`、`docs/strategies/`；
  #   ④ README 整文件豁免 → 行级（映射/历史行精确放行，其余行纳入判据）；strategies 两
  #      文件随 R06-TEST-I5/TOOLS-I6 闭环改行级；platform 测试一处 `results/parquet` 文本行放行。
  legacy_scan() {  # $1=names 旧仓库名 | paths 已迁路径；输出 file:line:content（tracked+untracked）
    local pats scope pcre=""
    case "$1" in
      names)
        pats=(-e "quant-platform-main" -e "quant-platform-research" -e "projects/quant-platform")
        scope=(. ':!governance/evidence' ':!knowledge/design/platform' ':!platform/tools/lob_fact/notes' ':!research/tools/lob_fact/notes' ':!knowledge/design/research') ;;
      paths)
        pats=(-e "research/docs/" -e "docs/reviews" -e "docs/index" -e "docs/factors" -e "docs/strategies" \
              -e "platform/results" -e "anaconda3/envs/emb" -e "emb/bin/python" \
              -e "research/tools/quark/" -e "research/tools/converters/" -e "research/tools/lob_fact/" \
              -e "research/tools/ch_ingest/" -e "research/tools/ashare_ingest/" \
              -e "research/tools/universe_stages/" -e "research/tools/1m_features/" \
              -e "research/tools/extract_sz_cancels/" -e "research/tools/_env" -e "research/tools/lib/")
        # 裸 results/：单条 PCRE（GNU grep 3.1 的 -P 只收单模式，不能与多 -e 混用；
        # git grep 不受此限，统一拆成两路）；后随具体名字，负向后顾排除 platform/results、
        # runs/results、test_results；负向前瞻放行两类规范前缀（非旧路径）：
        #   results/platform/  —— R37/R39：settings.results_dir 与服务相对 output_dir 官方根；
        #   results/YYYY-MM-*  —— quantresearch 研究 campaign 命名（CONVENTIONS §1）。
        pcre='(?<![\w/])results/(?!(platform/|[0-9]{4}-[0-9]{2}))[A-Za-z0-9_]'
        scope=(. ':!governance/evidence' ':!knowledge/design' ':!platform/docs' ':!research/docs' ':!platform/tools/lob_fact/notes' ':!research/tools/lob_fact/notes') ;;
    esac
    { if [ -n "$pcre" ]; then git grep -nIP "${pats[@]}" -e "$pcre" -- "${scope[@]}" 2>/dev/null
      else git grep -nIP "${pats[@]}" -- "${scope[@]}" 2>/dev/null; fi
      git ls-files -oz --exclude-standard -- "${scope[@]}" 2>/dev/null \
        | xargs -0 -r grep -HnI "${pats[@]}" 2>/dev/null
      if [ -n "$pcre" ]; then
        git ls-files -oz --exclude-standard -- "${scope[@]}" 2>/dev/null \
          | xargs -0 -r grep -HnIP -e "$pcre" 2>/dev/null
      fi
    } | LC_ALL=C sort -u
  }

  names_hits=$(legacy_scan names \
      | grep -vE '^governance/ops/gates\.sh:' \
      | grep -vE '^governance/workspace/(workspace-p0p8|traceability-matrix|remote-cleanup-checklist)\.md:' \
      | grep -v 'local-backup-20260903（975M' \
      | grep -vE 'R17' || true)
  n=$(printf '%s\n' "$names_hits" | grep -c . || true)
  if [ "$n" = "0" ]; then ok "活文件零残留（tracked+untracked；豁免：3 份历史文档 + data-map 归档行 + R17 删除记录行）";
  else bad "活文件仍有 $n 处旧路径"; printf '%s\n' "$names_hits" | head -5 | sed 's/^/      /'; fi

  # R06-M9：已迁路径的**活引用**判据（旧仓库名之外）。覆盖：research/docs/<内容>、
  # docs/{reviews,index}、platform/results、R27 已迁工具旧路径、emb 解释器路径；
  # R07-GATE-I3 起加：docs/{factors,strategies}、research/tools/lib/、裸 results/。
  # 扫面排除：证据（含历史 reviews）、设计文档（冻结历史）；两处 R24 指针壳
  # （platform|research/docs，仅存 README 指针）；lob_fact notes（既有豁免）。
  # 行/文件豁免（均为映射/台账/门自身/反向断言/历史引文，非活指针）：
  #   gates.sh（本判据字面量）、check_reviews.py（旧→新映射 + 台账路径豁免表）、
  #   migration-r04.md（迁移台账）；
  #   README 映射/历史行（knowledge/README、dossiers/factors/README、runs/README）——行级；
  #   strategies 两文件历史注记 + 「旧路径不得出现」回归断言——行级；
  #   pending-items #16「原表述保留如下」历史引文、workspace-p0p8 R24 前 worktree 盘点的行级；
  #   platform 测试注释 `results/parquet I/O`（纯文本非路径）行级。
  path_hits=$(legacy_scan paths \
      | grep -vE '^governance/ops/(gates\.sh|check_reviews\.py):' \
      | grep -vE '^governance/workspace/migration-r04\.md:' \
      | grep -vE '^knowledge/README\.md:[0-9]+:> `research/docs/`' \
      | grep -vE '^knowledge/README\.md:[0-9]+:\| 研究设计/计划（原 `research/docs/' \
      | grep -vE '^knowledge/dossiers/factors/README\.md:[0-9]+:> \*\*R24 路径映射' \
      | grep -vE '^knowledge/dossiers/factors/README\.md:[0-9]+:> 索引 `docs/index' \
      | grep -vE '^knowledge/dossiers/factors/README\.md:[0-9]+:`platform/results/<name>/summary\.json`' \
      | grep -vE '^knowledge/dossiers/factors/README\.md:[0-9]+:\| `platform/results/`' \
      | grep -vE '^runs/README\.md:[0-9]+:> R24' \
      | grep -vE '^research/tools/strategies/run_strategy\.py:[0-9]+:.*此前硬编码' \
      | grep -vE '^research/tools/strategies/tests/test_run_strategy_cli\.py:[0-9]+:.*旧硬编码' \
      | grep -vE '^research/tools/strategies/tests/test_run_strategy_cli\.py:[0-9]+:.*assert "platform/results" not in' \
      | grep -vE '^governance/workspace/pending-items\.md:[0-9]+:.*研究侧写盘已收敛到 `research/tools/lib/writekit\.py`' \
      | grep -vE '^governance/workspace/workspace-p0p8\.md:[0-9]+:.*（research worktree）' \
      | grep -vE '^platform/tests/test_architecture\.py:[0-9]+:.*results/parquet I/O 单点门' \
      || true)
  n=$(printf '%s\n' "$path_hits" | grep -c . || true)
  if [ "$n" = "0" ]; then ok "已迁路径活引用零残留（R06-M9+R07：docs/*、runs 旧路径、工具旧路径、emb、裸 results/；tracked+untracked）";
  else bad "已迁路径活引用仍有 $n 处"; printf '%s\n' "$path_hits" | head -5 | sed 's/^/      /'; fi
  echo "[G-PATHS] pyproject / pytest 路径自洽"
  grep -q 'pythonpath = \["src"\]' "$PLATFORM/pyproject.toml" && ok "platform pythonpath=src" || bad "platform pythonpath 异常"
  [ -d "$PLATFORM/src/factorlab" ] && ok "platform/src/factorlab 存在" || bad "platform/src/factorlab 缺失"
  grep -q 'testpaths' "$RESEARCH/pyproject.toml" && ok "research 有 testpaths" || bad "research pyproject 缺 testpaths"
  [ -f "$ROOT/pyproject.toml" ] && bad "根目录不应有 pyproject.toml（防 rootdir 抢占）" || ok "根无 pyproject.toml"

  echo "[G-IMPORTS] 全仓 factorlab.* 导入可解析（含负向自检）"
  if "$PLATFORM/.venv/bin/python" governance/ops/check_imports.py --selftest >/dev/null 2>&1; then ok "自检通过（能抓到迁移遗漏）"; else bad "自检失败——门失效"; fi
  if out=$("$PLATFORM/.venv/bin/python" governance/ops/check_imports.py 2>&1); then ok "$(echo "$out" | tail -1)"; else bad "导入解析失败"; echo "$out" | head -8 | sed 's/^/      /'; fi

  if [ "$OFFLINE" = "1" ]; then
    echo "[G-INDEX] 因子/策略/composite 索引与生成器一致 · spec↔档案成对"
    skip_offline "G-INDEX（三索引 + 成对门）" "需要研究产物区（spec/档案/索引）：$PRODUCT_ROOT"
    echo "[G-ANNOTATE] 因子档案 snapshot 标注齐备（R21 约定）"
    skip_offline "G-ANNOTATE（档案 snapshot）" "档案在产物区：$PRODUCT_ROOT/dossiers"
    echo "[G-LOCKBOX] 样本声明与锁箱登记一致（离线：仅格式校验）"
    if [ ! -d "$PRODUCT_ROOT" ]; then
      skip_offline "G-LOCKBOX（格式校验）" "研究产物区不在盘：$PRODUCT_ROOT"
    elif out=$("$PLATFORM/.venv/bin/python" governance/ops/check_lockbox.py --offline --root "$PRODUCT_ROOT" 2>&1); then ok "$out"; else bad "$out"; fi
    skip_offline "G-LOCKBOX 台账交叉核对（宿主段）" "锁箱登记在 $PRODUCT_ROOT/data/ledger.sqlite（access_id kind/window 核验需宿主）"
    echo "[G-REVIEWS] 评审台账口径（ID 唯一/状态词表/引用路径/统计实计）"
    skip_offline "G-REVIEWS（评审台账）" "台账引用 runs/ 产物，干净 checkout/离线不可解析"
  else
    echo "[G-INDEX] 因子索引与生成器一致"
    if out=$("$PLATFORM/.venv/bin/python" research/tools/factor_lib/build_index.py --check 2>&1); then ok "$out"; else bad "索引不一致：$out"; fi

    echo "[G-INDEX] 策略索引与生成器一致 · spec↔档案成对"
    if out=$("$PLATFORM/.venv/bin/python" research/tools/factor_lib/build_strategy_index.py --check 2>&1); then ok "$out"; else bad "策略索引不一致：$out"; fi

    echo "[G-INDEX] composite 索引与生成器一致 · spec↔档案成对"
    if out=$("$PLATFORM/.venv/bin/python" research/tools/factor_lib/build_composite_index.py --check 2>&1); then ok "$out"; else bad "composite 索引不一致：$out"; fi

    echo "[G-ANNOTATE] 因子档案 snapshot 标注齐备（R21 约定）"
    # 脚本原位在 R21/EVID（证据即工具）；只做只读 --check，不写档案。
    # R06-M5：改平台 venv 解释器（原系统 python3=anaconda 3.10，与单解释器声明不符）。
    if out=$("$PLATFORM/.venv/bin/python" governance/evidence/verification/R21/EVID/annotate_factor_archives.py --check 2>&1); then ok "$out"; else bad "$out"; fi

    echo "[G-LOCKBOX] 样本声明与锁箱登记一致"
    # 权威层 = campaign 级 results/<dir>/manifest.json + dossiers/factors/**；台账 = $PRODUCT_ROOT/data/ledger.sqlite。
    if out=$("$PLATFORM/.venv/bin/python" governance/ops/check_lockbox.py --root "$PRODUCT_ROOT" 2>&1); then ok "$out"; else bad "$out"; fi
    if "$PLATFORM/.venv/bin/python" governance/ops/check_lockbox.py --selftest >/dev/null 2>&1; then
      ok "负向自检通过（缺字段/空 id/幽灵 id/探索冒充终评/档案缺声明；干净样本不误伤）"
    else bad "G-LOCKBOX 负向自检失败"; fi

    echo "[G-REVIEWS] 评审台账口径（ID 唯一/状态词表/引用路径/统计实计）"
    if out=$("$PLATFORM/.venv/bin/python" governance/ops/check_reviews.py 2>&1); then
      ok "$(echo "$out" | sed -n '2p' | sed 's/^ *✓ *//')"
      info "$(echo "$out" | sed -n '3p' | sed 's/^ *· *//')"
      info "$(echo "$out" | sed -n '4p' | sed 's/^ *· *//')"
    else
      bad "台账门失败"; echo "$out" | sed 's/^/      /'
    fi
    if out=$("$PLATFORM/.venv/bin/python" governance/ops/check_reviews.py --selftest 2>&1); then
      ok "负向自检通过（重复 ID/非法状态/死路径/空修复说明/统计不符）"
    else bad "自检失败——门失效"; echo "$out" | sed 's/^/      /'; fi
  fi

  echo "[G-LINT] 全库因子 spec lint（单进程批跑；挖矿在途 spec 一并计入）"
  # 失败行含具体 spec 路径——在途红与代码级红按文件区分，不误报为门故障。
  if [ "$OFFLINE" = "1" ] && [ ! -d "$PRODUCT_ROOT/factor" ]; then
    skip_offline "G-LINT（全库因子 spec lint）" "研究产物区不在盘：$PRODUCT_ROOT/factor"
  elif out=$("$PLATFORM/.venv/bin/factorlab" lint --all 2>&1); then ok "$(echo "$out" | tail -1)"; else
    bad "lint 有失败（在途/存量按下列文件区分）"; echo "$out" | sed 's/^/      /'; fi

  echo "[G-VENV] 平台 editable 落位断言"
  resolved=$("$PLATFORM/.venv/bin/python" -c "import factorlab;print(factorlab.__file__)" 2>/dev/null || echo "")
  case "$resolved" in
    "$ROOT/platform/src/factorlab/__init__.py") ok "factorlab → platform/src ✓" ;;
    "") bad "平台 venv 无法 import factorlab" ;;
    *) bad "factorlab 解析到 $resolved（应为 platform/src）" ;;
  esac
  # R30 Task 15（D12）：评估内核并入 platform/src/factorlab/core/eval/kernel.py——
  # 反向断言独立 quant-core dist/旧 editable finder（projects/）不得复活。
  qc=$("$PLATFORM/.venv/bin/python" -c "import quant_core;print(quant_core.__file__)" 2>/dev/null || echo "")
  if [ -n "$qc" ]; then bad "quant_core 仍可 import（$qc）——独立内核 dist 应已删除（D12）"; else ok "quant_core 独立包已删除（内核并入 factorlab）✓"; fi
}

# ── 数据接口门（R4 前为报告模式）────────────────────────────────
topo() {
  # R11：工具拓扑（依赖方向 / 横向耦合）。判据与自检见 governance/ops/check_tool_layering.py。
  # R06-M5：平台 venv 缺失时**响亮失败**（不再 fallback 系统 python3=anaconda）。
  PY=${PLATFORM}/.venv/bin/python
  if [ ! -x "$PY" ]; then bad "平台 venv 缺失：$PY（单解释器纪律：不回落系统 python3）"; return; fi
  "$PY" governance/ops/check_tool_layering.py || FAIL=1
  "$PY" governance/ops/check_tool_layering.py --selftest || FAIL=1
}

dataiface() {
  # R8c：从"报告模式 grep 计数"升级为 AST 判定——
  #   ENFORCED：工具树分区字面量（`year=` 只许 partitions 产出）、标记路径构造（只许 writekit）；
  #   REPORT  ：平台表名字面量（未竟 #12①:70 处，2026-09-16 实测）、工具树直读（未竟 #13）。
  # 由 Python 侧统一输出（含负向自检：--selftest 保证门不是死的）。
  # R06-M5：平台 venv 缺失时响亮失败（同上，不回落系统 python3）。
  PY=${PLATFORM}/.venv/bin/python
  if [ ! -x "$PY" ]; then bad "平台 venv 缺失：$PY（单解释器纪律：不回落系统 python3）"; return; fi
  if ! "$PY" governance/ops/check_dataiface.py; then
    FAIL=1
  fi
  "$PY" governance/ops/check_dataiface.py --selftest || FAIL=1
}

refaudit() {
  # R37-REF-I2（#31）：参考库基础指标审计——结构（sidecar 存在/产物齐全/指纹未过期）
  # 必须绿；门槛违规按 `_reference_policy.yaml` enforcement（report 报告 / enforce 失败）。
  echo "[G-REF] 参考库基础指标审计"
  if [ "$OFFLINE" = "1" ]; then
    skip_offline "G-REF（参考库 sidecar/指纹）" "侧车与产物在产物区：$PRODUCT_ROOT/results"
    return
  fi
  PY=${PLATFORM}/.venv/bin/python
  if [ ! -x "$PY" ]; then bad "平台 venv 缺失：$PY（单解释器纪律：不回落系统 python3）"; return; fi
  "$PY" research/tools/factor_lib/reference_audit.py --check || FAIL=1
}

echo "== stock gates =="
if [ "$OFFLINE" = "1" ]; then
  echo "[G-OFFLINE] 离线模式：SKIP 依赖研究产物区/台账的门（G-INDEX / G-ANNOTATE / G-REVIEWS / G-REF sidecar；"
  echo "[G-OFFLINE] G-LINT 无产物区时 SKIP），静态门照跑；SKIP 原因逐条打印 [G-OFFLINE]。"
fi
case "$MODE" in
  --structure) structure ;;
  --dataiface) dataiface ;;
  --topo) topo ;;
  --ref) refaudit ;;
  *) structure; echo; topo; echo; dataiface; echo; refaudit ;;
esac
echo
if [ "$FAIL" = "0" ]; then echo "结构门：全绿"; else echo "结构门：有失败（见上）"; fi
echo "数据接口门：ENFORCED 判定（AST）+ REPORT 未竟计数（见 governance/ops/check_dataiface.py）"
exit $FAIL
