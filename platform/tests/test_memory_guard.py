"""R05-C1 内存护栏（P0 事故）单测：规格解析 / 采样 / 触发条件 / 协作检查 /
daemon 线程看门狗 / RLIMIT_AS 硬上限 / settings 工厂 / 分钟长窗估算门。

断言来源：`governance/evidence/reviews/r05-usage-2026-09-16/report.md`「事故记录」平台修复
建议 1/2/4——`FACTORLAB_MAX_MEMORY`（进程 RSS 上限）、可选
`FACTORLAB_MIN_AVAILABLE_MEMORY`（系统可用内存下限）、~5s 采样 daemon 线程 +
chunk 边界协作检查、RLIMIT_AS 硬护栏、超限干净中止（无半成品）——以及
interface.md §1 `factorlab run` 内存护栏节。不依赖真实 DB。
"""
import time

import pytest

from factorlab.app.memory import (MINUTE_BYTES_PER_CODE_DAY, MinuteChunkSizeWarning,
                                  MemoryLimitExceeded, MemoryWatchdog, apply_address_space_limit,
                                  apply_hard_memory_limit_from_settings, cli_default_max_memory,
                                  cli_memory_guardrails, format_bytes,
                                  guard_minute_chunk_days, memory_watchdog_from_settings,
                                  parse_memory, resolve_cli_guardrails)
from factorlab.config import settings

GB = 1024 ** 3
MB = 1024 ** 2


class _Readings:
    """可控读数序列：每次调用返回下一个；末值重复（采样时序测试用）。"""

    def __init__(self, values):
        self.values = list(values)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.values[min(self.calls - 1, len(self.values) - 1)]


def _watchdog(**kw):
    kw.setdefault("rss_reader", lambda: 10 * MB)
    kw.setdefault("available_reader", lambda: 100 * GB)
    return MemoryWatchdog(**kw)


# ---------------- parse_memory / format_bytes ----------------

@pytest.mark.parametrize("spec,expected", [
    ("8GB", 8 * GB), ("512MB", 512 * MB), ("1kb", 1024), ("2GiB", 2 * GB),
    ("1024", 1024), ("1.5GB", int(1.5 * GB)), ("  4 gb ", 4 * GB),
    (1073741824, 1073741824),
])
def test_parse_memory_units(spec, expected):
    assert parse_memory(spec) == expected


def test_parse_memory_none_or_empty_means_disabled():
    assert parse_memory(None) is None
    assert parse_memory("") is None
    assert parse_memory("   ") is None


@pytest.mark.parametrize("bad", ["abc", "8XB", "-1", "0", "-1GB", "1..5GB", True])
def test_parse_memory_invalid_fails_loud(bad):
    with pytest.raises(ValueError, match="内存"):
        parse_memory(bad)


@pytest.mark.parametrize("n,expected", [
    (8 * GB, "8.0GB"), (512 * MB, "512.0MB"), (1024, "1.0KB"), (0, "0B"),
])
def test_format_bytes(n, expected):
    assert format_bytes(n) == expected


# ---------------- settings 工厂（默认不启用 / 显式启用 / 非法 fail loud） ----------------

def test_watchdog_from_settings_disabled_by_default(monkeypatch):
    monkeypatch.setattr(settings, "max_memory", None)
    monkeypatch.setattr(settings, "min_available_memory", None)
    assert memory_watchdog_from_settings() is None


def test_watchdog_from_settings_parses_both_thresholds(monkeypatch):
    monkeypatch.setattr(settings, "max_memory", "1GB")
    monkeypatch.setattr(settings, "min_available_memory", "256MB")
    wd = memory_watchdog_from_settings()
    assert wd is not None
    assert wd.max_rss == GB and wd.min_available == 256 * MB


def test_watchdog_from_settings_invalid_fails_loud(monkeypatch):
    monkeypatch.setattr(settings, "max_memory", "8XB")
    with pytest.raises(ValueError, match="内存"):
        memory_watchdog_from_settings()


# ---------------- 触发条件（RSS / 系统可用下限）与协作检查 ----------------

def test_check_raises_when_rss_over_limit_with_guidance():
    wd = _watchdog(max_rss=100 * MB, rss_reader=lambda: 200 * MB)
    with pytest.raises(MemoryLimitExceeded) as ei:
        wd.check()
    msg = str(ei.value)
    assert "FACTORLAB_MAX_MEMORY" in msg and "RSS" in msg
    assert "200.0MB" in msg and "100.0MB" in msg      # 当前值 + 阈值进文案
    assert "--chunk-days" in msg and "并发" in msg     # 建议：减块 / 避免与 LLM 并发


