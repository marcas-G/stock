"""memguard（R30 主机内存保护）单测 —— 规格来源：R30 任务书「主机内存保护体系」：
2s 采样 /proc/meminfo + 本用户进程 RSS；纯决策函数阈值 warn<10GB / term<5GB /
kill<2.5GB；只杀 gaolei 且 RSS>=2GB 的候选（cmd 匹配
python|pytest|vllm|run_pipeline|factorlab|convert|ingest|polars|jupyter）；
保护 sshd/systemd/opencode/code/vscode-server/clickhouse/memguard/bash；
llama-server 默认保护但 RSS>38GB 转候选；SIGTERM→3s→SIGKILL；30s 冷却；
触发时 top5 RSS 快照；--dry-run 不杀；memlog 10s/7 天轮转。

R30.1 swap 压力前置触发（机械盘 swap 是 freeze 主因，提前动手避免换页到 HDD）：
swap_free<10GB 且 avail<15GB → term 候选；swap_free<6GB 且 avail<8GB → kill；
与原 avail 阈值取更严重者（先触发者）；heartbeat 带 swap 用量与触发来源标注。

断言均针对行为（不是格式）：选择/动作/信号序列/文件内容/日志快照。
"""
from __future__ import annotations

import json
import logging
import signal
import time
from pathlib import Path

import pytest

import memguard as mg

GB = 1024 ** 3
MB = 1024 ** 2


def P(pid, rss, cmd, user="gaolei", ):
    return mg.ProcInfo(pid=pid, user=user, rss=rss, cmd=cmd)


MEMINFO_OK = """MemTotal:       131072000 kB
MemFree:         20000000 kB
MemAvailable:    41943040 kB
Buffers:          1000000 kB
Cached:          30000000 kB
SwapTotal:       67108864 kB
SwapFree:        33554432 kB
"""


# ---------------- /proc/meminfo 解析 ----------------

def test_parse_meminfo_returns_available_and_swapfree_bytes():
    s = mg.parse_meminfo(MEMINFO_OK)
    assert s.available == 41943040 * 1024        # 40GB
    assert s.swap_free == 33554432 * 1024        # 32GB
    assert s.total == 131072000 * 1024


def test_parse_meminfo_missing_key_fails_loud():
    with pytest.raises(ValueError, match="MemAvailable"):
        mg.parse_meminfo("MemTotal: 1 kB\nSwapFree: 1 kB\n")


# ---------------- 阈值分级（warn<10GB / term<5GB / kill<2.5GB） ----------------

@pytest.mark.parametrize("avail_gb,expected", [
    (20, "ok"), (10, "ok"), (9.99, "warn"), (5, "warn"),
    (4.99, "term"), (2.5, "term"), (2.49, "kill"), (0.5, "kill"),
])
def test_level_for_boundaries(avail_gb, expected):
    assert mg.level_for(int(avail_gb * GB), mg.DEFAULT_THRESHOLDS) == expected


# ---------------- 候选选择（用户 / RSS / cmd / 保护名单 / llama 例外） ----------------

def test_select_candidates_only_target_user():
    procs = [P(1, 5 * GB, "python heavy.py", user="someone_else"),
             P(2, 5 * GB, "python heavy.py")]
    got = mg.select_candidates(procs, target_user="gaolei",
                               thresholds=mg.DEFAULT_THRESHOLDS)
    assert [p.pid for p in got] == [2]


def test_select_candidates_requires_rss_at_least_2gb():
    procs = [P(1, 2 * GB - 1, "python a.py"), P(2, 2 * GB, "python b.py")]
    got = mg.select_candidates(procs, target_user="gaolei",
                               thresholds=mg.DEFAULT_THRESHOLDS)
    assert [p.pid for p in got] == [2]


@pytest.mark.parametrize("cmd", [
    "python run.py", "pytest -q", "vllm serve model", "run_pipeline all",
    "factorlab run spec.yaml", "convert parquet", "ch_ingest daily",
    "polars scan", "jupyter-lab",
])
def test_select_candidates_matches_heavy_cmd_patterns(cmd):
    got = mg.select_candidates([P(7, 3 * GB, cmd)], target_user="gaolei",
                               thresholds=mg.DEFAULT_THRESHOLDS)
    assert [p.pid for p in got] == [7]


