#!/usr/bin/env bash
# R09 分钟链性能基线复测（before/after 可复现脚本；M3 分段计时）
#
# 用法（仓库根运行）：
#   bash governance/evidence/verification/R31/minute-perf/bench.sh
#
# 默认：4 个因子（简单 am_pm_vol + R09 点名病态 vol_asym/autocorr_micro/
# vol_price_corr），窗口 2024-01-02..2024-03-29（≈58 交易日，见 timings.md
# 「窗口选择」：R09 原窗 2023-01-01..2025-12-31 的 723 交易日下病态因子
# ≥15min 超时，无法在 25min/因子预算内完成；缩短窗口保证 before/after 同窗
# 可比，折算方法写入证据），`--chunk-days 10 --profile`，25min/因子超时，
# FACTORLAB_MAX_MEMORY=8GB 护栏 + heavy.sh 闸。
#
# 环境变量（均有默认）：
#   BENCH_OUT       输出目录（默认 .../R31/minute-perf/before）
#   BENCH_START/END 窗口（默认 2024-01-02 / 2024-03-29；BENCH_FULL=1 用 spec 原窗）
#   BENCH_TIMEOUT_S 单因子超时秒数（默认 1500 = 25min）
#   BENCH_CHUNK_DAYS 分块交易日（默认 10，R09 同口径）
#   BENCH_CHUNK_WORKERS 分钟链 chunk 并行度（默认 1=现行为；R09-PERF-P4）
#   BENCH_SKIP_DONE=1 已有 summary.json 的因子跳过（断点续跑）
#   BENCH_FACTORS   空格分隔的 spec 短名（默认 am_pm_vol vol_asym autocorr_micro
#                   vol_price_corr；文件须在 research/factor/intraday/<名>.yaml）
#
# 每个因子产出：<out>/runs/<name>/summary.json（含 runtime.profile）、run.log
# （stderr 人读分段）、time.txt（/usr/bin/time -v 外部峰值 RSS）、exit_code；
# 汇总渲染 <out>/timings.md。退出码：任一因子 timeout/error 记入表，脚本仍
# 以 0 退出（如实记录非零为证据；硬失败只发生在环境错误）。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [ ! -d "$ROOT/research/factor" ] && [ "$ROOT" != "/" ]; do
    ROOT="$(dirname "$ROOT")"
done
if [ ! -d "$ROOT/research/factor" ]; then
    echo "bench.sh: 未找到仓库根（research/factor）" >&2
    exit 2
fi

OUT="${BENCH_OUT:-$ROOT/governance/evidence/verification/R31/minute-perf/before}"
START="${BENCH_START:-2024-01-02}"
END="${BENCH_END:-2024-03-29}"
TIMEOUT_S="${BENCH_TIMEOUT_S:-1500}"
CHUNK_DAYS="${BENCH_CHUNK_DAYS:-10}"
WORKERS="${BENCH_CHUNK_WORKERS:-1}"
FACTORS="${BENCH_FACTORS:-am_pm_vol vol_asym autocorr_micro vol_price_corr}"
FULL="${BENCH_FULL:-0}"
SKIP_DONE="${BENCH_SKIP_DONE:-0}"

PY="$ROOT/platform/.venv/bin/python"
HEAVY="$ROOT/governance/ops/heavy.sh"
FACTORLAB="$ROOT/platform/.venv/bin/factorlab"
mkdir -p "$OUT/runs" "$OUT/specs"

# R09 复跑口径（report.md §3）+ 8GB 护栏（重任务协议）
export FACTORLAB_DATA_BACKEND=ch
export FACTORLAB_MAX_MEMORY=8GB
export FACTORLAB_MIN_AVAILABLE_MEMORY=6GB
export FACTORLAB_ST_DEGRADE=allow
export FACTORLAB_MINUTE_UNCOVERED=drop
export OMP_NUM_THREADS=8
export POLARS_MAX_THREADS=8

