"""R05-C1（P0 事故）进程内存护栏：`factorlab run` 的 RSS 看门狗 + RLIMIT_AS 硬上限。

背景：2023–2025 全市场分钟链运行期间主机内存耗尽、SSH 卡死、ClickHouse 一度
无响应，进程 D 状态零输出——平台 run 链此前无进程级内存上限/看门狗（事故记录
见 `governance/evidence/reviews/r05-usage-2026-09-16/report.md`）。本模块提供：

- `MemoryWatchdog`：psutil ~5s 采样（daemon 线程）+ 主线程协作检查
  （`run_factor` / `run_factor_minute` 在 chunk 边界与落盘前调用 `check()`）——
  RSS 超 `FACTORLAB_MAX_MEMORY` 或系统可用内存低于
  `FACTORLAB_MIN_AVAILABLE_MEMORY` → `MemoryLimitExceeded(ValueError)` 干净
  中止（落盘前中止 = 无半成品；写盘中断走 R02-I8 summary tombstone 协议）。
- `apply_address_space_limit`：显式设置 RSS 上限时在 CLI 入口叠加 POSIX
  `resource.setrlimit(RLIMIT_AS)` 硬护栏（软看门狗线程被饿死/D 状态时的兜底；
  触发 MemoryError 也不拖垮主机）。非 POSIX/失败 → warning 降级。
- `guard_minute_chunk_days`：分钟链显式巨大 chunk / 未分块长窗的静态估算门
  （校准自 R04-P1 实测；默认自动 20 日/块路径只告警不拒绝）。

**默认行为**：`FACTORLAB_MAX_MEMORY` 与 `FACTORLAB_MIN_AVAILABLE_MEMORY` 都
未设 → 整个护栏不启用（零线程、零采样、行为与现状一致——避免误杀 CI/小 run）。
推荐生产值（16GB 机 + LLM 并发）：`FACTORLAB_MAX_MEMORY=8GB`、
`FACTORLAB_MIN_AVAILABLE_MEMORY=2GB`；语义见 `knowledge/contracts/interface.md` §1 内存护栏。
"""
from __future__ import annotations

import os
import re
import threading
import warnings
from dataclasses import dataclass
from typing import Callable

import psutil

try:  # pragma: no cover - Windows/非 POSIX 降级路径由测试 monkeypatch 覆盖
    import resource
except ImportError:
    resource = None  # type: ignore[assignment]

from factorlab.config import settings

DEFAULT_SAMPLE_INTERVAL_S = 5.0

_UNIT_BYTES = {
    "": 1, "b": 1,
    "k": 1024, "kb": 1024, "ki": 1024, "kib": 1024,
    "m": 1024 ** 2, "mb": 1024 ** 2, "mi": 1024 ** 2, "mib": 1024 ** 2,
    "g": 1024 ** 3, "gb": 1024 ** 3, "gi": 1024 ** 3, "gib": 1024 ** 3,
    "t": 1024 ** 4, "tb": 1024 ** 4, "ti": 1024 ** 4, "tib": 1024 ** 4,
}
_MEMORY_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([a-z]*)$")


class MemoryLimitExceeded(ValueError):
    """进程内存超限（R05-C1）：看门狗/协作检查触发——干净中止。

    ValueError 子类：CLI 既有 `(ValueError, FileNotFoundError, FactorDSLError)`
    处理路径直接给出干净错误 + exit 1（不裸 traceback、不产生半成品）。
    """


