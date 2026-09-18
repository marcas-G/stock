"""R09-M3 分段计时（`--profile` / `FACTORLAB_PROFILE=1`）：墙钟 + 峰值 RSS。

背景：R09 评审（`governance/evidence/reviews/r09-2026-09-17-minute-perf/report.md`
§1④）指出 `factorlab run` 无逐阶段计时/剖析开关，慢在哪一步只能外部掐表。本模块
提供默认关闭的轻量剖析器，接入日频链（`_run_factor`）与分钟链
（`_run_factor_minute`）及评估装配（`evaluate_run`）：

- 段（segments）：
  - `read_data`：label 前的信号侧（日频=装载+公式+process；分钟=逐 chunk
    装载/注入/成员过滤 + 折日 + 拼装/canonical/panel）；
  - `fold`：分钟折日 `compute_minute_factor_panel`（`read_data` 的子段；
    日频链无）；
  - `label`：`_compute_labels`；
  - `evaluate`：评估 kernel + ic_decay（weekly 模式含 align_weekly）；
  - `layered_backtest`：分层回测（`--no-backtest` 时无此段）；
  - `persist`：artifact/panel/weekly 落盘（summary.json 自身重写不计入——
    递归：写入包含"写自身耗时"的摘要）。
- wall_ms 为正整数毫秒；rss_peak_mb/rss_delta_mb 为段窗口内进程 RSS 峰值/增量
  （MiB）；`version` 为 schema 版本，`clock`/`rss_unit` 为口径字段。
- 默认关闭：不创建采样线程、不写 summary.runtime、stderr 零输出（零行为变化）。
- 采样：段边界必采样 + daemon 线程按 `sample_interval` 采样（默认 20Hz，
  线程只在 profiler.start() 后运行；峰值按段的 [t0, t1] 窗口归属）。

schema（append-only，与既有 summary 兼容）：

    "runtime": {"profile": {
        "version": 1, "clock": "wall_ms", "rss_unit": "mb",
        "total_wall_ms": 123,
        "segments": {"read_data": {"wall_ms": 10, "rss_peak_mb": 300,
                                   "rss_delta_mb": 10, "calls": 3}, ...}
    }}
"""
from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager, nullcontext
from typing import Any, Callable, Iterator, Mapping

import psutil

PROFILE_VERSION = 1
#: 报告的展示/排序顺序（未列出的段追加在后）
SEGMENT_ORDER = ("read_data", "fold", "label", "evaluate",
                 "layered_backtest", "persist")
DEFAULT_SAMPLE_INTERVAL_S = 0.05
_MB = 1024 * 1024
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off", ""}


def _parse_bool(raw: str, name: str) -> bool:
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise ValueError(
        f"{name} 必须是 1/0/true/false/yes/no/on/off（收到 {raw!r}）")


def profile_enabled(settings_obj: Any = None,
                    env: Mapping[str, str] | None = None) -> bool:
    """是否启用分段计时：env `FACTORLAB_PROFILE` 优先，其次 settings.profile。

    非法 env 值 → ValueError（配置错误 fail loud，不静默取默认）。
    """
    environ = os.environ if env is None else env
    raw = environ.get("FACTORLAB_PROFILE")
    if raw is not None:
        return _parse_bool(raw, "FACTORLAB_PROFILE")
    if settings_obj is None:
        from factorlab.config import settings
        settings_obj = settings
    return bool(getattr(settings_obj, "profile", False))


def _default_rss_reader() -> int:
    return psutil.Process().memory_info().rss