def test_select_candidates_ignores_unmatched_cmd():
    procs = [P(1, 5 * GB, "node some_server.js"), P(2, 5 * GB, "sleep 999")]
    assert mg.select_candidates(procs, target_user="gaolei",
                                thresholds=mg.DEFAULT_THRESHOLDS) == []


@pytest.mark.parametrize("cmd", [
    "/usr/sbin/sshd -D", "systemd --user", "opencode run",
    "node /usr/bin/code-server", "vscode-server --port 8080",
    "clickhouse server --config-file=config.xml", "bash heavy.sh x",
    "python memguard.py --live",
])
def test_protected_names_never_candidates(cmd):
    got = mg.select_candidates([P(3, 50 * GB, cmd)], target_user="gaolei",
                               thresholds=mg.DEFAULT_THRESHOLDS)
    assert got == []


def test_llama_server_protected_below_38gb_candidate_above():
    small = P(10, 37 * GB, "llama-server -m model.gguf")
    big = P(11, 39 * GB, "llama-server -m model.gguf")
    got = mg.select_candidates([small, big], target_user="gaolei",
                               thresholds=mg.DEFAULT_THRESHOLDS)
    assert [p.pid for p in got] == [11]
    assert 37 * GB < mg.DEFAULT_THRESHOLDS.llama_candidate_rss <= 39 * GB


def test_candidates_sorted_by_rss_desc():
    procs = [P(1, 3 * GB, "python a"), P(2, 8 * GB, "python b"),
             P(3, 5 * GB, "python c")]
    got = mg.select_candidates(procs, target_user="gaolei",
                               thresholds=mg.DEFAULT_THRESHOLDS)
    assert [p.pid for p in got] == [2, 3, 1]


# ---------------- 决策（纯函数） ----------------

def _procs():
    return [P(1, 6 * GB, "python a"), P(2, 3 * GB, "pytest b")]


def test_decide_ok_no_action():
    d = mg.decide(20 * GB, _procs(), swap_free=50 * GB,
                  thresholds=mg.DEFAULT_THRESHOLDS,
                  last_action_ts=None, now=1000.0, target_user="gaolei")
    assert d.level == "ok" and d.action == "none" and d.victims == ()


def test_decide_warn_no_victims():
    d = mg.decide(8 * GB, _procs(), swap_free=50 * GB,
                  thresholds=mg.DEFAULT_THRESHOLDS,
                  last_action_ts=None, now=1000.0, target_user="gaolei")
    assert d.level == "warn" and d.action == "none" and d.victims == ()


def test_decide_term_selects_victims_rss_desc():
    d = mg.decide(4 * GB, _procs(), swap_free=50 * GB,
                  thresholds=mg.DEFAULT_THRESHOLDS,
                  last_action_ts=None, now=1000.0, target_user="gaolei")
    assert d.level == "term" and d.action == "terminate" and d.victims == (1, 2)


def test_decide_kill_level_and_no_candidates():
    d = mg.decide(1 * GB, [P(1, 100 * MB, "python tiny")], swap_free=50 * GB,
                  thresholds=mg.DEFAULT_THRESHOLDS,
                  last_action_ts=None, now=1000.0, target_user="gaolei")
    assert d.level == "kill" and d.action == "none" and d.victims == ()


def test_decide_cooldown_blocks_for_30s():
    kw = dict(thresholds=mg.DEFAULT_THRESHOLDS, target_user="gaolei",
              swap_free=50 * GB)
    cooling = mg.decide(1 * GB, _procs(), last_action_ts=990.0, now=1000.0, **kw)
    assert cooling.action == "cooldown" and cooling.victims == ()
    ready = mg.decide(1 * GB, _procs(), last_action_ts=960.0, now=1000.0, **kw)
    assert ready.action == "terminate" and ready.victims == (1, 2)
    assert mg.DEFAULT_THRESHOLDS.cooldown_s == 30.0


