"""阶段编排：类别命令链、失败上抛、逐阶段记账续跑、flock 单实例、日志落盘。

设计：knowledge/design/workspace/2026-09-16-pan-data-update-design.md §3/§5/§8
- ``STAGE_CHAINS``：类别 → 命令序列（daily/minutes/fund_flow/financials 由 T5-T8 填实）。
  daily 链在 import_daily（raw → daily_fact）与 ingest_daily 之间插入
  ``data_quality/pipeline.py clean``（Plan DQ-M1 §5：CLEAN STAGING → PRE-INGEST GATE
  → CANONICAL INGEST）；clean 非零退出（PRE-INGEST FAIL）→ 阶段失败上抛，ingest 不执行、
  stage 标记不落。M1 只 clean 最新分区（增量硬门；全史体检是 T8 的只审不改职责）。
- ``run_category_stage``：同一 (类别, 阶段) 成功才落标记（值为 ISO 时间）；
  失败即停、标记不动，重跑从链头整链重放（链内每步须幂等，设计 §3/§8）。
- ``run_cmd``：子进程输出逐行喂给 log；非零退出抛 ``StageError(stage, cmd, rc, tail)``，
  tail 为末 ``TAIL_LINES`` 行输出。
- ``single_instance``：``fcntl.flock`` 非阻塞单实例；锁被占 → ``RuntimeError``（不等待）。
- 日志：``runs/platform/logs/pan_update-YYYYMMDD.log``，行前缀 ``[类别]``，追加不截断。
"""
from __future__ import annotations

import contextlib
import datetime
import fcntl
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Callable

from pan_update import config

TAIL_LINES = 20

LOG_DIR = config.repo_root() / "runs" / "platform" / "logs"

# 阶段链命令一律 [平台 venv 解释器, 脚本绝对路径]，路径经 config.repo_root() 派生
_VENV_PYTHON = config.repo_root() / "platform" / ".venv" / "bin" / "python"
_TOOLS = config.repo_root() / "platform" / "tools"

STAGE_CHAINS: dict[str, list[list[str]]] = {
    "daily": [
        [str(_VENV_PYTHON), str(_TOOLS / "ashare_ingest" / "import_daily.py")],
        [str(_VENV_PYTHON), str(_TOOLS / "data_quality" / "pipeline.py"),
         "clean", "--partition", "latest"],
        [str(_VENV_PYTHON), str(_TOOLS / "ch_ingest" / "ingest_daily.py")],
        [str(_VENV_PYTHON), str(_TOOLS / "ch_ingest" / "derive_stk_limit.py")],
        [str(_VENV_PYTHON), str(_TOOLS / "ch_ingest" / "adj_backfill.py")],
    ],
    "minutes": [
        [str(_VENV_PYTHON), str(_TOOLS / "converters" / "convert_minutes_to_parquet.py"),
         "--mode", "production"],
        [str(_VENV_PYTHON), str(_TOOLS / "ch_ingest" / "ingest_bars.py")],
    ],
    "fund_flow": [
        # 项 2：先解析 raw zip（zj/hyzj/gnzj/gn_detail）→ fact，再灌 CH moneyflow +
        # moneyflow_sector + concept_members（parse 落 fact 后 ingest 全量重算，两步幂等）
        [str(_VENV_PYTHON), str(_TOOLS / "pan_update" / "parse_fund_flow.py")],
        [str(_VENV_PYTHON), str(_TOOLS / "ch_ingest" / "ingest_moneyflow.py")],
    ],
    "financials": [
        [str(_VENV_PYTHON), str(_TOOLS / "pan_update" / "parse_fundamentals_xlsx.py")],
        [str(_VENV_PYTHON), str(_TOOLS / "ch_ingest" / "ingest_fundamentals.py")],
    ],
}


class StageError(Exception):
    """阶段命令失败：stage=阶段名，cmd=原命令，rc=退出码，tail=末尾输出。"""

    def __init__(self, stage: str, cmd: list[str], rc: int, tail: str):
        self.stage = stage
        self.cmd = list(cmd)
        self.rc = rc
        self.tail = tail
        super().__init__(f"阶段 {stage} 失败（rc={rc}）：{shlex.join(cmd)}\n{tail}")


def _stage_name(cmd: list[str]) -> str:
    for arg in cmd:
        if arg.endswith(".py"):
            return Path(arg).stem
    return Path(cmd[0]).name


def _noop(line: str) -> None:
    pass


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def run_cmd(cmd: list[str], *, log: Callable[[str], None], env: dict | None = None) -> None:
    """跑一条命令；输出逐行 ``log``；非零退出 → ``StageError``。"""
    if not cmd:
        raise ValueError("空命令：cmd 不能为空列表")
    stage = _stage_name(cmd)
    log(f"$ {shlex.join(cmd)}")
    full_env = {**os.environ, **(env or {})}
    lines: list[str] = []
    try:
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, bufsize=1, env=full_env) as proc:
            for raw in proc.stdout:
                line = raw.rstrip("\n")
                lines.append(line)
                log(line)
        rc = proc.returncode
    except OSError as ex:
        raise StageError(stage, cmd, -1, str(ex)) from ex
    if rc != 0:
        raise StageError(stage, cmd, rc, "\n".join(lines[-TAIL_LINES:]))


def run_category_stage(state: dict, category: str, phase: str, *,
                       runner: Callable[..., None] = run_cmd,
                       log: Callable[[str], None] | None = None,
                       env: dict | None = None) -> bool:
    """跑类别阶段链并记账；已标记 → no-op 返回 False，否则跑完标记返回 True。"""
    log = log or _noop
    marks = state.setdefault("stages", {}).setdefault(category, {})
    if marks.get(phase):
        log(f"[{category}] 阶段 {phase} 已标记，跳过")
        return False
    if category not in STAGE_CHAINS:
        raise KeyError(f"未配置阶段链：{category}（STAGE_CHAINS 待填实）")
    chain = STAGE_CHAINS[category]
    log(f"[{category}] 阶段 {phase} 开始（{len(chain)} 步）")
    started = time.monotonic()
    for cmd in chain:
        runner(cmd, log=log, env=env)
    marks[phase] = _now()
    log(f"[{category}] 阶段 {phase} 完成（{time.monotonic() - started:.1f}s）")
    return True


@contextlib.contextmanager
def single_instance(lock_path: Path):
    """flock 单实例临界区；锁被其它进程/句柄占用 → 立即 ``RuntimeError``。"""
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as ex:
            fh.seek(0)
            holder = fh.read().strip()
            raise RuntimeError(
                f"单实例锁被占用：{lock_path}（持锁 pid={holder or '未知'}）") from ex
        fh.seek(0)
        fh.truncate()
        fh.write(f"{os.getpid()}\n")
        fh.flush()
        yield lock_path
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


def open_log(category: str, date, *, log_dir: Path | None = None) -> Callable[[str], None]:
    """打开当日日志（追加）；返回按行写入的 ``log``，行前缀 ``[category]``。"""
    if isinstance(date, datetime.datetime):
        date = date.date()
    if isinstance(date, str):
        date = datetime.date.fromisoformat(date)
    if not isinstance(date, datetime.date):
        raise TypeError(f"date 需为 datetime.date 或 ISO 串：{date!r}")
    path = Path(log_dir or LOG_DIR) / f"pan_update-{date.strftime('%Y%m%d')}.log"
    path.parent.mkdir(parents=True, exist_ok=True)

    def log(line: str) -> None:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"[{category}] {line}\n")
            fh.flush()

    return log