git_status="$(git -C "$ROOT" status --porcelain | wc -l)"
{
    echo "date=$(date -Iseconds)"
    echo "commit=$(git -C "$ROOT" rev-parse HEAD)"
    echo "dirty_files=$git_status"
    echo "window=$([ "$FULL" = "1" ] && echo "spec" || echo "$START..$END")"
    echo "chunk_days=$CHUNK_DAYS timeout_s=$TIMEOUT_S chunk_workers=$WORKERS"
    echo "backend=$FACTORLAB_DATA_BACKEND max_memory=$FACTORLAB_MAX_MEMORY"
    echo "st_degrade=$FACTORLAB_ST_DEGRADE minute_uncovered=$FACTORLAB_MINUTE_UNCOVERED"
    echo "host=$(hostname) cores=$(nproc) mem_gb=$(awk '/MemTotal/{printf "%.0f", $2/1024/1024}' /proc/meminfo)"
} > "$OUT/env.txt"

run_one() {
    local short="$1"
    local src="$ROOT/research/factor/intraday/$short.yaml"
    local run_dir="$OUT/runs/$short"
    local spec="$src"
    if [ ! -f "$src" ]; then
        echo "bench.sh: 缺 spec: $src" >&2
        return 2
    fi
    mkdir -p "$run_dir"
    if [ "$FULL" != "1" ]; then
        spec="$OUT/specs/${short}__${START}_${END}.yaml"
        "$PY" - "$src" "$spec" "$START" "$END" <<'PYEOF'
import sys, yaml
src, dst, start, end = sys.argv[1:5]
with open(src, encoding="utf-8") as fh:
    doc = yaml.safe_load(fh)
doc["date"] = {"start": start, "end": end}
with open(dst, "w", encoding="utf-8") as fh:
    yaml.safe_dump(doc, fh, allow_unicode=True, sort_keys=False)
PYEOF
    fi
    if [ "$SKIP_DONE" = "1" ] && [ -f "$run_dir/summary.json" ]; then
        echo "== $short: skip（已有 summary.json）"
        return 0
    fi
    rm -f "$run_dir/summary.json" "$run_dir/exit_code"
    echo "== $short: $(basename "$spec")（$(date -Iseconds)）"
    /usr/bin/time -v -o "$run_dir/time.txt" \
        timeout --signal=TERM --kill-after=60 "$TIMEOUT_S" \
        "$HEAVY" "$FACTORLAB" run "$spec" \
        --chunk-days "$CHUNK_DAYS" --chunk-workers "$WORKERS" \
        --profile --output-dir "$run_dir" \
        > "$run_dir/run.log" 2>&1
    local rc=$?
    echo "$rc" > "$run_dir/exit_code"
    case "$rc" in
        0) echo "   ok（$(date -Iseconds)）" ;;
        124|137) echo "   TIMEOUT（rc=$rc，$(date -Iseconds)）" ;;
        *) echo "   ERROR（rc=$rc，见 $run_dir/run.log）" ;;
    esac
    return 0
}

for f in $FACTORS; do
    run_one "$f"
done

# ---- 渲染 timings.md（从已收集产物重建，幂等） ----
"$PY" - "$OUT" "$TIMEOUT_S" "$CHUNK_DAYS" "$START" "$END" <<'PYEOF'
import json
import re
import sys
from pathlib import Path

out = Path(sys.argv[1])
timeout_s, chunk_days, start, end = int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]
SEGMENTS = ("read_data", "fold", "label", "evaluate", "layered_backtest",
            "persist")


def max_rss_mb(time_txt: Path):
    if not time_txt.is_file():
        return None
    m = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)",
                  time_txt.read_text(errors="replace"))
    return int(m.group(1)) // 1024 if m else None


