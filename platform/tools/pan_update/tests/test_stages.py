"""stages.py 行为测试：注入 runner 离线验证阶段链、续跑标记、失败上抛、flock 单实例、日志。

需求源：.superpowers/sdd/2026-09-16-pan-data-update-plan/task-4-brief.md
设计：knowledge/design/workspace/2026-09-16-pan-data-update-design.md §3/§5/§8
"""
import datetime
import subprocess
import sys
import threading

import pytest

from pan_update import stages

def test_run_category_stage_records_and_resumes(tmp_path):
    calls = []
    def runner(cmd, log, env=None): calls.append(cmd)
    s = {"version": 1, "files": {}, "stages": {}, "runs": []}
    stages.STAGE_CHAINS["toy"] = [["echo", "a"], ["echo", "b"]]
    stages.run_category_stage(s, "toy", "build", runner=runner)
    assert calls == [["echo", "a"], ["echo", "b"]]
    # 阶段标记后重跑 no-op
    stages.run_category_stage(s, "toy", "build", runner=runner)
    assert len(calls) == 2
    assert s["stages"]["toy"]["build"]

def test_stage_failure_raises_with_stage_name(tmp_path):
    def bad(cmd, log, env=None): raise stages.StageError("toy", cmd, 1, "boom")
    s = {"version": 1, "files": {}, "stages": {}, "runs": []}
    with pytest.raises(stages.StageError):
        stages.run_category_stage(s, "toy", "publish", runner=bad)


# —— 增补守卫（红→绿）：失败即停/不标、phase 记账、未知类别、log/env 透传 ——

def test_failure_stops_chain_and_leaves_marker_unset():
    calls = []
    def runner(cmd, log, env=None):
        calls.append(cmd)
        if cmd[-1] == "b":
            raise stages.StageError("guard", cmd, 2, "boom")
    s = {"version": 1, "files": {}, "stages": {}, "runs": []}
    stages.STAGE_CHAINS["guard"] = [["echo", "a"], ["echo", "b"], ["echo", "c"]]
    with pytest.raises(stages.StageError):
        stages.run_category_stage(s, "guard", "build", runner=runner)
    assert calls == [["echo", "a"], ["echo", "b"]]
    assert "build" not in s["stages"].get("guard", {})
    # 修复后重跑：命令幂等，整链重放，成功才落标记
    calls.clear()
    stages.run_category_stage(s, "guard", "build",
                              runner=lambda cmd, log, env=None: calls.append(cmd))
    assert calls == [["echo", "a"], ["echo", "b"], ["echo", "c"]]
    assert s["stages"]["guard"]["build"]


def test_phase_markers_scoped_per_phase():
    calls = []
    def runner(cmd, log, env=None): calls.append(cmd)
    s = {"version": 1, "files": {}, "stages": {}, "runs": []}
    stages.STAGE_CHAINS["guard2"] = [["echo", "x"]]
    stages.run_category_stage(s, "guard2", "build", runner=runner)
    stages.run_category_stage(s, "guard2", "build", runner=runner)
    assert len(calls) == 1
    stages.run_category_stage(s, "guard2", "publish", runner=runner)
    assert len(calls) == 2
    assert s["stages"]["guard2"]["build"] and s["stages"]["guard2"]["publish"]


def test_unknown_category_fails_loud():
    s = {"version": 1, "files": {}, "stages": {}, "runs": []}
    with pytest.raises(KeyError):
        stages.run_category_stage(s, "no_such_category", "build",
                                  runner=lambda cmd, log, env=None: None)


def test_runner_gets_log_callable_and_env():
    seen = {}
    def runner(cmd, log, env=None):
        seen["log"] = log
        seen["env"] = env
    s = {"version": 1, "files": {}, "stages": {}, "runs": []}
    stages.STAGE_CHAINS["guard3"] = [["echo", "x"]]
    lines = []
    my_log = lines.append
    stages.run_category_stage(s, "guard3", "build", runner=runner,
                              log=my_log, env={"A": "1"})
    assert seen["env"] == {"A": "1"}
    assert callable(seen["log"])
    assert seen["log"] is my_log


# —— run_cmd：真实子进程（sys.executable），错误含 stage/cmd/rc/tail ——