def test_decide_never_picks_protected_or_other_users():
    procs = [P(1, 60 * GB, "clickhouse server"), P(2, 50 * GB, "python x", user="bob"),
             P(3, 30 * GB, "llama-server -m m.gguf")]
    d = mg.decide(1 * GB, procs, swap_free=50 * GB,
                  thresholds=mg.DEFAULT_THRESHOLDS,
                  last_action_ts=None, now=1000.0, target_user="gaolei")
    assert d.victims == ()


# ---------------- swap 压力前置触发（R30.1） ----------------

@pytest.mark.parametrize("avail_gb,swap_free_gb,expected", [
    (20, 50, "ok"), (14.99, 50, "ok"), (10, 50, "ok"),
    (14.99, 9.99, "term"),      # swap 前置：avail 仍 >10GB（原判 ok）
    (15, 9.99, "ok"),           # avail 边界：不严格小于 15 → 不触发
    (14.99, 10, "ok"),          # swap 边界：不严格小于 10 → 不触发
    (8, 5.99, "term"),          # kill 的 avail 边界不满足 → 退到 term
    (8, 9.99, "term"),
    (7.99, 5.99, "kill"),       # swap kill 前置：avail 仍在 warn 区间
    (7.99, 6, "term"),          # kill 的 swap 边界不满足 → term
    (7.99, 50, "warn"),         # swap 健康 → 原 avail 判定不受影响
    (4.99, 4, "kill"),          # 原判 term + swap kill → 取更严重者
    (2.49, 50, "kill"),         # 原 avail kill 仍生效
])
def test_level_for_swap_boundaries_and_first_trigger(avail_gb, swap_free_gb, expected):
    level, _ = mg.classify(int(avail_gb * GB), int(swap_free_gb * GB),
                           mg.DEFAULT_THRESHOLDS)
    assert level == expected


def test_decide_swap_term_upgrades_before_avail_ok():
    d = mg.decide(14 * GB, _procs(), swap_free=9 * GB,
                  thresholds=mg.DEFAULT_THRESHOLDS,
                  last_action_ts=None, now=1000.0, target_user="gaolei")
    assert d.level == "term" and d.action == "terminate" and d.victims == (1, 2)
    assert d.source == "swap"


def test_decide_swap_kill_upgrades_avail_term():
    d = mg.decide(7 * GB, _procs(), swap_free=5 * GB,
                  thresholds=mg.DEFAULT_THRESHOLDS,
                  last_action_ts=None, now=1000.0, target_user="gaolei")
    assert d.level == "kill" and d.action == "terminate"
    assert d.source == "swap"


def test_decide_avail_only_source_is_avail():
    d = mg.decide(4 * GB, _procs(), swap_free=50 * GB,
                  thresholds=mg.DEFAULT_THRESHOLDS,
                  last_action_ts=None, now=1000.0, target_user="gaolei")
    assert d.level == "term" and d.source == "avail"


def test_decide_both_same_level_flags_both_sources():
    d = mg.decide(4 * GB, _procs(), swap_free=9 * GB,
                  thresholds=mg.DEFAULT_THRESHOLDS,
                  last_action_ts=None, now=1000.0, target_user="gaolei")
    assert d.level == "term" and d.source == "avail+swap"


# ---------------- MemGuard.step：动作 / 信号序列 / 冷却 / dry-run ----------------

class _Rig:
    """测试台：可控 meminfo / 进程表 / 信号 / 时钟 / 存活表。"""

    def __init__(self, tmp_path, *, avail=20 * GB, procs=None, dry_run=False,
                 swap_free=50 * GB, swap_total=64 * GB):
        self.signals = []
        self.sleeps = []
        self.alive = {}
        self.now = 1000.0
        self.mem = mg.MemSample(total=125 * GB, available=avail,
                                swap_free=swap_free, swap_total=swap_total)
        self.procs = list(procs or [])
        self.g = mg.MemGuard(
            target_user="gaolei", dry_run=dry_run,
            state_dir=tmp_path / "state",
            meminfo_reader=lambda: self.mem,
            proc_reader=lambda: list(self.procs),
            signal_sender=lambda pid, sig: self.signals.append((sig, pid)),
            alive_reader=lambda pid: self.alive.get(pid, False),
            sleeper=lambda s: self.sleeps.append(s),
            clock=lambda: self.now,
        )

    def step(self):
        return self.g.step()


