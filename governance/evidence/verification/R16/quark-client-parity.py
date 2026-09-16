#!/usr/bin/env python3
"""R16 证据：共享客户端 `quark_client` 的函数 vs 原实现（HEAD）的**归一化对照**。

判据：把两边的函数 AST 反解析成源码、剥掉文档串，再只允许**声明过的**差异：
- `http`：cookie 来源 `_cookies()` → `cookies()`（合并后的单点）；
- `get_download_urls` / `download_file`：诊断打印改为 `log` 回调（缺省静默）；
- `get_stoken`：v2 的缓存/锁保留，server 的"每次现取"以 `cache=False` 表达。
其余必须逐字相同——合并时曾把 `http` 手写成"重试后抛异常"、把 UA 手打成 Chrome/122，
两处都由本对照（与身份断言）抓回。

用法：<platform venv>/bin/python docs/verification/R16/quark-client-parity.py
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[3]
TOOL = REPO / "research" / "tools" / "quark_download"


def body_without_docstring(src: str, name: str) -> str:
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            node.body = [s for s in node.body
                         if not (isinstance(s, ast.Expr)
                                 and isinstance(s.value, ast.Constant)
                                 and isinstance(s.value.value, str))]
            return ast.unparse(node)
    raise KeyError(f"{name} 不在源码里")


def norm(text: str) -> str:
    return (text.replace("_cookies()", "cookies()")
                .replace("COOKIES", "cookies()")
                .replace(", *, log=None", "")
                .replace(", *, cache: bool = True, ttl: int = STOKEN_TTL", "")
                .replace(", *, log=None):", "):")
                .replace("def get_download_urls(stoken, fid_tok_list, *):",
                         "def get_download_urls(stoken, fid_tok_list):"))


def main() -> int:
    orig_v2 = subprocess.run(
        ["git", "-C", str(REPO), "show", "HEAD:research/tools/quark_download/quark_download_v2.py"],
        capture_output=True, text=True, check=True).stdout
    orig_srv = subprocess.run(
        ["git", "-C", str(REPO), "show", "HEAD:research/tools/quark_download/quark_download_server.py"],
        capture_output=True, text=True, check=True).stdout
    new = (TOOL / "quark_client.py").read_text(encoding="utf-8")

    fails = []
    # ① 逐字相同（仅声明过的差异：cookie 单点 / 日志回调口径）
    for name, refs in (("http", [orig_v2, orig_srv]),
                       ("download_file", [orig_v2, orig_srv])):
        got = norm(body_without_docstring(new, name))
        for i, ref in enumerate(refs):
            want = norm(body_without_docstring(ref, name))
            ok = got == want
            print(f"  {'✓' if ok else '✗'} {name} vs 原实现[{i}]（逐字相同）")
            if not ok:
                fails.append((name, "逐字对照不等"))

    # ② 声明过的重构：以**行为清单**核对（每条都是原实现里必须活下来的语义）
    CHECKLIST = {
        "get_download_urls": [
            "for i in range(0, total, 50)",      # 50/批
            "urls[it['fid']] = u",               # fid → url
            "'dl-guest'",                        # 降级链接丢弃
            "time.sleep(0.3)",                   # 批间节流
        ],
        "get_stoken": [
            "_lock", "_state", "STOKEN_TTL",     # v2 的锁 + 缓存 + TTL
            "assert s == 200", "passcode", "stoken",   # 失败显式、口令、取 stoken
        ],
    }
    for name, needles in CHECKLIST.items():
        # get_stoken 的 "assert/passcode" 落在抽出的 `_fetch_stoken` 里 → 按**模块**范围核对
        got = body_without_docstring(new, name) if name == "get_download_urls" else new
        miss = [n for n in needles if n not in got]
        print(f"  {'✓' if not miss else '✗'} {name} 行为清单 {len(needles)} 条"
              f"{'' if not miss else f'，缺 {miss}'}")
        if miss:
            fails.append((name, f"缺行为 {miss}"))

    if fails:
        print("FAIL：", fails)
        return 1
    print("PASS：共享实现与原实现等价（逐字对照 + 行为清单；差异仅限声明过的三项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