def main():
    env = (out / "env.txt").read_text(encoding="utf-8").strip() if (
        out / "env.txt").is_file() else "(no env.txt)"
    _wm = re.search(r"chunk_workers=(\d+)", env)
    _workers = _wm.group(1) if _wm else None
    if "after-p4" in str(out):
        _phase = ("after-P4（R09-PERF-P4 chunk_workers=" + (_workers or "?")
                  + "）")
    elif out.name == "after":
        _phase = "after（R09-PERF-I1 融合）"
    elif out.name == "after-p3":
        _phase = "after（R09-PERF-I2 条件取值/单次 agg）"
    else:
        _phase = "before"
    lines = [
        f"# R09 分钟链性能{_phase}——M3 分段计时实测",
        "",
        f"- 运行口径：`--chunk-days {chunk_days} "
        f"--chunk-workers {_workers or 1} --profile`，单因子超时 "
        f"{timeout_s}s（{timeout_s / 60:.0f}min）；env：`FACTORLAB_DATA_BACKEND=ch "
        f"FACTORLAB_MAX_MEMORY=8GB FACTORLAB_ST_DEGRADE=allow "
        f"FACTORLAB_MINUTE_UNCOVERED=drop`；经 `governance/ops/heavy.sh` 闸。",
        "- 脚本：`governance/evidence/verification/R31/minute-perf/bench.sh`"
        "（`BENCH_OUT` 指向本目录；after 复测同脚本换目录）。",
        "",
        "## 窗口选择（与 R09 原窗差异）",
        "",
        f"- 本基线窗口：`{start}..{end}`（≈58 交易日 = Q1 2024；CH 实测 7.32e7 分钟行）。",
        "- R09 原窗：`2023-01-01..2025-12-31`（723 交易日 ≈ 8.4e8 行，"
        "`evidence/timings.md`）。病态三因子在 R09 窗 ≥15min 超时（timeout@900s），"
        "25min/因子预算内无法完成；**缩短窗口保证 before/after 同窗可比**。",
        "- 折算：分钟链工作量 ≈ O(交易日)（日内 240 行/组为常数），"
        "本窗 → R09 原窗约 ×12.5（723/58）；病态因子在 R09 窗只会更慢（超时）。"
        "基线绝对值仅在同一窗口内与 after 对比。",
        "",
        "## 环境/树状态（env.txt）",
        "",
        "```",
        env,
        "```",
        "",
        "## 实测（墙钟来自 summary.runtime.profile.wall_ms；外部峰值来自 "
        "/usr/bin/time -v）",
        "",
        "| 因子 | 状态 | 总墙钟 | read_data | fold | label | evaluate | backtest | persist | 进程峰值RSS |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    runs = out / "runs"
    names = ([p.name for p in sorted(runs.iterdir()) if p.is_dir()]
             if runs.is_dir() else [])
    any_missing = False
    for name in names:
        run = runs / name
        rc = (run / "exit_code").read_text().strip() if (
            run / "exit_code").is_file() else "?"
        rss = max_rss_mb(run / "time.txt")
        summary_p = run / "summary.json"
        if rc == "0" and summary_p.is_file():
            summary = json.loads(summary_p.read_text(encoding="utf-8"))
            profile = (summary.get("runtime") or {}).get("profile") or {}
            seg = profile.get("segments", {})
            total = profile.get("total_wall_ms")
            cells = []
            for key in SEGMENTS:
                v = seg.get(key)
                cells.append(f"{v['wall_ms']}ms" if v else "—")
            status = "ok"
            rows = summary.get("panel_rows")
            if rows is not None:
                status += f"（panel_rows={rows}, {summary.get('date_start')}..{summary.get('date_end')}）"
            lines.append("| {} | {} | {} | {} | {} |".format(
                name, status, f"{total}ms" if total is not None else "—",
                " | ".join(cells),
                f"{rss}MB" if rss is not None else "—"))
        else:
            any_missing = True
            status = ("TIMEOUT" if rc in ("124", "137") else f"ERROR(rc={rc})")
            tail = ""
            log = run / "run.log"
            if rc != "0" and log.is_file():
                last = [ln for ln in log.read_text(errors="replace").splitlines()
                        if ln.strip()][-1:]
                if last:
                    tail = last[0][:120].replace("|", "/")
            lines.append("| {} | {} {} | —（未完成） | — | — | — | — | — | — | {} |".format(
                name, status, tail, f"{rss}MB" if rss is not None else "—"))
    if any_missing:
        lines += ["", "> 未完成项：超时/失败因子的分段墙钟不可得（进程未走到落盘）；"
                  "外部峰值 RSS 与末行日志如实记录。"]
    (out / "timings.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"bench.sh: 已渲染 {out / 'timings.md'}（{len(names)} 个因子）")


main()
PYEOF