def test_check_raises_when_available_below_floor():
    wd = _watchdog(min_available=10 * GB,
                   rss_reader=lambda: 50 * MB, available_reader=lambda: GB)
    with pytest.raises(MemoryLimitExceeded) as ei:
        wd.check()
    assert "FACTORLAB_MIN_AVAILABLE_MEMORY" in str(ei.value)


def test_check_passes_below_thresholds_and_tracks_peak():
    reads = _Readings([50 * MB, 300 * MB, 100 * MB])
    wd = MemoryWatchdog(max_rss=GB, sample_interval=999, rss_reader=reads,
                        available_reader=lambda: 100 * GB)
    wd.check()
    wd.check()
    wd.check()
    assert wd.samples == 3
    assert wd.peak_rss == 300 * MB          # 峰值真实记录（不是硬编码）
    assert wd.violation is None


def test_violation_is_valueerror_for_cli_clean_abort():
    # CLI 只捕获 (ValueError, FileNotFoundError, FactorDSLError) → 干净 exit 1
    assert issubclass(MemoryLimitExceeded, ValueError)


def test_check_prefers_recorded_violation_without_resampling():
    reads = _Readings([500 * MB])
    wd = MemoryWatchdog(max_rss=100 * MB, sample_interval=999, rss_reader=reads,
                        available_reader=lambda: 100 * GB)
    with pytest.raises(MemoryLimitExceeded):
        wd.check()
    n_after_first = reads.calls
    with pytest.raises(MemoryLimitExceeded):
        wd.check()                          # 已记录违例：直接抛，不再采样
    assert reads.calls == n_after_first


# ---------------- 线程看门狗（~5s 采样，超限记录违例） ----------------

def test_watchdog_thread_samples_and_records_violation():
    wd = MemoryWatchdog(max_rss=100 * MB, sample_interval=0.01,
                        rss_reader=lambda: 500 * MB,
                        available_reader=lambda: 100 * GB)
    wd.start()
    assert wd.running is True
    deadline = time.monotonic() + 5
    while wd.violation is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert wd.violation is not None         # 线程独立采到超限
    assert wd.samples >= 1
    wd.stop()
    assert wd.running is False
    with pytest.raises(MemoryLimitExceeded):
        wd.check()                          # 协作检查复用线程记录的违例


# ---------------- RLIMIT_AS 硬护栏（POSIX；不支持时降级） ----------------

def test_apply_address_space_limit_sets_symmetric_rlimit(monkeypatch):
    import factorlab.app.memory as mem
    calls = []
    monkeypatch.setattr(mem.resource, "setrlimit",
                        lambda which, limits: calls.append((which, limits)))
    limit = apply_address_space_limit(8 * GB, current_vm=GB, cpu_count=40)
    assert calls == [(mem.resource.RLIMIT_AS, (limit, limit))]
    assert limit >= 3 * 8 * GB              # 硬上限不贴 RSS 阈值
    assert limit >= GB + 8 * GB             # 覆盖当前 VA + RSS 上限


def test_apply_address_space_limit_headroom_covers_polars_va(monkeypatch):
    # 实测：小 run VmSize 13.6GB vs VmRSS 0.4GB（polars/glibc arena 虚拟预留）——
    # 硬上限必须含 VA 预留头，否则正常 run 误报 MemoryError
    import factorlab.app.memory as mem
    monkeypatch.setattr(mem.resource, "setrlimit", lambda which, limits: None)
    limit = apply_address_space_limit(GB, current_vm=13 * GB, cpu_count=40)
    assert limit >= 13 * GB + GB


def test_apply_address_space_limit_degrades_when_unsupported(monkeypatch, recwarn):
    import factorlab.app.memory as mem
    monkeypatch.setattr(mem, "resource", None)
    assert apply_address_space_limit(8 * GB) is None
    assert any("RLIMIT_AS" in str(w.message) for w in recwarn)


def test_apply_hard_memory_limit_from_settings_skips_when_unset(monkeypatch):
    monkeypatch.setattr(settings, "max_memory", None)
    assert apply_hard_memory_limit_from_settings() is None


def test_apply_hard_memory_limit_from_settings_applies_explicit_value(monkeypatch):
    import factorlab.app.memory as mem
    monkeypatch.setattr(settings, "max_memory", "2GB")
    seen = []
    monkeypatch.setattr(mem, "apply_address_space_limit",
                        lambda b, **kw: seen.append(b) or b)
    assert apply_hard_memory_limit_from_settings() == 2 * GB
    assert seen == [2 * GB]


