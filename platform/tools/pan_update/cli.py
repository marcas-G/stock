"""pan_update CLI（Plan P T9）：`sync|build|publish|verify|all`。

设计：knowledge/design/workspace/2026-09-16-pan-data-update-design.md §3/§5/§8
用法：
    platform/.venv/bin/python platform/tools/pan_update/cli.py all [--dry-run] \
        [--categories daily,minutes,fund_flow,financials] [--prune] [--workers N]

T9 裁决（brief 未逐字覆盖处，报告同录）：
- **build 与 publish 共用同一阶段标记**（phase="build"）：T5-T8 为每类别收口一条
  完整链（含 CH 灌入=发布面），T4 转接项要求「同一 phase 跑完整链」；publish 作为
  设计 CLI 动词保留，幂等跳过，不重放链。`all` = sync → build → publish(跳过) → verify。
- **freshness 闩锁**：sync 有 downloaded 非空 → 清该类别 `stages[cat]` 全部标记，
  下次 build 重跑整链；无新增则阶段跳过（命令不再执行）。
- **state_path 固定** `data/raw/pan_state.json`；每成功文件原子落盘（T3 断点续跑）。
- **内存护栏**：`FACTORLAB_MAX_MEMORY` 显式设置 → 复用 `factorlab.app.memory`
  的 RLIMIT_AS 公式落进程级硬限（子进程继承）；env 白名单显式透传（`run_cmd` 以
  `{**os.environ, **env}` 合并，非白名单项经父环境继承）。
- **cookie**：单点 = 仓根 `quark_cookies.txt`。未显式设 `QUARK_COOKIE_FILE` 且仓根文件在
  → 启动即接线 env 并刷新 `quark_client` 常量；sync/all 校验
  （`quark_client.cookies()`），缺失/空 → exit 2 且文案含 `quark_cookies.txt` 路径；
  build/publish/verify 离线可跑。
- **manual 就位接续（修复轮 1）**：sync 前扫 dest_root——分享清单存在 + 本地已有 +
  size 匹配 + state 未登记 → 记 `adopted`（不取链/不下载），视同新数据清阶段标记。
- **manual_required 不为错**（exit 0）；failed 非空 → exit 1；未知类别 → exit 2。
- **超限转存回退**（2026-09-17）：size limit 项默认经 `transfer.DriveTransfer`
  转存自有盘 `factorlab_tmp` 后自取直链下载（`--no-transfer` 关闭维持 manual、
  `--keep-drive-copy` 保留网盘副本；失败 loud 不误删，见 transfer.py）。
- **--prune**：删除本地 raw 中不在本次分享清单内的残留文件（默认保留）。
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Callable

_TOOLS = Path(__file__).resolve().parents[1]
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from pan_update import config, share, stages, sync  # noqa: E402
from pan_update import state as st  # noqa: E402
from pan_update import transfer  # noqa: E402
from quark_download import quark_client  # noqa: E402

STATE_PATH = config.RAW_ROOT / "pan_state.json"
LOCK_PATH = config.RAW_ROOT / "pan_update.lock"
RAW_ROOT = config.RAW_ROOT
RECONCILE = config.repo_root() / "platform" / "tools" / "ch_ingest" / "reconcile.py"
VENV_PYTHON = config.repo_root() / "platform" / ".venv" / "bin" / "python"

# 子命令 env 白名单（其余继承 os.environ；见 run_cmd 的 full_env 合并）
ENV_KEYS = ("FACTORLAB_DATA_BACKEND", "FACTORLAB_MAX_MEMORY",
            "FACTORLAB_MIN_AVAILABLE_MEMORY", "PYARROW_JEMALLOC")


class CookieMissing(Exception):
    """cookie 缺失/为空：配置错误（exit 2），不静默空 Cookie 打转（设计 §5/§8）。"""


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _select_categories(spec: str | None) -> list[str]:
    if spec is None:
        return list(config.CATEGORIES)
    cats = [c.strip() for c in spec.split(",") if c.strip()]
    if not cats:
        raise ValueError("--categories 为空")
    unknown = [c for c in cats if c not in config.CATEGORIES]
    if unknown:
        raise ValueError(
            f"未知类别 {unknown}；可选：{', '.join(config.CATEGORIES)}")
    return list(dict.fromkeys(cats))


def apply_memory_guard(env) -> int | None:
    """`FACTORLAB_MAX_MEMORY` 显式设置 → RLIMIT_AS 硬上限（复用平台公式）。

    未设置 → None（不动进程资源）；非法值 → ValueError（由 main 归为 exit 2）。
    """
    spec = (env.get("FACTORLAB_MAX_MEMORY") or "").strip()
    if not spec:
        return None
    from _env import ensure_platform
    ensure_platform()
    from factorlab.app.memory import apply_address_space_limit, parse_memory
    return apply_address_space_limit(parse_memory(spec))


def _stage_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in ENV_KEYS if os.environ.get(k)}
    # T10 实测（R30/T10 根因）：40 核下多线程 glibc malloc 预留 ~18GB VA，
    # 24GiB RLIMIT_AS（=3×FACTORLAB_MAX_MEMORY）内 ingest_daily 的 Arrow IPC
    # malloc 失败（VmPeak 26.0GB → arena 上限 2 时 16.4GB）。显式设置优先。
    env["MALLOC_ARENA_MAX"] = os.environ.get("MALLOC_ARENA_MAX") or "2"
    return env


def _wire_cookie_env() -> None:
    """设计/手册单点：仓根 `quark_cookies.txt`。

    未显式设 `QUARK_COOKIE_FILE` 且仓根文件存在 → 接线 env（子进程继承）并刷新
    `quark_client` import 期快照的 `COOKIE_PATH`（模块已加载，仅 setdefault 环境变量
    不会改变常量）。已显式设置 env 时不覆盖（显式优先）。
    """
    if os.environ.get("QUARK_COOKIE_FILE") or not config.COOKIE_PATH.exists():
        return
    os.environ.setdefault("QUARK_COOKIE_FILE", str(config.COOKIE_PATH))
    quark_client.COOKIE_PATH = str(config.COOKIE_PATH)


def _check_cookie() -> None:
    try:
        text = quark_client.cookies()
    except FileNotFoundError as ex:
        raise CookieMissing(
            f"夸克 cookie 不可用：{ex}。请更新 {config.COOKIE_PATH}"
            f"（quark_cookies.txt，chmod 600；QUARK_COOKIE_FILE 可覆盖）") from ex
    if not text.strip():
        raise CookieMissing(
            f"夸克 cookie 为空：{config.COOKIE_PATH}（quark_cookies.txt，请重新导出）")


def _category_entries(listdir: Callable, cat: str):
    """root_fid=0 → level2_detail → 类别目录（share_dir 精确匹配）→ 递归文件清单。"""
    root_fid = share.find_dir(listdir, "0", config.SHARE_ROOT_DIR)
    cat_fid = share.find_dir(listdir, root_fid, config.CATEGORIES[cat].share_dir)
    return share.iter_category(listdir, cat_fid)


def _dest_root(raw_root: Path, cat: str) -> Path:
    rel = config.CATEGORIES[cat].local_root.relative_to(config.RAW_ROOT)
    return Path(raw_root) / rel


def _prune_extras(dest_root: Path, entries, *, dry_run: bool,
                  log: Callable[[str], None]) -> list[str]:
    """删除 dest_root 下不在分享清单内的文件（返回删除/待删 rel_path 列表）。"""
    keep = {e["rel_path"] if isinstance(e, dict) else e.rel_path for e in entries}
    removed: list[str] = []
    if not dest_root.is_dir():
        return removed
    for p in sorted(dest_root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(dest_root).as_posix()
        if rel in keep:
            continue
        removed.append(rel)
        if dry_run:
            log(f"[prune] 将删除本地残留 {rel}（--dry-run 不删）")
        else:
            p.unlink()
            log(f"[prune] 删除本地残留 {rel}")
    if not dry_run:
        for d in sorted(dest_root.rglob("*"), reverse=True):
            with contextlib.suppress(OSError):
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()
    return removed


def _print_report(cat: str, rep: sync.SyncReport, removed: list[str],
                  dry_run: bool) -> None:
    print(f"[{cat}] to_fetch={len(rep.to_fetch)} downloaded={len(rep.downloaded)} "
          f"adopted={len(rep.adopted)} unchanged={len(rep.unchanged)} "
          f"manual={len(rep.manual)} failed={len(rep.failed)}", flush=True)
    if dry_run:
        for rel in rep.to_fetch:
            print(f"  fetch  {rel}", flush=True)
    else:
        for rel in rep.downloaded:
            print(f"  got    {rel}", flush=True)
    for rel in rep.adopted:
        print(f"  adopt  {rel}", flush=True)
    for rel in removed:
        suffix = "（--dry-run 不删）" if dry_run else "（已删除）"
        print(f"  prune  {rel}{suffix}", flush=True)


def _print_summary(downloaded: int, adopted: int, manual: list, failed: list) -> None:
    if manual:
        print(f"manual_required（{len(manual)} 项；转存回退不可用或已 --no-transfer，"
              f"放入对应 data/raw/<类别> 后重跑）：", flush=True)
        for cat, item, dest_root in manual:
            print(f"  [{cat}] {item['name']}（{item['reason']}）→ {dest_root}",
                  flush=True)
    if failed:
        print(f"failed（{len(failed)} 项）：", flush=True)
        for cat, item in failed:
            print(f"  [{cat}] {item['name']}：{item['reason']}", flush=True)
    print(f"总结：downloaded={downloaded} adopted={adopted} "
          f"manual={len(manual)} failed={len(failed)}", flush=True)


def _run_sync(cats: list[str], state: dict, deps, *, dry_run: bool, prune: bool,
              workers: int) -> tuple[int, int, int, list, list]:
    """逐类别差集同步；返回 (rc, downloaded 总数, adopted 总数, manual, failed)。"""
    rc = 0
    n_downloaded = 0
    n_adopted = 0
    manual: list = []
    failed: list = []
    transport = deps["transport"]
    for cat in cats:
        log = stages.open_log(cat, datetime.date.today(), log_dir=deps["log_dir"])
        entries = _category_entries(deps["listdir"], cat)
        dest_root = _dest_root(deps["raw_root"], cat)
        rep = sync.sync_category(
            state, cat, entries=entries,
            transport=transport if transport is not None else sync.QuarkTransport(),
            dest_root=dest_root, dry_run=dry_run, workers=workers,
            state_path=None if dry_run else deps["state_path"],
            transfer=deps.get("transfer"))
        removed: list[str] = []
        if prune:
            removed = _prune_extras(dest_root, entries, dry_run=dry_run, log=log)
        if (rep.downloaded or rep.adopted) and not dry_run:
            state.setdefault("stages", {})[cat] = {}
            log(f"[freshness] 有新增/变更 {len(rep.downloaded)} 项 / "
                f"人工就位 {len(rep.adopted)} 项 → 清阶段标记")
        n_downloaded += len(rep.downloaded)
        n_adopted += len(rep.adopted)
        manual.extend((cat, it, dest_root) for it in rep.manual)
        failed.extend((cat, it) for it in rep.failed)
        _print_report(cat, rep, removed, dry_run)
        if rep.failed:
            rc = 1
        if not dry_run:
            st.save_state_atomic(deps["state_path"], state)
    return rc, n_downloaded, n_adopted, manual, failed


def _run_stages(cats: list[str], state: dict, deps, *, verb: str, dry_run: bool) -> None:
    """跑类别处理链。phase 恒为 "build"（build/publish 同一阶段标记，见模块 docstring）。"""
    env = _stage_env()
    for cat in cats:
        log = stages.open_log(cat, datetime.date.today(), log_dir=deps["log_dir"])
        if dry_run:
            chain = stages.STAGE_CHAINS.get(cat, [])
            print(f"[{cat}] 将执行 {verb} 链 {len(chain)} 步（--dry-run 不执行）",
                  flush=True)
            continue
        ran = stages.run_category_stage(state, cat, "build", runner=deps["runner"],
                                        log=log, env=env)
        print(f"[{cat}] 阶段 {verb}：{'执行完成' if ran else '已标记，跳过'}",
              flush=True)
        st.save_state_atomic(deps["state_path"], state)


def _run_verify(cats: list[str], deps, *, dry_run: bool) -> int:
    """reconcile 全量；daily 类别且有当天 staged clean → 传 ``--source``（I1 收口）。

    staged 缺失/未查 daily → 保持 raw 口径（向后兼容）。
    """
    cmd = [str(VENV_PYTHON), str(RECONCILE)]
    staged = deps.get("staging_path")
    if "daily" in cats and staged is not None and Path(staged).is_file():
        cmd += ["--source", str(staged)]
    log = stages.open_log("verify", datetime.date.today(), log_dir=deps["log_dir"])
    log(f"$ {shlex.join(cmd)}")
    print(f"$ {shlex.join(cmd)}", flush=True)
    if dry_run:
        return 0
    rc = int(deps["verify_runner"](cmd))
    print(f"  reconcile：{'全库一致' if rc == 0 else '存在差异'}（rc={rc}）", flush=True)
    return rc


def _dispatch(args, cats, state, deps) -> int:
    verb = args.command
    if verb == "sync":
        rc, n_dl, n_ad, manual, failed = _run_sync(
            cats, state, deps, dry_run=args.dry_run, prune=args.prune,
            workers=args.workers)
        _print_summary(n_dl, n_ad, manual, failed)
        return rc
    if verb in ("build", "publish"):
        _run_stages(cats, state, deps, verb=verb, dry_run=args.dry_run)
        return 0
    if verb == "verify":
        return _run_verify(cats, deps, dry_run=args.dry_run)
    # all
    rc, n_dl, n_ad, manual, failed = _run_sync(
        cats, state, deps, dry_run=args.dry_run, prune=args.prune,
        workers=args.workers)
    _run_stages(cats, state, deps, verb="build", dry_run=args.dry_run)
    if args.dry_run:
        _run_verify(cats, deps, dry_run=True)
    else:
        _run_stages(cats, state, deps, verb="publish", dry_run=False)
        rc = max(rc, _run_verify(cats, deps, dry_run=False))
    _print_summary(n_dl, n_ad, manual, failed)
    return rc


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="pan_update", description=__doc__)
    ap.add_argument("command", choices=["sync", "build", "publish", "verify", "all"],
                    help="sync=差集下载；build=处理链（含 CH 灌入）；"
                         "publish=build 同义（幂等）；verify=reconcile 全量；all=全链")
    ap.add_argument("--categories", default=None,
                    help=f"逗号分隔（缺省全部：{','.join(config.CATEGORIES)}）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印清单/计划，不下载、不执行、不写 state")
    ap.add_argument("--prune", action="store_true",
                    help="删除本地 raw 中不在分享清单内的残留（默认保留）")
    ap.add_argument("--workers", type=int, default=8,
                    help="下载并发上限（默认 8；1=串行）")
    ap.add_argument("--transfer", dest="transfer", action="store_true", default=True,
                    help="超限文件自动「转存自有网盘 → 自取直链」下载（默认启用）")
    ap.add_argument("--no-transfer", dest="transfer", action="store_false",
                    help="关闭转存回退：超限文件维持 manual_required")
    ap.add_argument("--keep-drive-copy", action="store_true",
                    help="下载校验通过后保留网盘临时副本（默认删除清空间）")
    return ap


def main(argv: list[str] | None = None, *, listdir: Callable | None = None,
         transport=None, runner: Callable | None = None,
         verify_runner: Callable[[list[str]], int] | None = None,
         state_path: Path | None = None, lock_path: Path | None = None,
         log_dir: Path | None = None, raw_root: Path | None = None,
         transfer_client=None, staging_path: Path | None = None) -> int:
    """CLI 入口；返回进程退出码（0 成功 / 1 运行失败 / 2 用法或配置错误）。

    注入面（测试/装配用，生产留 None）：listdir、transport、runner、verify_runner、
    state_path、lock_path、log_dir、raw_root、transfer_client（转存回退客户端；
    缺省且 --transfer 开启时装配生产 ``transfer.DriveTransfer``）、staging_path
    （当天 clean staging 文件；verify 的 daily 类别据此传 reconcile ``--source``）。
    """
    raw_argv = list(argv) if argv is not None else list(sys.argv[1:])
    _wire_cookie_env()
    args = _parser().parse_args(raw_argv)
    try:
        cats = _select_categories(args.categories)
    except ValueError as ex:
        print(f"参数错误：{ex}", file=sys.stderr)
        return 2
    if args.workers < 1:
        print("参数错误：--workers 必须 ≥ 1", file=sys.stderr)
        return 2
    try:
        apply_memory_guard(os.environ)
    except ValueError as ex:
        print(f"参数错误：FACTORLAB_MAX_MEMORY 非法（{ex}）", file=sys.stderr)
        return 2

    deps = {
        "listdir": listdir if listdir is not None else share._default_listdir,
        "transport": transport,
        "runner": runner if runner is not None else stages.run_cmd,
        "verify_runner": (verify_runner if verify_runner is not None
                          else lambda cmd: subprocess.run(cmd).returncode),
        "state_path": Path(state_path) if state_path is not None else STATE_PATH,
        "log_dir": Path(log_dir) if log_dir is not None else stages.LOG_DIR,
        "raw_root": Path(raw_root) if raw_root is not None else RAW_ROOT,
        "transfer": transfer_client,
        "staging_path": (Path(staging_path) if staging_path is not None
                         else stages._DAILY_STAGING),
    }
    if args.command in ("sync", "all"):
        if not args.transfer:
            deps["transfer"] = None
        elif deps["transfer"] is None:
            deps["transfer"] = transfer.DriveTransfer(
                transfer.QuarkPcTransport(), keep_copy=args.keep_drive_copy)
    state = st.load_state(deps["state_path"])
    started = _now()
    ran = False
    rc = 1
    err: str | None = None
    try:
        if args.command in ("sync", "all"):
            _check_cookie()
        if args.dry_run:
            rc = _dispatch(args, cats, state, deps)
        else:
            lock = Path(lock_path) if lock_path is not None else LOCK_PATH
            with stages.single_instance(lock):
                ran = True
                rc = _dispatch(args, cats, state, deps)
    except CookieMissing as ex:
        print(f"错误：{ex}", file=sys.stderr)
        return 2
    except stages.StageError as ex:
        err = f"{ex.stage}: rc={ex.rc}"
        print(f"错误：{ex}", file=sys.stderr)
        rc = 1
    except (KeyError, RuntimeError, OSError, ValueError) as ex:
        err = str(ex)
        print(f"错误：{ex}", file=sys.stderr)
        rc = 1
    if not args.dry_run and ran:
        state.setdefault("runs", []).append({
            "started_at": started, "cmd": " ".join(raw_argv),
            "status": "ok" if rc == 0 else "failed", "error": err,
        })
        with contextlib.suppress(OSError):
            st.save_state_atomic(deps["state_path"], state)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
