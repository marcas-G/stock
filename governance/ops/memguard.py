#!/usr/bin/env python3
"""memguard —— 主机内存保护用户级守护（R30，2026-09-18）。

背景（根因证据 `sar -r -f /var/log/sysstat/sa18`：09:37/09:57 两次 MemAvailable
99.7% 耗尽、可用 ~1GB；swap 在机械盘 sda、swappiness=60；systemd-oomd/earlyoom
均 inactive；user cgroup 无法设内存上限（systemd-run --user -p MemoryMax 实测
失败））：主机的最后一道内存护栏只能落在**进程级守护**上。

职责：
- 每 2s 采样 `/proc/meminfo`（MemAvailable/SwapFree）+ 本用户进程 RSS（psutil）；
- 纯函数决策（`level_for`/`level_for_swap`/`classify`/`select_candidates`/
  `decide`，全部可单测）：阈值 warn<10GB / term<5GB / kill<2.5GB；
  **swap 压力前置触发（R30.1，机械盘 swap 是 freeze 主因）**：
  swap_free<10GB 且 avail<15GB → term 候选；swap_free<6GB 且 avail<8GB → kill；
  与原 avail 阈值取更严重者（先触发者，`source` 标注 avail/swap/avail+swap）；
  只杀本用户且 RSS>=2GB 的候选（cmd 匹配 python|pytest|vllm|run_pipeline|
  factorlab|convert|ingest|polars|jupyter）；保护 sshd/systemd/opencode/code/
  vscode-server/clickhouse/memguard 自身/bash；llama-server 默认保护，
  RSS>38GB 转候选（模型重载爆内存场景）；
- 动作：SIGTERM → 3s → SIGKILL；动作后 30s 冷却；触发日志附 top5 RSS 快照；
  `--dry-run` 只看不杀；heartbeat 带 swap 用量与触发来源；
- 取证：每 10s 追加 `~/.local/state/memguard/memlog.tsv`
  （time/avail/swap_free/load1/top5 RSS），按天轮转、保留 7 天。

安装见 `governance/ops/install_memguard.sh`（systemd user service；
linger 不可用回退 crontab）。测试：
`platform/.venv/bin/python -m pytest governance/ops/tests/test_memguard.py -q`。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import os
import re
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

import psutil

GB = 1024 ** 3
MB = 1024 ** 2

DEFAULT_INTERVAL_S = 2.0
DEFAULT_MEMLOG_INTERVAL_S = 10.0
DEFAULT_HEARTBEAT_S = 60.0
DEFAULT_TERM_GRACE_S = 3.0
MEMLOG_KEEP_DAYS = 7
DEFAULT_STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "memguard"

# 候选/保护规则（任务书明文）：候选是"重任务"关键词，保护名单优先。
CANDIDATE_RE = re.compile(
    r"python|pytest|vllm|run_pipeline|factorlab|convert|ingest|polars|jupyter")
PROTECTED_RE = re.compile(
    r"sshd|systemd|opencode|vscode-server|clickhouse|memguard|"
    r"code-server|(^|[/\s])code($|[\s:/-])|(^|[/\s])bash($|\s)")
LLAMA_MARK = "llama-server"

log = logging.getLogger("memguard")


# ---------------------------------------------------------------- 数据模型

@dataclass(frozen=True)
class Thresholds:
    """分级阈值（字节）+ 候选 RSS 下限 + llama 例外 + 冷却 + swap 前置触发。"""

    warn: int = 10 * GB
    term: int = 5 * GB
    kill: int = int(2.5 * GB)
    candidate_rss: int = 2 * GB
    llama_candidate_rss: int = 38 * GB
    cooldown_s: float = 30.0
    # swap 压力前置触发（R30.1）：机械盘 swap 开始大量换页前先动手
    swap_term_free: int = 10 * GB       # swap_free 低于此值 + avail<swap_term_avail
    swap_term_avail: int = 15 * GB      # → term 候选（早于 avail<5GB）
    swap_kill_free: int = 6 * GB        # swap_free 低于此值 + avail<swap_kill_avail
    swap_kill_avail: int = 8 * GB       # → kill 级


DEFAULT_THRESHOLDS = Thresholds()


@dataclass(frozen=True)
class MemSample:
    total: int
    available: int
    swap_total: int
    swap_free: int


@dataclass(frozen=True)
class ProcInfo:
    pid: int
    user: str
    rss: int
    cmd: str


@dataclass(frozen=True)
class Decision:
    level: str                       # ok | warn | term | kill
    action: str                      # none | terminate | cooldown
    victims: tuple[int, ...]
    reason: str
    source: str = ""                 # ok | avail | swap | avail+swap（触发来源）


@dataclass
class StepResult:
    sample: MemSample
    decision: Decision
    executed: bool
    signals_sent: list[tuple[int, int]]   # (signum, pid)


# ---------------------------------------------------------------- 纯函数

def parse_meminfo(text: str) -> MemSample:
    """解析 /proc/meminfo 文本 → MemSample（字节）。缺关键键 fail loud。"""
    vals: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            vals[key.strip()] = int(parts[0]) * 1024
    missing = [k for k in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree")
               if k not in vals]
    if missing:
        raise ValueError(f"meminfo 缺关键字段: {', '.join(missing)}")
    return MemSample(total=vals["MemTotal"], available=vals["MemAvailable"],
                     swap_total=vals["SwapTotal"], swap_free=vals["SwapFree"])


def read_meminfo(path: str | Path = "/proc/meminfo") -> MemSample:
    return parse_meminfo(Path(path).read_text(encoding="utf-8"))


def level_for(avail: int, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> str:
    """可用内存 → 级别（严格小于阈值）：ok/warn/term/kill。"""
    if avail < thresholds.kill:
        return "kill"
    if avail < thresholds.term:
        return "term"
    if avail < thresholds.warn:
        return "warn"
    return "ok"


def level_for_swap(avail: int, swap_free: int,
                   thresholds: Thresholds = DEFAULT_THRESHOLDS) -> str:
    """swap 压力前置分级（严格小于；机械盘 swap 大量换页前先兆）：
    kill: swap_free<swap_kill_free 且 avail<swap_kill_avail；
    term: swap_free<swap_term_free 且 avail<swap_term_avail；否则 ok。"""
    if (swap_free < thresholds.swap_kill_free
            and avail < thresholds.swap_kill_avail):
        return "kill"
    if (swap_free < thresholds.swap_term_free
            and avail < thresholds.swap_term_avail):
        return "term"
    return "ok"


_SEVERITY = {"ok": 0, "warn": 1, "term": 2, "kill": 3}


def classify(avail: int, swap_free: int,
             thresholds: Thresholds = DEFAULT_THRESHOLDS) -> tuple[str, str]:
    """综合 avail 阈值与 swap 前置条件 → (level, source)，取更严重者（先触发者）。
    同级且非 ok → source=avail+swap；source ∈ ok|avail|swap|avail+swap。"""
    avail_level = level_for(avail, thresholds)
    swap_level = level_for_swap(avail, swap_free, thresholds)
    if _SEVERITY[swap_level] > _SEVERITY[avail_level]:
        return swap_level, "swap"
    if _SEVERITY[avail_level] > _SEVERITY[swap_level]:
        return avail_level, "avail"
    if avail_level == "ok":
        return "ok", "ok"
    return avail_level, "avail+swap"


def is_protected(cmd: str) -> bool:
    return bool(PROTECTED_RE.search(cmd))


def is_llama(cmd: str) -> bool:
    return LLAMA_MARK in cmd


def select_candidates(procs: Iterable[ProcInfo], *, target_user: str,
                      thresholds: Thresholds = DEFAULT_THRESHOLDS
                      ) -> list[ProcInfo]:
    """候选 = 本用户 & RSS>=candidate_rss & 非保护名单 & 命中重任务关键词；
    llama-server 默认保护，仅 RSS>llama_candidate_rss 时转候选。按 RSS 降序。"""
    out: list[ProcInfo] = []
    for p in procs:
        if p.user != target_user or p.rss < thresholds.candidate_rss:
            continue
        if is_llama(p.cmd):                       # 先判 llama 例外
            if p.rss > thresholds.llama_candidate_rss:
                out.append(p)
            continue
        if is_protected(p.cmd):
            continue
        if CANDIDATE_RE.search(p.cmd):
            out.append(p)
    out.sort(key=lambda p: p.rss, reverse=True)
    return out


def decide(avail: int, procs: Sequence[ProcInfo], *, swap_free: int,
           thresholds: Thresholds = DEFAULT_THRESHOLDS,
           last_action_ts: float | None, now: float, target_user: str) -> Decision:
    """纯决策：avail/swap 级别（取更严重者，source 标注来源）+ 候选 + 冷却
    → 意图动作（不执行任何信号）。"""
    level, source = classify(avail, swap_free, thresholds)
    if level == "ok":
        return Decision(level, "none", (), "可用内存与 swap 充足", source)
    if level == "warn":
        return Decision(level, "none", (),
                        f"可用内存 < {fmt(thresholds.warn)}，仅告警", source)
    candidates = select_candidates(procs, target_user=target_user,
                                   thresholds=thresholds)
    if not candidates:
        return Decision(level, "none", (),
                        "无合格候选（用户/RSS/保护名单过滤后）", source)
    if last_action_ts is not None and (now - last_action_ts) < thresholds.cooldown_s:
        return Decision(level, "cooldown", (),
                        f"{thresholds.cooldown_s:.0f}s 冷却期内，跳过本轮", source)
    limit = thresholds.term if level == "term" else thresholds.kill
    return Decision(level, "terminate", tuple(p.pid for p in candidates),
                    f"avail<{fmt(limit)}（source={source}），"
                    f"终止 {len(candidates)} 个候选", source)


def fmt(n: int) -> str:
    for unit, scale in (("TB", 1024 ** 4), ("GB", 1024 ** 3),
                        ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= scale:
            return f"{n / scale:.1f}{unit}"
    return f"{n}B"


def format_top(procs: Sequence[ProcInfo], n: int = 5) -> str:
    top = sorted(procs, key=lambda p: p.rss, reverse=True)[:n]
    return " ".join(f"[pid={p.pid} rss={fmt(p.rss)} {p.cmd.split()[0] if p.cmd.split() else '?'}]"
                    for p in top)


def datetime_to_ts(date_str: str) -> float:
    d = _dt.date.fromisoformat(date_str)
    return time.mktime(d.timetuple())


def prune_memlog_history(state_dir: Path, *, now: float,
                         keep_days: int = MEMLOG_KEEP_DAYS) -> list[Path]:
    """删除 `memlog-YYYY-MM-DD.tsv` 中早于 (today - keep_days + 1) 的归档。"""
    today = _dt.date.fromtimestamp(now)
    cutoff = today - _dt.timedelta(days=keep_days - 1)
    removed: list[Path] = []
    for p in sorted(state_dir.glob("memlog-*.tsv")):
        m = re.fullmatch(r"memlog-(\d{4}-\d{2}-\d{2})\.tsv", p.name)
        if not m:
            continue
        try:
            d = _dt.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if d < cutoff:
            p.unlink(missing_ok=True)
            removed.append(p)
    return removed


# ---------------------------------------------------------------- 进程采样

def list_user_processes(user: str) -> list[ProcInfo]:
    out: list[ProcInfo] = []
    for p in psutil.process_iter(["pid", "username", "memory_info", "cmdline"]):
        try:
            if p.info["username"] != user:
                continue
            mi = p.info["memory_info"]
            if mi is None:
                continue
            cmd = " ".join(p.info["cmdline"] or [])
            out.append(ProcInfo(pid=p.info["pid"], user=user, rss=int(mi.rss), cmd=cmd))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return out


# ---------------------------------------------------------------- 守护

class MemGuard:
    """常驻守护：step() 一次采样→决策→动作→取证；run_forever() 循环。"""

    def __init__(self, *, target_user: str | None = None,
                 thresholds: Thresholds = DEFAULT_THRESHOLDS,
                 interval: float = DEFAULT_INTERVAL_S,
                 memlog_interval: float = DEFAULT_MEMLOG_INTERVAL_S,
                 term_grace: float = DEFAULT_TERM_GRACE_S,
                 dry_run: bool = False,
                 state_dir: str | Path = DEFAULT_STATE_DIR,
                 meminfo_reader: Callable[[], MemSample] | None = None,
                 proc_reader: Callable[[], list[ProcInfo]] | None = None,
                 signal_sender: Callable[[int, int], None] | None = None,
                 alive_reader: Callable[[int], bool] | None = None,
                 sleeper: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.time):
        self.target_user = target_user or _current_user()
        self.thresholds = thresholds
        self.interval = float(interval)
        self.memlog_interval = float(memlog_interval)
        self.term_grace = float(term_grace)
        self.dry_run = bool(dry_run)
        self.state_dir = Path(state_dir)
        self.memlog_path = self.state_dir / "memlog.tsv"
        self._read_meminfo = meminfo_reader or read_meminfo
        self._read_procs = proc_reader or (lambda: list_user_processes(self.target_user))
        self._signal = signal_sender or os.kill
        self._alive = alive_reader or psutil.pid_exists
        self._sleep = sleeper
        self._clock = clock
        self.last_action_ts: float | None = None
        self._last_memlog_ts: float | None = None
        self._last_heartbeat_ts: float | None = None
        self.heartbeat_interval = DEFAULT_HEARTBEAT_S
        self._memlog_day: _dt.date | None = None
        self.steps = 0

    # ---- 一次迭代 ----

    def step(self) -> StepResult:
        self.steps += 1
        now = self._clock()
        sample = self._read_meminfo()
        procs = self._read_procs()
        decision = decide(sample.available, procs, swap_free=sample.swap_free,
                          thresholds=self.thresholds,
                          last_action_ts=self.last_action_ts, now=now,
                          target_user=self.target_user)
        sent: list[tuple[int, int]] = []
        executed = False
        if decision.action == "terminate":
            if self.dry_run:
                log.warning("memguard 触发（dry-run，不发送信号）: level=%s source=%s "
                            "avail=%s swap_used=%s victims=%s top5: %s",
                            decision.level, decision.source, fmt(sample.available),
                            fmt(self._swap_used(sample)),
                            list(decision.victims), format_top(procs))
            else:
                sent = self._terminate(decision.victims)
                executed = True
                self.last_action_ts = now
                log.warning("memguard 触发（已执行）: level=%s source=%s avail=%s "
                            "swap_used=%s victims=%s signals=%s top5: %s",
                            decision.level, decision.source, fmt(sample.available),
                            fmt(self._swap_used(sample)),
                            list(decision.victims), sent, format_top(procs))
        elif decision.action == "cooldown":
            log.warning("memguard 触发但冷却中: level=%s source=%s avail=%s "
                        "swap_used=%s top5: %s",
                        decision.level, decision.source, fmt(sample.available),
                        fmt(self._swap_used(sample)), format_top(procs))
        elif decision.action == "none" and decision.level in ("term", "kill"):
            log.warning("memguard 内存紧张但无候选: level=%s source=%s avail=%s "
                        "swap_used=%s top5: %s",
                        decision.level, decision.source, fmt(sample.available),
                        fmt(self._swap_used(sample)), format_top(procs))
        elif decision.level == "warn":
            log.warning("memguard warn: source=%s avail=%s (<%s) swap_used=%s top5: %s",
                        decision.source, fmt(sample.available),
                        fmt(self.thresholds.warn), fmt(self._swap_used(sample)),
                        format_top(procs))
        self._maybe_heartbeat(now, sample, decision)
        self._maybe_write_memlog(now, sample, procs)
        return StepResult(sample=sample, decision=decision, executed=executed,
                          signals_sent=sent)

    @staticmethod
    def _swap_used(sample: MemSample) -> int:
        return max(0, sample.swap_total - sample.swap_free)

    def _maybe_heartbeat(self, now: float, sample: MemSample,
                         decision: Decision) -> None:
        """周期心跳日志（默认 60s；服务验收的"日志有周期采样"证据）；
        R30.1：带 swap 用量（used=total-free）与触发来源标注。"""
        if (self._last_heartbeat_ts is not None
                and now - self._last_heartbeat_ts < self.heartbeat_interval):
            return
        log.info("memguard heartbeat: level=%s source=%s avail=%s swap_used=%s "
                 "swap_free=%s steps=%d",
                 decision.level, decision.source, fmt(sample.available),
                 fmt(self._swap_used(sample)), fmt(sample.swap_free), self.steps)
        self._last_heartbeat_ts = now

    def _terminate(self, victims: Sequence[int]) -> list[tuple[int, int]]:
        sent: list[tuple[int, int]] = []
        for pid in victims:
            try:
                self._signal(pid, signal.SIGTERM)
                sent.append((signal.SIGTERM, pid))
            except (ProcessLookupError, PermissionError) as exc:
                log.error("SIGTERM pid=%s 失败: %s", pid, exc)
        self._sleep(self.term_grace)
        for pid in victims:
            if self._alive(pid):
                try:
                    self._signal(pid, signal.SIGKILL)
                    sent.append((signal.SIGKILL, pid))
                except (ProcessLookupError, PermissionError) as exc:
                    log.error("SIGKILL pid=%s 失败: %s", pid, exc)
        return sent

    # ---- memlog 取证 ----

    def _maybe_write_memlog(self, now: float, sample: MemSample,
                            procs: Sequence[ProcInfo]) -> None:
        if (self._last_memlog_ts is not None
                and now - self._last_memlog_ts < self.memlog_interval):
            return
        day = _dt.date.fromtimestamp(now)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if self._memlog_day is not None and day != self._memlog_day:
            archive = self.state_dir / f"memlog-{self._memlog_day.isoformat()}.tsv"
            try:
                self.memlog_path.rename(archive)
            except OSError:
                pass
            prune_memlog_history(self.state_dir, now=now)
        self._memlog_day = day
        new = not self.memlog_path.exists()
        load1 = os.getloadavg()[0] if hasattr(os, "getloadavg") else 0.0
        top = " ".join(f"{p.pid}:{fmt(p.rss)}"
                       for p in sorted(procs, key=lambda p: p.rss, reverse=True)[:5])
        with self.memlog_path.open("a", encoding="utf-8") as fh:
            if new:
                fh.write("time\tavail\tswap_free\tload1\ttop5\n")
            fh.write(f"{int(now)}\t{sample.available}\t{sample.swap_free}\t"
                     f"{load1:.2f}\t{top}\n")
        self._last_memlog_ts = now

    # ---- 循环 ----

    def run_forever(self) -> None:
        prune_memlog_history(self.state_dir, now=self._clock())
        log.info("memguard 启动: user=%s interval=%.1fs dry_run=%s state=%s "
                 "thresholds(warn=%s term=%s kill=%s candidate>=%s "
                 "swap_term=<%s free & <%s avail, swap_kill=<%s free & <%s avail)",
                 self.target_user, self.interval, self.dry_run, self.state_dir,
                 fmt(self.thresholds.warn), fmt(self.thresholds.term),
                 fmt(self.thresholds.kill), fmt(self.thresholds.candidate_rss),
                 fmt(self.thresholds.swap_term_free),
                 fmt(self.thresholds.swap_term_avail),
                 fmt(self.thresholds.swap_kill_free),
                 fmt(self.thresholds.swap_kill_avail))
        while True:
            try:
                self.step()
            except Exception:  # 守护不能因单次采样异常而死
                log.exception("memguard step 异常（继续运行）")
            self._sleep(self.interval)


def _current_user() -> str:
    try:
        import getpass
        return getpass.getuser()
    except Exception:
        return str(os.getuid())


# ---------------------------------------------------------------- CLI

def _parse_size(s: str) -> int:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]*)\s*", s or "")
    units = {"": 1, "b": 1, "k": 1024, "kb": 1024, "kib": 1024,
             "m": MB, "mb": MB, "mib": MB, "g": GB, "gb": GB, "gib": GB}
    if not m or m.group(2).lower() not in units:
        raise argparse.ArgumentTypeError(f"无法解析内存规格: {s!r}")
    return int(float(m.group(1)) * units[m.group(2).lower()])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="memguard",
        description="主机内存保护守护（只杀本用户重任务；dry-run 只看不杀）")
    p.add_argument("--once", action="store_true", help="采样/决策一次后退出（取证）")
    p.add_argument("--dry-run", action="store_true", help="只记录不发送信号")
    p.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S,
                   help=f"采样周期秒（默认 {DEFAULT_INTERVAL_S:g}）")
    p.add_argument("--memlog-interval", type=float, default=DEFAULT_MEMLOG_INTERVAL_S,
                   help=f"memlog 写入周期秒（默认 {DEFAULT_MEMLOG_INTERVAL_S:g}）")
    p.add_argument("--term-grace", type=float, default=DEFAULT_TERM_GRACE_S,
                   help="SIGTERM 后等待秒数再 SIGKILL（默认 3）")
    p.add_argument("--warn", type=_parse_size, default=DEFAULT_THRESHOLDS.warn)
    p.add_argument("--term", type=_parse_size, default=DEFAULT_THRESHOLDS.term)
    p.add_argument("--kill", type=_parse_size, default=DEFAULT_THRESHOLDS.kill)
    p.add_argument("--candidate-rss", type=_parse_size,
                   default=DEFAULT_THRESHOLDS.candidate_rss)
    p.add_argument("--llama-rss", type=_parse_size,
                   default=DEFAULT_THRESHOLDS.llama_candidate_rss)
    p.add_argument("--target-user", default=None, help="只处理该用户进程（默认当前用户）")
    p.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    p.add_argument("--meminfo-file", default="/proc/meminfo", help=argparse.SUPPRESS)
    return p


def _setup_logging(state_dir: Path, *, live: bool) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if live:
        from logging.handlers import TimedRotatingFileHandler
        state_dir.mkdir(parents=True, exist_ok=True)
        fh = TimedRotatingFileHandler(state_dir / "memguard.log", when="D",
                                      backupCount=7, encoding="utf-8")
        handlers.append(fh)
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    state_dir = Path(args.state_dir)
    thresholds = Thresholds(warn=args.warn, term=args.term, kill=args.kill,
                            candidate_rss=args.candidate_rss,
                            llama_candidate_rss=args.llama_rss)
    guard = MemGuard(target_user=args.target_user, thresholds=thresholds,
                     interval=args.interval, memlog_interval=args.memlog_interval,
                     term_grace=args.term_grace, dry_run=args.dry_run,
                     state_dir=state_dir,
                     meminfo_reader=lambda: read_meminfo(args.meminfo_file))
    _setup_logging(state_dir, live=not args.once)
    if args.once:
        res = guard.step()
        print(json.dumps({
            "time": int(guard._clock()),
            "available": res.sample.available,
            "swap_free": res.sample.swap_free,
            "level": res.decision.level,
            "source": res.decision.source,
            "action": res.decision.action,
            "victims": list(res.decision.victims),
            "executed": res.executed,
            "dry_run": guard.dry_run,
        }, ensure_ascii=False))
        return 0
    guard.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