def test_step_dry_run_never_signals(tmp_path, caplog):
    rig = _Rig(tmp_path, avail=1 * GB, procs=[P(1, 5 * GB, "python a")], dry_run=True)
    with caplog.at_level(logging.WARNING, logger="memguard"):
        res = rig.step()
    assert rig.signals == []                       # 一个信号都没发
    assert res.decision.action == "terminate"      # 决策仍意图终止（可审计）
    assert res.executed is False
    assert "dry-run" in caplog.text


def test_step_sends_sigterm_then_sigkill_after_grace(tmp_path):
    rig = _Rig(tmp_path, avail=1 * GB,
               procs=[P(42, 5 * GB, "python a")])
    rig.alive[42] = True                           # TERM 后仍活着 → 升级 KILL
    res = rig.step()
    assert rig.signals == [(signal.SIGTERM, 42), (signal.SIGKILL, 42)]
    assert rig.sleeps == [3.0]                     # SIGTERM→3s→SIGKILL
    assert res.executed is True


def test_step_no_sigkill_when_process_dead(tmp_path):
    rig = _Rig(tmp_path, avail=1 * GB, procs=[P(42, 5 * GB, "python a")])
    rig.alive[42] = False
    rig.step()
    assert rig.signals == [(signal.SIGTERM, 42)]


def test_step_action_sets_30s_cooldown(tmp_path):
    rig = _Rig(tmp_path, avail=1 * GB, procs=[P(42, 5 * GB, "python a")])
    rig.alive[42] = False
    rig.step()
    assert rig.g.last_action_ts == 1000.0
    rig.now = 1005.0                               # 冷却期内
    res = rig.step()
    assert res.decision.action == "cooldown"
    assert rig.signals == [(signal.SIGTERM, 42)]   # 没有第二次信号


def test_step_trigger_logs_top5_snapshot(tmp_path, caplog):
    procs = [P(i, (10 - i) * GB, f"python w{i}.py") for i in range(7)]
    rig = _Rig(tmp_path, avail=1 * GB, procs=procs)
    for p in procs:
        rig.alive[p.pid] = False
    with caplog.at_level(logging.WARNING, logger="memguard"):
        rig.step()
    text = caplog.text
    for p in procs[:5]:
        assert f"pid={p.pid}" in text              # 触发日志含 top5 RSS 快照
    assert "pid=5" not in text and "pid=6" not in text   # 第 6/7 名不进快照


def test_step_warn_logs_no_signal(tmp_path, caplog):
    rig = _Rig(tmp_path, avail=8 * GB, procs=[P(1, 5 * GB, "python a")])
    with caplog.at_level(logging.WARNING, logger="memguard"):
        res = rig.step()
    assert rig.signals == []
    assert res.decision.level == "warn"
    assert "warn" in caplog.text.lower()


def test_step_periodic_heartbeat_in_log(tmp_path, caplog):
    """服务日志必须有周期采样证据（装完验证项）：默认 60s 一条 heartbeat，
    含 avail/level；不是每步都刷（避免 journal 噪声）。"""
    rig = _Rig(tmp_path, avail=20 * GB, procs=[P(1, 5 * GB, "python a")])
    with caplog.at_level(logging.INFO, logger="memguard"):
        rig.step()                                 # 首拍
        rig.now += 2.0
        rig.step()
        rig.now += 2.0
        rig.step()
    beats = [r for r in caplog.records if "heartbeat" in r.getMessage()]
    assert len(beats) == 1                         # 4s 内只写 1 条
    assert "level=ok" in beats[0].getMessage()
    assert "avail=20.0GB" in beats[0].getMessage()
    rig.now += 61.0
    with caplog.at_level(logging.INFO, logger="memguard"):
        rig.step()
    beats = [r for r in caplog.records if "heartbeat" in r.getMessage()]
    assert len(beats) == 2                         # 60s 到点再写
    assert mg.DEFAULT_HEARTBEAT_S == 60.0


