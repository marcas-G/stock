"""R30 Task4 存根突变检查：把 stages.py 实现替换为硬编码/降级存根，测试必须失败。

不修改工作区最终状态：每个突变跑完立即恢复原文（最后断言文件与原文一致）。
复现：python3 governance/evidence/verification/R30/task4/mutation.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path("/data/students/gaolei/stock")
STAGES = REPO / "platform/tools/pan_update/stages.py"
PY = REPO / "platform/.venv/bin/python"
CWD = REPO / "platform"

ORIG = STAGES.read_text(encoding="utf-8")

RUN_MARK = "    for cmd in chain:\n        runner(cmd, log=log, env=env)\n    marks[phase] = _now()"

MUTS = {
    "链不跑只标标记（阶段编排存根）": ORIG.replace(
        RUN_MARK,
        "    marks[phase] = _now()",
    ),
    "忽略阶段标记（每次都重跑）": ORIG.replace(
        "    if marks.get(phase):",
        "    if False:",
    ),
    "先标后跑（失败也落标记）": ORIG.replace(
        RUN_MARK,
        "    marks[phase] = _now()\n" + RUN_MARK.replace("\n    marks[phase] = _now()", ""),
    ),
    "吞掉失败继续跑（不 fail 即停）": ORIG.replace(
        RUN_MARK,
        "    for cmd in chain:\n"
        "        try:\n"
        "            runner(cmd, log=log, env=env)\n"
        "        except Exception:\n"
        "            pass\n"
        "    marks[phase] = _now()",
    ),
    "unknown 类别静默空链（不报错）": ORIG.replace(
        '        raise KeyError(f"未配置阶段链：{category}（STAGE_CHAINS 待填实）")',
        "        STAGE_CHAINS[category] = []",
    ),
    "single_instance 不 flock（永远可入）": ORIG.replace(
        "            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)",
        "            pass",
    ),
    "single_instance 恒拒绝（锁永远占用）": ORIG.replace(
        "            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)",
        "            raise OSError('busy')",
    ),
    "single_instance 不建父目录": ORIG.replace(
        "    lock_path.parent.mkdir(parents=True, exist_ok=True)",
        "    pass",
    ),
    "single_instance 占用报错不含锁路径": ORIG.replace(
        '                f"单实例锁被占用：{lock_path}（持锁 pid={holder or \'未知\'}）") from ex',
        '                "锁被占用") from ex',
    ),
    "single_instance 去 LOCK_NB（阻塞等待）": ORIG.replace(
        "            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)",
        "            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)",
    ),
    "run_cmd 忽略 rc（失败不抛）": ORIG.replace(
        "    if rc != 0:",
        "    if False:",
    ),
    "run_cmd StageError 丢 rc（恒 0）": ORIG.replace(
        '        raise StageError(stage, cmd, rc, "\\n".join(lines[-TAIL_LINES:]))',
        '        raise StageError(stage, cmd, 0, "\\n".join(lines[-TAIL_LINES:]))',
    ),
    "run_cmd tail 不截断（全量输出）": ORIG.replace(
        '"\\n".join(lines[-TAIL_LINES:])',
        '"\\n".join(lines)',
    ),
    "run_cmd 忽略 env 覆盖": ORIG.replace(
        "    full_env = {**os.environ, **(env or {})}",
        "    full_env = dict(os.environ)",
    ),
    "run_cmd 输出不喂 log": ORIG.replace(
        "                lines.append(line)\n                log(line)",
        "                lines.append(line)",
    ),
    "run_cmd stage 名恒空": ORIG.replace(
        "    stage = _stage_name(cmd)",
        '    stage = ""',
    ),
    "run_cmd Popen 错误不转 StageError": ORIG.replace(
        "    except OSError as ex:\n        raise StageError(stage, cmd, -1, str(ex)) from ex",
        "    except OSError:\n        raise",
    ),
    "open_log 截断重写（非追加）": ORIG.replace(
        '        with open(path, "a", encoding="utf-8") as fh:',
        '        with open(path, "w", encoding="utf-8") as fh:',
    ),
    "open_log 行无类别前缀": ORIG.replace(
        '            fh.write(f"[{category}] {line}\\n")',
        '            fh.write(f"{line}\\n")',
    ),
}


def main() -> int:
    for label, mutated in MUTS.items():
        assert mutated != ORIG, f"{label}: 突变未生效"
        STAGES.write_text(mutated, encoding="utf-8")
        try:
            proc = subprocess.run(
                [str(PY), "-m", "pytest", "tools/pan_update/tests/test_stages.py", "-q"],
                cwd=CWD, capture_output=True, text=True, timeout=120,
            )
        except subprocess.TimeoutExpired:
            print(f"\n### 突变：{label}")
            print("期望：测试失败；实测：TIMEOUT（挂死=未通过，视为被抓住）")
            continue
        out = (proc.stdout + proc.stderr).strip()
        verdict = "FAIL（符合预期：存根被测试抓住）" if proc.returncode != 0 else "PASS（不符合预期！）"
        print(f"\n### 突变：{label}")
        print(f"期望：测试失败；实测：{verdict}  [exit={proc.returncode}]")
        print("\n".join(out.splitlines()[-3:]) if out else "(无输出)")
    STAGES.write_text(ORIG, encoding="utf-8")
    assert STAGES.read_text(encoding="utf-8") == ORIG, "恢复失败：stages.py 与原文不一致"
    proc = subprocess.run(
        [str(PY), "-m", "pytest", "tools/pan_update/tests", "-q"],
        cwd=CWD, capture_output=True, text=True,
    )
    print("\n恢复原文后复跑：", (proc.stdout + proc.stderr).strip().splitlines()[-1],
          f"[exit={proc.returncode}]")
    return 0 if proc.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
