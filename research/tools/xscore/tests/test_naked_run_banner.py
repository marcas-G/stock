"""E4：非流水线直跑警示（源码清单 + 实跑一条）。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

STOCK = Path(__file__).resolve().parents[4]
SCRIPTS = [
    STOCK / "research/tools/xscore/run_ladder.py",
    STOCK / "research/tools/xscore/run_split.py",
    STOCK / "research/tools/xscore/pipeline/score_once.py",
    STOCK / "research/tools/xscore/pipeline/portfolio_once.py",
    STOCK / "research/tools/porteval/run.py",
]


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_banner_present_and_wired(script: Path):
    text = script.read_text(encoding="utf-8")
    assert "_dev_banner" in text and "非流水线运行（dev only）" in text
    assert "if __name__ == \"__main__\":" in text
    idx = text.index("if __name__ == \"__main__\":")
    assert "_dev_banner()" in text[idx:idx + 120], "banner 必须在 __main__ 中调用"


def test_banner_live_and_marked_silent(tmp_path: Path):
    script = STOCK / "research/tools/porteval/run.py"
    env = {k: v for k, v in os.environ.items() if k != "FACTORLAB_PIPELINE"}
    r1 = subprocess.run([sys.executable, str(script), "--help"],
                        capture_output=True, text=True, env=env)
    assert "非流水线运行（dev only）" in r1.stderr
    r2 = subprocess.run([sys.executable, str(script), "--help"],
                        capture_output=True, text=True,
                        env={**env, "FACTORLAB_PIPELINE": "1"})
    assert "非流水线运行" not in r2.stderr