def test_heartbeat_includes_swap_usage_and_source(tmp_path, caplog):
    """R30.1：心跳必须带 swap 用量（used=total-free）与触发来源标注。"""
    rig = _Rig(tmp_path, avail=20 * GB, swap_total=64 * GB, swap_free=50 * GB)
    with caplog.at_level(logging.INFO, logger="memguard"):
        rig.step()
    msg = next(r.getMessage() for r in caplog.records if "heartbeat" in r.getMessage())
    assert "swap_used=14.0GB" in msg               # 64-50，不是硬编码
    assert "swap_free=50.0GB" in msg
    assert "source=ok" in msg


def test_heartbeat_source_swap_when_swap_pressure_early(tmp_path, caplog):
    """R30.1：swap 前置条触发时，心跳 level/source 标注来源=swap。"""
    rig = _Rig(tmp_path, avail=14 * GB, swap_total=64 * GB, swap_free=9 * GB)
    with caplog.at_level(logging.INFO, logger="memguard"):
        res = rig.step()
    msg = next(r.getMessage() for r in caplog.records if "heartbeat" in r.getMessage())
    assert res.decision.level == "term" and res.decision.source == "swap"
    assert "level=term" in msg and "source=swap" in msg
    assert "swap_used=55.0GB" in msg


# ---------------- memlog 取证采样（10s 粒度 / 7 天轮转） ----------------

def test_step_writes_memlog_tsv_row(tmp_path):
    rig = _Rig(tmp_path, avail=20 * GB,
               procs=[P(1, 5 * GB, "python a"), P(2, 3 * GB, "python b")])
    rig.g.memlog_interval = 0.0                    # 本次必写
    rig.step()
    path = rig.g.memlog_path
    assert path == tmp_path / "state" / "memlog.tsv"
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0].split("\t")[:4] == ["time", "avail", "swap_free", "load1"]
    cols = lines[1].split("\t")
    assert abs(int(cols[1]) - 20 * GB) < MB        # 真读到的 avail（不是硬编码）
    assert "1:5.0GB" in lines[1]                   # top5 RSS 快照
    assert "2:3.0GB" in lines[1]


def test_memlog_day_rotation_and_7day_retention(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    for name in ("memlog-2026-09-08.tsv", "memlog-2026-09-10.tsv",
                 "memlog-2026-09-12.tsv", "memlog-2026-09-17.tsv"):
        (state / name).write_text("x", encoding="utf-8")
    (state / "memlog.tsv").write_text("old", encoding="utf-8")
    # 2026-09-18：09-08 超 7 天删除；09-10 恰第 9 天删除；09-12/09-17 保留
    removed = mg.prune_memlog_history(state, now=mg.datetime_to_ts("2026-09-18"),
                                      keep_days=7)
    left = {p.name for p in state.glob("memlog-*.tsv")}
    assert "memlog-2026-09-08.tsv" not in left
    assert "memlog-2026-09-10.tsv" not in left
    assert {"memlog-2026-09-12.tsv", "memlog-2026-09-17.tsv"} <= left
    assert len(removed) == 2


# ---------------- CLI（--once / --dry-run） ----------------

def test_cli_once_dry_run_uses_real_meminfo_and_exits_0(capsys, tmp_path):
    rc = mg.main(["--once", "--dry-run", "--state-dir", str(tmp_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["level"] in ("ok", "warn", "term", "kill")
    assert out["available"] > 0                    # 真实 /proc/meminfo 采样
    assert out["dry_run"] is True


def test_cli_default_thresholds_match_spec():
    th = mg.DEFAULT_THRESHOLDS
    assert (th.warn, th.term, th.kill) == (10 * GB, 5 * GB, int(2.5 * GB))
    assert th.candidate_rss == 2 * GB
    assert th.cooldown_s == 30.0


def test_cli_live_interval_default_is_2s():
    assert mg.DEFAULT_INTERVAL_S == 2.0
    assert mg.DEFAULT_MEMLOG_INTERVAL_S == 10.0
    assert mg.MEMLOG_KEEP_DAYS == 7
