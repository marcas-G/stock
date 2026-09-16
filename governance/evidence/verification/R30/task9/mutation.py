"""R30 Task9 存根突变检查：CLI/补链/护栏/doc 关键行为替换为存根或反向，测试必须失败。

不修改工作区最终状态：每个突变跑完立即恢复原文（最后逐字节断言与原文一致）。
复现：platform/.venv/bin/python governance/evidence/verification/R30/task9/mutation.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
PY = REPO / "platform" / ".venv" / "bin" / "python"

CLI = REPO / "platform/tools/pan_update/cli.py"
STAGES = REPO / "platform/tools/pan_update/stages.py"
PARSE = REPO / "platform/tools/pan_update/parse_fundamentals_xlsx.py"
MAKEFILE = REPO / "Makefile"
INSTALL = REPO / "governance/ops/install_pan_timer.sh"

T_CLI = "platform/tools/pan_update/tests/test_cli.py"

# name -> (path, old, new, tests)
MUTS = {
    # —— sync 装配（workers/state_path/dry-run/新鲜度）——
    "cli: 忽略 --workers（恒 1）": (
        CLI,
        "dest_root=dest_root, dry_run=dry_run, workers=workers,",
        "dest_root=dest_root, dry_run=dry_run, workers=1,", [T_CLI]),
    "cli: dry-run 仍真下载": (
        CLI,
        "dest_root=dest_root, dry_run=dry_run, workers=workers,",
        "dest_root=dest_root, dry_run=False, workers=workers,", [T_CLI]),
    "cli: 新文件不清阶段标记（闩锁失效）": (
        CLI, "if rep.downloaded and not dry_run:",
        "if rep.downloaded and False:", [T_CLI]),
    "cli: 新文件清标记语句删除": (
        CLI, '            state.setdefault("stages", {})[cat] = {}\n', "", [T_CLI]),
    "cli: 不传 state_path（断点记账丢失）": (
        CLI, '            state_path=None if dry_run else deps["state_path"])',
        "            state_path=None)", [T_CLI]),
    "cli: manual 也当失败（exit 1）": (
        CLI, "        if rep.failed:\n            rc = 1",
        "        if rep.manual or rep.failed:\n            rc = 1", [T_CLI]),
    "cli: failed 被吞（恒 exit 0）": (
        CLI, "        if rep.failed:\n            rc = 1",
        "        if False:\n            rc = 1", [T_CLI]),
    "cli: 未知类别不校验（放行进链）": (
        CLI, "    if unknown:", "    if False:", [T_CLI]),
    # —— 分享树接线 ——
    "cli: 跳过类别目录 find_dir（不按 share_dir 匹配）": (
        CLI, "    cat_fid = share.find_dir(listdir, root_fid, config.CATEGORIES[cat].share_dir)",
        "    cat_fid = root_fid", [T_CLI]),
    "cli: 缺省 listdir 换成空树存根": (
        CLI, '"listdir": listdir if listdir is not None else share._default_listdir,',
        '"listdir": listdir if listdir is not None else (lambda fid: []),', [T_CLI]),
    # —— cookie / 护栏 / env ——
    "cli: cookie 缺失以 exit 1（契约是 2）": (
        CLI,
        "    except CookieMissing as ex:\n"
        "        print(f\"错误：{ex}\", file=sys.stderr)\n        return 2",
        "    except CookieMissing as ex:\n"
        "        print(f\"错误：{ex}\", file=sys.stderr)\n        return 1", [T_CLI]),
    "cli: 不查 cookie（静默下网盘）": (
        CLI, '        if args.command in ("sync", "all"):\n            _check_cookie()\n',
        "", [T_CLI]),
    "cli: 未设内存上限也落 RLIMIT_AS": (
        CLI,
        'spec = (env.get("FACTORLAB_MAX_MEMORY") or "").strip()',
        'spec = (env.get("FACTORLAB_MAX_MEMORY") or "1GB").strip()', [T_CLI]),
    "cli: env 白名单不透传（空 env）": (
        CLI, "return {k: os.environ[k] for k in ENV_KEYS if os.environ.get(k)}",
        "return {}", [T_CLI]),
    # —— 阶段/verify ——
    "cli: publish 用另一 phase（重放链）": (
        CLI, 'stages.run_category_stage(state, cat, "build", runner=deps["runner"],',
        'stages.run_category_stage(state, cat, "publish", runner=deps["runner"],',
        [T_CLI]),
    "cli: verify 退出码被吞（恒 0）": (
        CLI, '    rc = int(deps["verify_runner"](cmd))',
        '    deps["verify_runner"](cmd)\n    rc = 0', [T_CLI]),
    "cli: verify 带表名参数（非全量）": (
        CLI, "    cmd = [str(VENV_PYTHON), str(RECONCILE)]",
        '    cmd = [str(VENV_PYTHON), str(RECONCILE), "daily"]', [T_CLI]),
    "cli: all 不跑 verify": (
        CLI, "        rc = max(rc, _run_verify(deps, dry_run=False))",
        "        _run_verify(deps, dry_run=False)", [T_CLI]),
    # —— 补链（stages）——
    "stages: fund_flow 链缺失": (
        STAGES,
        '    "fund_flow": [\n'
        '        [str(_VENV_PYTHON), str(_TOOLS / "ch_ingest" / "ingest_moneyflow.py")],\n'
        '    ],\n', "", [T_CLI]),
    "stages: financials 链顺序颠倒（先灌后解析）": (
        STAGES,
        '    "financials": [\n'
        '        [str(_VENV_PYTHON), str(_TOOLS / "pan_update" / "parse_fundamentals_xlsx.py")],\n'
        '        [str(_VENV_PYTHON), str(_TOOLS / "ch_ingest" / "ingest_fundamentals.py")],\n'
        '    ],',
        '    "financials": [\n'
        '        [str(_VENV_PYTHON), str(_TOOLS / "ch_ingest" / "ingest_fundamentals.py")],\n'
        '        [str(_VENV_PYTHON), str(_TOOLS / "pan_update" / "parse_fundamentals_xlsx.py")],\n'
        '    ],', [T_CLI]),
    # —— 解析入口（--raw-dir/--prev/轮换）——
    "parse: --raw-dir 被忽略（恒默认源）": (
        PARSE, "    src = find_latest_xlsx(args.raw_dir or DEFAULT_SRC)",
        "    src = find_latest_xlsx(DEFAULT_SRC)", [T_CLI]),
    "parse: --prev 被忽略（恒默认 .prev）": (
        PARSE, "    out = write_fact(df, args.out or DEFAULT_FACT, prev=args.prev)",
        "    out = write_fact(df, args.out or DEFAULT_FACT, prev=None)", [T_CLI]),
    "parse: write_fact 不轮换旧 fact": (
        PARSE, "    if out.exists():", "    if False:", [T_CLI]),
    # —— Makefile / 定时器脚本 ——
    "makefile: 去掉内存护栏": (
        MAKEFILE, "FACTORLAB_MAX_MEMORY=8GB $(PLATFORM_PY) platform/tools/pan_update/cli.py all",
        "$(PLATFORM_PY) platform/tools/pan_update/cli.py all", [T_CLI]),
    "makefile: data-update 只跑 sync": (
        MAKEFILE, "platform/tools/pan_update/cli.py all",
        "platform/tools/pan_update/cli.py sync", [T_CLI]),
    "timer: 定时从 08:10 改 09:10": (
        INSTALL, "OnCalendar=*-*-* 08:10:00", "OnCalendar=*-*-* 09:10:00", [T_CLI]),
    "timer: crontab 行时间改 08:00": (
        INSTALL, 'CRON_LINE="10 8 * * *', 'CRON_LINE="0 8 * * *', [T_CLI]),
}


def run_pytest(tests: list[str]) -> int:
    proc = subprocess.run([str(PY), "-m", "pytest", *tests, "-q"],
                          cwd=REPO, capture_output=True, text=True)
    return proc.returncode


def main() -> int:
    paths = {CLI, STAGES, PARSE, MAKEFILE, INSTALL}
    originals = {p: p.read_text(encoding="utf-8") for p in paths}
    caught = 0
    try:
        for name, (path, old, new, tests) in MUTS.items():
            src = originals[path]
            if old not in src:
                print(f"[HARNESS-ERROR] 替换串不存在：{name}")
                return 2
            path.write_text(src.replace(old, new, 1), encoding="utf-8")
            rc = run_pytest(tests)
            ok = rc != 0
            caught += ok
            print(f"{'CAUGHT' if ok else 'NOT-CAUGHT'}  rc={rc}  {name}")
            path.write_text(src, encoding="utf-8")
    finally:
        for path, src in originals.items():
            path.write_text(src, encoding="utf-8")
            assert path.read_text(encoding="utf-8") == src, f"恢复失败：{path}"
    print(f"\n{caught}/{len(MUTS)} 突变被测试抓住；工作区已恢复逐字节一致")
    rc = run_pytest([T_CLI])
    print(f"恢复后 T9 测试：rc={rc}")
    return 0 if caught == len(MUTS) and rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