class Profiler:
    """分段墙钟 + 峰值 RSS 累积器（线程安全的边界采样）。

    `wall_reader`/`rss_reader` 可注入（测试）；`sample_interval` 为后台采样线程
    周期（秒）。不调用 `start()` 时只有段边界采样（单测确定性用）。
    """

    def __init__(self, *, rss_reader: Callable[[], int] | None = None,
                 wall_reader: Callable[[], float] | None = None,
                 sample_interval: float = DEFAULT_SAMPLE_INTERVAL_S):
        self._rss_reader = rss_reader or _default_rss_reader
        self._wall_reader = wall_reader or time.perf_counter
        self.sample_interval = float(sample_interval)
        self._segments: dict[str, dict[str, int]] = {}
        self._samples: list[tuple[float, int]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._t_start = self._wall_reader()

    # ---- 采样 / 生命周期 ----

    def start(self) -> "Profiler":
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop,
                                            name="factorlab-profiler",
                                            daemon=True)
            self._thread.start()
        return self

    def _loop(self) -> None:
        while not self._stop.wait(self.sample_interval):
            self._sample_once()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)

    def _sample_once(self) -> tuple[float, int]:
        t = self._wall_reader()
        rss = int(self._rss_reader())
        with self._lock:
            self._samples.append((t, rss))
        return t, rss

    def _window_peak(self, t0: float, t1: float) -> int:
        # 边界采样由调用方（rss0/rss1）显式纳入；这里排除 t0（上一段退出样本
        # 与本段进入同刻，属上一窗口），避免旧峰值串段。
        with self._lock:
            return max((rss for t, rss in self._samples if t0 < t <= t1),
                       default=0)

    # ---- 段 ----

    @contextmanager
    def segment(self, name: str) -> Iterator[None]:
        """累积一个命名段；可重入/嵌套（同名多次调用累加，calls 计数）。"""
        t0, rss0 = self._sample_once()
        try:
            yield
        finally:
            t1, rss1 = self._sample_once()
            peak = max(rss0, rss1, self._window_peak(t0, t1))
            with self._lock:
                row = self._segments.setdefault(name, {
                    "wall_ms": 0, "rss_peak_mb": 0, "rss_delta_mb": 0,
                    "calls": 0})
                row["wall_ms"] += max(1, int(round((t1 - t0) * 1000)))
                row["rss_peak_mb"] = max(row["rss_peak_mb"], peak // _MB)
                row["rss_delta_mb"] = max(row["rss_delta_mb"],
                                          max(0, (peak - rss0) // _MB))
                row["calls"] += 1

    def report(self) -> dict:
        """schema 报告（version/口径字段 + 各段值）。"""
        with self._lock:
            names = [n for n in SEGMENT_ORDER if n in self._segments]
            names += [n for n in self._segments if n not in SEGMENT_ORDER]
            segments = {n: dict(self._segments[n]) for n in names}
        return {
            "version": PROFILE_VERSION,
            "clock": "wall_ms",
            "rss_unit": "mb",
            "total_wall_ms": max(1, int(round(
                (self._wall_reader() - self._t_start) * 1000))),
            "segments": segments,
        }


def span(profiler: Profiler | None, name: str):
    """`profiler` 为 None（默认关闭）→ nullcontext（零开销路径）。"""
    if profiler is None:
        return nullcontext()
    return profiler.segment(name)


def attach_profile(summary: dict, profiler: Profiler | None) -> dict:
    """把报告写入 `summary["runtime"]["profile"]`（append-only；None → 原样）。"""
    if profiler is None:
        return summary
    summary.setdefault("runtime", {})["profile"] = profiler.report()
    return summary


def render_profile_summary(report: dict) -> str:
    """stderr 人读摘要（总耗时 + 各段 wall_ms/rss 峰值/增量/calls）。"""
    lines = [f"[profile] total={report['total_wall_ms']}ms "
             f"(clock={report['clock']}, rss={report['rss_unit']})"]
    for name, row in report["segments"].items():
        lines.append(
            f"  {name:<16} {row['wall_ms']:>9}ms  "
            f"peak={row['rss_peak_mb']}MB (+{row['rss_delta_mb']}MB)  "
            f"calls={row['calls']}")
    return "\n".join(lines)