def parse_memory(value: str | int | None) -> int | None:
    """解析内存规格 → 字节数；None/空白串 = 未设置（返回 None）。

    支持 "8GB"/"512MB"/"1kb"/"1.5GiB"/纯字节数（int 或数字串）；KB/MB/GB/TB
    按 1024 进制（与 prlimit/safe_run.sh 口径一致）。非法值 → ValueError
    （配置错误 fail loud，不静默取默认）。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"内存规格非法: {value!r}——支持 '8GB'/'512MB'/纯字节数；"
                         f"不启用请留空")
    if isinstance(value, int):
        if value <= 0:
            raise ValueError(f"内存规格必须为正数（收到 {value!r}）；不启用请留空")
        return value
    s = str(value).strip().lower()
    if not s:
        return None
    m = _MEMORY_RE.fullmatch(s)
    if not m or m.group(2) not in _UNIT_BYTES:
        raise ValueError(f"无法解析内存规格 {value!r}——支持 '8GB'/'512MB'/'1kb'/"
                         f"纯字节数（bytes）；不启用请留空")
    n = int(float(m.group(1)) * _UNIT_BYTES[m.group(2)])
    if n <= 0:
        raise ValueError(f"内存规格必须为正数（收到 {value!r}）；不启用请留空")
    return n


def format_bytes(n: int) -> str:
    """人类可读字节（1024 进制，1 位小数）：8GB → "8.0GB"。"""
    for unit, scale in (("TB", 1024 ** 4), ("GB", 1024 ** 3), ("MB", 1024 ** 2),
                        ("KB", 1024), ("B", 1)):
        if n >= scale:
            return f"{n / scale:.1f}{unit}"
    return "0B"


@dataclass(frozen=True)
class MemorySample:
    """单次采样：进程 RSS + 系统可用内存（bytes）。"""

    rss: int
    available: int


class MemoryWatchdog:
    """进程内存看门狗：~5s 采样线程 + 主线程协作检查。

    - 采样线程只**记录**违例（线程内不能把异常抛进主线程）；
    - `check()` 在主线程（chunk 边界/落盘前）先看已记录违例，否则现场采样；
      超限 → `MemoryLimitExceeded`；
    - 读者可注入（测试用序列读数；默认 psutil 当前进程/虚拟内存）。
    """

    def __init__(self, *, max_rss: int | None = None,
                 min_available: int | None = None,
                 sample_interval: float = DEFAULT_SAMPLE_INTERVAL_S,
                 rss_reader: Callable[[], int] | None = None,
                 available_reader: Callable[[], int] | None = None):
        if max_rss is None and min_available is None:
            raise ValueError("MemoryWatchdog 至少需要一个阈值（max_rss/min_available）")
        self.max_rss = max_rss
        self.min_available = min_available
        self.sample_interval = float(sample_interval)
        self._rss_reader = rss_reader or _default_rss_reader
        self._available_reader = available_reader or _default_available_reader
        self.samples = 0
        self.peak_rss = 0
        self.last_sample: MemorySample | None = None
        self._violation: MemoryLimitExceeded | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- 采样 / 判定 ----

    def sample(self) -> MemorySample:
        s = MemorySample(rss=int(self._rss_reader()),
                         available=int(self._available_reader()))
        self.samples += 1
        self.peak_rss = max(self.peak_rss, s.rss)
        self.last_sample = s
        if self._violation is None:
            self._violation = self._evaluate(s)
        return s

    def _evaluate(self, s: MemorySample) -> MemoryLimitExceeded | None:
        hits: list[str] = []
        if self.max_rss is not None and s.rss > self.max_rss:
            hits.append(f"RSS {format_bytes(s.rss)} > "
                        f"FACTORLAB_MAX_MEMORY={format_bytes(self.max_rss)}")
        if self.min_available is not None and s.available < self.min_available:
            hits.append(f"系统可用内存 {format_bytes(s.available)} < "
                        f"FACTORLAB_MIN_AVAILABLE_MEMORY={format_bytes(self.min_available)}")
        if not hits:
            return None
        return MemoryLimitExceeded(
            "进程内存超限（R05-C1 内存看门狗，已干净中止、未落半成品产物）："
            + "；".join(hits)
            + f"（当前 RSS={format_bytes(s.rss)}，系统可用={format_bytes(s.available)}）。"
            "建议：减小 --chunk-days（分钟链默认 20 交易日/块，见 "
            "knowledge/contracts/interface.md §1）、调大/设置 FACTORLAB_MAX_MEMORY、"
            "避免与 LLM 服务/多 agent 并发重任务"
            "（事故记录 governance/evidence/reviews/r05-usage-2026-09-16）。")

    @property
    def violation(self) -> MemoryLimitExceeded | None:
        return self._violation

    def check(self) -> None:
        """主线程协作检查（chunk 边界/落盘前）：超限 → MemoryLimitExceeded。

        已记录违例直接抛（不重复采样）；否则现场采样一次再判定。"""
        if self._violation is not None:
            raise self._violation
        self.sample()
        if self._violation is not None:
            raise self._violation

    # ---- 采样线程生命周期 ----

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> "MemoryWatchdog":
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._loop, name="factorlab-memory-watchdog", daemon=True)
            self._thread.start()
        return self

    def _loop(self) -> None:
        while not self._stop.wait(self.sample_interval):
            self.sample()
            if self._violation is not None:
                return

    def stop(self) -> None:
        self._stop.set()
        t, self._thread = self._thread, None
        if t is not None:
            t.join(timeout=2.0)


def _default_rss_reader() -> int:
    return psutil.Process().memory_info().rss


def _default_available_reader() -> int:
    return psutil.virtual_memory().available


def memory_watchdog_from_settings(settings_obj=None) -> MemoryWatchdog | None:
    """settings（env `FACTORLAB_MAX_MEMORY`/`FACTORLAB_MIN_AVAILABLE_MEMORY`）
    → 看门狗；二者都未设 → None（默认不启用，零行为变化）。非法值 fail loud。"""
    s = settings_obj if settings_obj is not None else settings
    max_rss = parse_memory(getattr(s, "max_memory", None))
    min_avail = parse_memory(getattr(s, "min_available_memory", None))
    if max_rss is None and min_avail is None:
        return None
    return MemoryWatchdog(max_rss=max_rss, min_available=min_avail)


# ---- RLIMIT_AS 硬护栏（POSIX） ----

# 为什么硬限远高于 RSS 阈值：polars/glibc/allocator 的**虚拟地址空间**预留远
# 大于 RSS（本机实测：小 run VmSize 峰值 13.6GB vs VmRSS 0.4GB——glibc arena
# 每线程约 64MB、arrow/expr_codegen 另有预留）。RLIMIT_AS 贴 RSS 阈值设会
# 让正常 run 误报 MemoryError（见 governance/evidence/verification/R23/safety 测量）。软看门狗
# 才是 RSS 主护栏；AS 硬限是采样线程被饿死/D 状态时的兜底。
_AS_HEADROOM_BASE = 12 * 1024 ** 3   # 实测 VA 预留 ~13.6GB + 余量（经验校准）
_AS_ARENA_PER_CPU = 64 * 1024 ** 2   # glibc arena 每线程预留上限量级


def apply_address_space_limit(rss_limit_bytes: int, *,
                              current_vm: int | None = None,
                              cpu_count: int | None = None) -> int | None:
    """POSIX 硬护栏：`RLIMIT_AS = max(3×RSS 上限, 当前 VA + RSS 上限 + 预留)`。

    成功 → 返回实际设置值；非 POSIX/设置失败 → warning + None（降级，软看门狗
    仍生效，不阻断 run）。进程级一次性设置（由 CLI 入口调用；库内嵌调用方
    只享受软护栏，避免替宿主进程设限）。
    """
    if resource is None or not hasattr(resource, "RLIMIT_AS"):
        warnings.warn(
            "当前平台不支持 resource.RLIMIT_AS（非 POSIX）——内存硬上限降级；"
            "psutil 软看门狗（FACTORLAB_MAX_MEMORY）仍生效",
            RuntimeWarning, stacklevel=2)
        return None
    if current_vm is None:
        current_vm = psutil.Process().memory_info().vms
    n_cpu = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
    limit = max(3 * rss_limit_bytes,
                current_vm + rss_limit_bytes + _AS_HEADROOM_BASE
                + _AS_ARENA_PER_CPU * n_cpu)
    try:
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ValueError, OSError) as exc:
        warnings.warn(f"RLIMIT_AS 设置失败（{exc}）——内存硬上限降级；"
                      f"psutil 软看门狗仍生效", RuntimeWarning, stacklevel=2)
        return None
    return limit


def apply_hard_memory_limit_from_settings(settings_obj=None) -> int | None:
    """显式 `FACTORLAB_MAX_MEMORY` 时落 RLIMIT_AS 硬上限（CLI 入口调用）。

    未设置 → None（不动进程资源）；非法值 → ValueError fail loud。"""
    s = settings_obj if settings_obj is not None else settings
    limit = parse_memory(getattr(s, "max_memory", None))
    if limit is None:
        return None
    return apply_address_space_limit(limit)


# ---- 分钟链长窗估算门（R05-C1） ----

# 校准（R04-P1 实测，commit 6985bc5）：非分块 5207 code × 117 交易日峰值 RSS
# 34.95GB（16GB 机 OOM）；20 日/块 6.95GB 且 wall 不增 → ≈60KB/(code·交易日)，
# 取 64KB 保守圆整。
MINUTE_BYTES_PER_CODE_DAY = 64 * 1024
MINUTE_PEAK_WARN_BYTES = 8 * 1024 ** 3      # 与推荐 FACTORLAB_MAX_MEMORY（16GB 机）同量级
MINUTE_PEAK_REJECT_BYTES = 32 * 1024 ** 3   # 16GB 机的 2 倍——必然拖垮主机（事故域）


class MinuteChunkSizeWarning(UserWarning):
    """R05-C1：分钟链显式巨大 chunk / 自动分块估算峰值偏高的响亮告警。"""


def guard_minute_chunk_days(n_codes: int, n_days: int, chunk_days: int, *,
                            explicit: bool) -> None:
    """分钟链块峰值静态估算门：`n_codes × min(chunk_days, n_days) × 64KB`。

    - `explicit=True`（用户显式 `--chunk-days`）且估算 > REJECT（32GB）→
      ValueError fail fast（不启动分钟批读；文案给推荐块长）；
    - 估算 > WARN（8GB）→ `MinuteChunkSizeWarning`（显式/自动都告警）；
    - 默认自动路径（`explicit=False`）**绝不拒绝**——20 日/块是平台实测安全点，
      残余风险由运行时看门狗（FACTORLAB_MAX_MEMORY）接管。

    用自适应估算而非固定天数阈值：小宇宙（测试/单票）合法长窗不误拒。
    """
    if n_codes <= 0 or n_days <= 0 or chunk_days <= 0:
        return
    effective_days = min(chunk_days, n_days)
    est = n_codes * effective_days * MINUTE_BYTES_PER_CODE_DAY
    if est <= MINUTE_PEAK_WARN_BYTES:
        return
    recommended = max(1, min(n_days, MINUTE_PEAK_WARN_BYTES
                             // (n_codes * MINUTE_BYTES_PER_CODE_DAY)))
    detail = (f"分钟链块峰值估算 ≈ {format_bytes(est)}（{n_codes} code × "
              f"{effective_days} 交易日 × {MINUTE_BYTES_PER_CODE_DAY // 1024}KB/"
              f"code·日，R04-P1 实测校准）")
    if explicit and est > MINUTE_PEAK_REJECT_BYTES:
        raise ValueError(
            f"显式 --chunk-days {chunk_days} 被拒绝：{detail}，超过拒绝阈值 "
            f"{format_bytes(MINUTE_PEAK_REJECT_BYTES)}——会耗尽主机内存"
            f"（R05-C1 事故：3 年全市场分钟链）。请用 --chunk-days {recommended}"
            f" 或更小（默认 20）；或先设置 FACTORLAB_MAX_MEMORY 护栏并小步验证。")
    warnings.warn(
        f"{detail}，超过建议上限 {format_bytes(MINUTE_PEAK_WARN_BYTES)}——建议 "
        f"--chunk-days {recommended}（分钟链默认 20）；运行期内存看门狗"
        f"（FACTORLAB_MAX_MEMORY）兜底。详见 knowledge/contracts/interface.md §1。",
        MinuteChunkSizeWarning, stacklevel=2)
