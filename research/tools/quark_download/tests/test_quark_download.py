"""quark 网盘下载脚本冒烟（R8c 补齐：此前 0 测试）。

能测什么 / 为什么：本工具的主体是网络 I/O（真调夸克 API），hermetic 测不了也不该
在测试里打网。可测且**有牙齿**的是两条：
1. **import 期不得碰文件系统**——原先模块顶层 `COOKIES = open("/tmp/quark_cookies.txt")`，
   换台机器/换个 cookie 路径连 import 都炸，"能不能跑"取决于环境而不是代码；
   现改为懒读 + 路径可配（`QUARK_COOKIE_FILE`）。用**子进程**断言（同进程已 import，
   测不出 import 期行为）。
2. cookie 缺失必须**显式报错**，不是带着空 Cookie 去请求（401 假象最难查）。
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOL_DIR = os.path.dirname(_HERE)


def _run_import(env_extra: dict) -> subprocess.CompletedProcess:
    env = dict(os.environ, **env_extra)
    code = (f"import sys; sys.path.insert(0, {_TOOL_DIR!r}); import quark_download_v2 as q;"
            " print(q.COOKIE_PATH); print(q.PREFIXES)")
    return subprocess.run([sys.executable, "-c", code], env=env,
                          capture_output=True, text=True)


def test_import_does_not_read_cookie_file(tmp_path):
    """cookie 文件不存在也必须能 import（配置只是运行时依赖，不是 import 依赖）。"""
    missing = str(tmp_path / "definitely-missing.txt")
    r = _run_import({"QUARK_COOKIE_FILE": missing})
    assert r.returncode == 0, f"import 期不应触碰 cookie 文件：{r.stderr}"
    # cookie 路径必须真的跟随配置（否则"可配"是假的，测试也没牙齿）
    assert r.stdout.splitlines()[0] == missing
    assert "00开头" in r.stdout


def test_prefixes_are_the_four_code_buckets():
    r = _run_import({"QUARK_COOKIE_FILE": str(_HERE)})
    assert r.returncode == 0, r.stderr
    assert "00开头" in r.stdout and "30开头" in r.stdout
    assert "60开头" in r.stdout and "68开头" in r.stdout


def test_missing_cookie_is_explicit_error(tmp_path, monkeypatch):
    """缺 cookie → 显式 FileNotFoundError（不静默用空串去请求）。"""
    sys.path.insert(0, _TOOL_DIR)
    import quark_download_v2 as q  # noqa: E402
    monkeypatch.setattr(q, "COOKIE_PATH", str(tmp_path / "nope.txt"))
    with pytest.raises(FileNotFoundError):
        q._cookies()


def test_cookie_read_is_stripped(tmp_path, monkeypatch):
    sys.path.insert(0, _TOOL_DIR)
    import quark_download_v2 as q  # noqa: E402
    p = tmp_path / "cookie.txt"
    p.write_text("  k=v; k2=v2 \n", encoding="utf-8")
    monkeypatch.setattr(q, "COOKIE_PATH", str(p))
    assert q._cookies() == "k=v; k2=v2"      # 首尾空白/换行剥掉（HTTP 头值不能带换行）