# ---------------- 分钟长窗估算门（显式巨大 chunk / 未分块拒绝） ----------------

def test_guard_rejects_huge_full_market_explicit_chunk():
    # R05 事故窗：5207 code × 729 日 × 64KB/(code·日) ≈ 243GB >> 32GB
    with pytest.raises(ValueError, match="chunk-days.*20"):
        guard_minute_chunk_days(5207, 729, 10_000, explicit=True)


def test_guard_warns_large_but_allows_explicit_chunk():
    with pytest.warns(MinuteChunkSizeWarning, match="估算"):
        guard_minute_chunk_days(5207, 60, 60, explicit=True)


def test_guard_auto_chunk_never_rejects(monkeypatch):
    import factorlab.app.memory as mem
    monkeypatch.setattr(mem, "MINUTE_PEAK_WARN_BYTES", 1)
    monkeypatch.setattr(mem, "MINUTE_PEAK_REJECT_BYTES", 1)
    with pytest.warns(MinuteChunkSizeWarning):
        guard_minute_chunk_days(5207, 729, 20, explicit=False)


def test_guard_small_universe_allows_whole_window():
    # 自适应估算（非固定天数阈值）：2 code 整段合法，不误拒
    guard_minute_chunk_days(2, 46, 10_000, explicit=True)


def test_guard_uses_calibrated_per_code_day_constant():
    # 常数必须是 R04-P1 实测校准量级（~64KB/(code·日)：5207×117×64KB≈39GB）
    assert 32 * 1024 <= MINUTE_BYTES_PER_CODE_DAY <= 128 * 1024


# ---------------- R30：CLI `factorlab run` 护栏默认化（env 未设 → 安全默认） ----------------
# 规格（R30 任务书 3）：CLI run 在 env 未设时默认
# FACTORLAB_MIN_AVAILABLE_MEMORY=6GB + FACTORLAB_MAX_MEMORY=min(16GB, 12% 物理内存)；
# 显式 off/none 关闭；API 直调语义不变（run_factor 不经过默认化）。

def test_cli_default_max_memory_formula():
    assert cli_default_max_memory(100 * GB) == 12 * GB            # 12% < 16GB
    assert cli_default_max_memory(200 * GB) == 16 * GB            # 12% > 16GB → 封顶
    assert cli_default_max_memory(16 * GB) == int(16 * GB * 0.12)  # 小主机按比例


def test_cli_resolve_defaults_when_env_unset(monkeypatch):
    monkeypatch.setattr(settings, "max_memory", None)
    monkeypatch.setattr(settings, "min_available_memory", None)
    max_rss, min_avail = resolve_cli_guardrails(total_memory=100 * GB)
    assert max_rss == 12 * GB                # min(16GB, 12%)
    assert min_avail == 6 * GB               # 安全预检


def test_cli_resolve_explicit_values_win(monkeypatch):
    monkeypatch.setattr(settings, "max_memory", "8GB")
    monkeypatch.setattr(settings, "min_available_memory", "2GB")
    assert resolve_cli_guardrails(total_memory=100 * GB) == (8 * GB, 2 * GB)


def test_cli_resolve_off_none_disables(monkeypatch):
    monkeypatch.setattr(settings, "max_memory", "off")
    monkeypatch.setattr(settings, "min_available_memory", "none")
    assert resolve_cli_guardrails(total_memory=100 * GB) == (None, None)
    # 单边关闭：另一边仍取默认（独立语义）
    monkeypatch.setattr(settings, "min_available_memory", None)
    assert resolve_cli_guardrails(total_memory=100 * GB) == (None, 6 * GB)


def test_cli_resolve_invalid_fails_loud(monkeypatch):
    monkeypatch.setattr(settings, "max_memory", "8XB")
    with pytest.raises(ValueError, match="内存"):
        resolve_cli_guardrails(total_memory=100 * GB)


def test_cli_guardrails_context_enables_watchdog_then_restores(monkeypatch):
    monkeypatch.setattr(settings, "max_memory", None)
    monkeypatch.setattr(settings, "min_available_memory", None)
    with cli_memory_guardrails(total_memory=100 * GB) as (max_rss, min_avail):
        assert (max_rss, min_avail) == (12 * GB, 6 * GB)
        wd = memory_watchdog_from_settings()     # 默认化真的接到看门狗工厂
        assert wd is not None
        assert wd.max_rss == 12 * GB and wd.min_available == 6 * GB
    # 退出后恢复：API 直调语义不变（不启用）
    assert settings.max_memory is None and settings.min_available_memory is None
    assert memory_watchdog_from_settings() is None
