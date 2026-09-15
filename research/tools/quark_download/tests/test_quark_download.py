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
sys.path.insert(0, _TOOL_DIR)      # 供同进程 import quark_client / 两个入口（子进程里另插一次）


def _run_import(env_extra: dict) -> subprocess.CompletedProcess:
    env = dict(os.environ, **env_extra)
    code = (f"import sys; sys.path.insert(0, {_TOOL_DIR!r});"
            " import quark_client as QC; import quark_download_v2 as q;"
            " print(QC.COOKIE_PATH); print(q.PREFIXES)")
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


# ── R16：共享客户端（三个入口不再各写一份传输层）────────────────────
def test_cookie_path_follows_env_and_missing_is_explicit(tmp_path, monkeypatch):
    """cookie 路径跟随 `QUARK_COOKIE_FILE`；缺失时显式报错（不静默空串）——口径已收敛到
    `quark_client`（R16 前 v2 在内部、server 在 import 期静默读，两套）。"""
    code = (f"import sys; sys.path.insert(0, {_TOOL_DIR!r}); import quark_client as QC;"
            " print(QC.COOKIE_PATH)")
    env = dict(os.environ, QUARK_COOKIE_FILE=str(tmp_path / "nope.txt"))
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == str(tmp_path / "nope.txt"), r.stderr


def test_entries_share_one_transport_implementation():
    """两个入口的 http/get_stoken/get_download_urls/download_file 必须是**同一个对象**
    （来自 quark_client）——否则说明又长出了第二份实现。"""
    import quark_client as QC
    import quark_download_server as QS
    import quark_download_v2 as Q2
    import quark_share as QSH
    for fn in ("http", "get_stoken", "get_download_urls", "download_file", "UA",
               "HOST_PC", "PWD_ID", "STOKEN_TTL"):
        assert getattr(Q2, fn) is getattr(QC, fn), f"v2.{fn} 不是共享实现"
        assert getattr(QS, fn) is getattr(QC, fn), f"server.{fn} 不是共享实现"
    assert QSH.UA is QC.UA, "share.UA 不是共享实现"


def test_server_no_longer_reads_cookie_at_import(tmp_path):
    """server 原先在 import 期读 cookie 文件（缺失时**静默空串**）——已收敛为懒读 + 显式报错。"""
    env = dict(os.environ, QUARK_COOKIE_FILE=str(tmp_path / "nope.txt"))
    code = (f"import sys; sys.path.insert(0, {_TOOL_DIR!r});"
            " import quark_download_server as QS; print('IMPORT_OK')")
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert r.returncode == 0 and "IMPORT_OK" in r.stdout, r.stderr


def test_shared_client_cookie_semantics(tmp_path, monkeypatch):
    """共享客户端 cookie 口径：显式路径优先；两处都缺 → FileNotFoundError（不静默空串）。"""
    import quark_client as QC
    p = tmp_path / "cookie.txt"
    p.write_text("  k=v  \n", encoding="utf-8")
    monkeypatch.setattr(QC, "COOKIE_PATH", str(p))
    assert QC.cookies() == "k=v"
    monkeypatch.setattr(QC, "COOKIE_PATH", str(tmp_path / "missing.txt"))
    monkeypatch.setattr(QC, "_FALLBACK_COOKIE", str(tmp_path / "also-missing.txt"))
    with pytest.raises(FileNotFoundError):
        QC.cookies()