def test_run_cmd_success_streams_and_returns_none():
    lines = []
    assert stages.run_cmd([sys.executable, "-c", "print('T4-OUT-42')"], log=lines.append) is None
    assert any("T4-OUT-42" in line for line in lines)


def test_run_cmd_failure_raises_with_rc_and_bounded_tail():
    code = "import sys; [print(f'L{i:02d}') for i in range(1, 51)]; sys.exit(7)"
    cmd = [sys.executable, "-c", code]
    with pytest.raises(stages.StageError) as ei:
        stages.run_cmd(cmd, log=lambda line: None)
    err = ei.value
    assert err.rc == 7
    assert err.cmd == cmd
    assert "L50" in err.tail and "L01" not in err.tail
    assert len(err.tail.splitlines()) <= 20


def test_run_cmd_failure_stage_name_from_script(tmp_path):
    script = tmp_path / "import_daily.py"
    script.write_text("import sys; sys.exit(2)", encoding="utf-8")
    with pytest.raises(stages.StageError) as ei:
        stages.run_cmd([sys.executable, str(script)], log=lambda line: None)
    assert ei.value.stage == "import_daily"


def test_run_cmd_missing_executable_is_stage_error(tmp_path):
    with pytest.raises(stages.StageError) as ei:
        stages.run_cmd([str(tmp_path / "missing.py")], log=lambda line: None)
    assert ei.value.stage == "missing"
    assert ei.value.rc != 0


def test_run_cmd_env_overrides_and_inherits():
    code = "import os; print(os.environ.get('T4_ENV'), bool(os.environ.get('PATH')))"
    lines = []
    stages.run_cmd([sys.executable, "-c", code], log=lines.append, env={"T4_ENV": "ok"})
    assert any("ok True" in line for line in lines)


# —— single_instance：fcntl.flock 非阻塞，占用 → RuntimeError，退出释放 ——

def test_single_instance_blocks_second_holder_and_releases(tmp_path):
    lock = tmp_path / "pan_update.lock"
    with stages.single_instance(lock):
        assert lock.exists()
        outcomes = []
        def attempt():
            try:
                with stages.single_instance(lock):
                    outcomes.append("entered")
            except RuntimeError:
                outcomes.append("blocked")
        t = threading.Thread(target=attempt)
        t.start()
        t.join(timeout=3)
        assert not t.is_alive(), "第二次获取应非阻塞立即失败（不允许等待）"
        assert outcomes == ["blocked"]
    with stages.single_instance(lock):
        pass
    lock.unlink()
    with stages.single_instance(tmp_path / "deep" / "nested.lock"):
        pass


def test_single_instance_cross_process(tmp_path):
    lock = tmp_path / "pan_update.lock"
    code = (
        "import fcntl, time\n"
        f"f = open({str(lock)!r}, 'a+')\n"
        "fcntl.flock(f.fileno(), fcntl.LOCK_EX)\n"
        "print('locked', flush=True)\n"
        "time.sleep(15)\n"
    )
    holder = subprocess.Popen([sys.executable, "-c", code],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        with pytest.raises(RuntimeError) as ei:
            with stages.single_instance(lock):
                pytest.fail("跨进程持锁时不应进入临界区")
        assert str(lock) in str(ei.value)
    finally:
        holder.kill()
        holder.wait(timeout=10)


# —— open_log：runs/platform/logs/pan_update-YYYYMMDD.log，追加不截断，行带类别 ——

def test_open_log_writes_daily_file_appends_with_category(tmp_path, monkeypatch):
    monkeypatch.setattr(stages, "LOG_DIR", tmp_path)
    d = datetime.date(2026, 9, 16)
    log = stages.open_log("daily", d)
    log("阶段 build 开始")
    log("阶段 build 完成 1.2s")
    f = tmp_path / "pan_update-20260916.log"
    text = f.read_text(encoding="utf-8")
    assert "[daily]" in text
    assert "阶段 build 开始" in text and "1.2s" in text
    log2 = stages.open_log("minutes", d)
    log2("分钟链")
    text2 = f.read_text(encoding="utf-8")
    assert "阶段 build 开始" in text2 and "[minutes] 分钟链" in text2


def test_log_dir_is_design_path():
    assert stages.LOG_DIR.as_posix().endswith("runs/platform/logs")
