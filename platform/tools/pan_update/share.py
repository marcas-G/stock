"""分享树遍历：类别根 → 递归文件清单（Entry）与子目录发现。

设计：knowledge/design/workspace/2026-09-16-pan-data-update-design.md §3/§4
- 单测经注入 ``listdir`` 离线运行；生产传输走 ``quark_client``（``_default_listdir``）。
- ``iter_category``/``walk_dir`` 返回 ``Entry`` dataclass；T3 在消费边界用
  ``dataclasses.asdict`` 转换（Plan P T2 接口裁决）。
- ``find_dir`` 首个参数为 ``listdir`` 以支持注入（brief 测试 ``find_dir(fake_listdir, ...)``
  为准；brief 接口行 ``find_dir(root_fid, name)`` 为简写）。
"""
from __future__ import annotations

import sys
import urllib.parse
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

try:
    from quark_download import quark_client
except ModuleNotFoundError:  # 脚本直启（无 conftest 铺路）→ 补 platform/tools 再导入
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from quark_download import quark_client

_PAGE_SIZE = 100
_MAX_PAGES = 1000


@dataclass
class Entry:
    name: str
    size: int
    fid: str
    fid_token: str
    rel_path: str
    is_dir: bool


def _join(prefix: str, name: str) -> str:
    return f"{prefix}/{name}" if prefix else name


def _walk(listdir: Callable[[str], "list[Entry]"], fid: str, prefix: str) -> "list[Entry]":
    """DFS：目录项不入结果但递归；文件 rel_path = 前缀 + 各层目录名。"""
    out: list[Entry] = []
    for e in listdir(fid):
        rel = _join(prefix, e.name)
        if e.is_dir:
            out.extend(_walk(listdir, e.fid, rel))
        else:
            out.append(replace(e, rel_path=rel))
    return out


def iter_category(listdir: Callable[[str], "list[Entry]"], start_fid: str) -> "list[Entry]":
    """递归遍历类别目录树，返回文件条目（rel_path 相对类别根）。"""
    return _walk(listdir, start_fid, "")


def walk_dir(fid: str, prefix: str = "") -> "list[Entry]":
    """生产入口：从分享内任意目录 fid 递归列文件，rel_path 前置 prefix。"""
    return _walk(_default_listdir, fid, prefix)


def find_dir(listdir: Callable[[str], "list[Entry]"], root_fid: str, name: str) -> str:
    """在 root_fid 的直接子目录中按 name 找目录 fid；不存在（或非目录）→ KeyError。"""
    for e in listdir(root_fid):
        if e.is_dir and e.name == name:
            return e.fid
    raise KeyError(f"目录不存在：{name!r}（pdir_fid={root_fid}）")


def _default_listdir(fid: str) -> "list[Entry]":
    """生产 listdir：quark ``share/sharepage/detail`` 分页（``_size=100``）。

    终止：``metadata._total`` 为 int 时以总数为准（已收 ≥ total；短页也继续翻，
    防服务端小页静默截断，``_MAX_PAGES`` 兜底）；无 total/非 int → 短页终止。
    失败（cookie 失效/401/非法 token/业务 status≠200）→ RuntimeError 显式上抛（设计 §8）。
    """
    stoken = urllib.parse.quote(quark_client.get_stoken(), safe="")
    out: list[Entry] = []
    for page in range(1, _MAX_PAGES + 1):
        url = (f"{quark_client.HOST_PC}/share/sharepage/detail?ver=2"
               f"&pwd_id={quark_client.PWD_ID}&stoken={stoken}"
               f"&pdir_fid={fid}&force=0&_page={page}&_size={_PAGE_SIZE}"
               f"&_fetch_total=1&_sort=")
        s, r = quark_client.http(url)
        if s != 200 or not r or r.get("status") != 200:
            raise RuntimeError(
                f"share detail 失败：HTTP {s} status={(r or {}).get('status')} "
                f"message={(r or {}).get('message')} pdir_fid={fid} page={page}")
        lst = (r.get("data") or {}).get("list") or []
        total = (r.get("metadata") or {}).get("_total")
        for it in lst:
            name = it["file_name"]
            out.append(Entry(
                name=name,
                size=int(it.get("size") or 0),
                fid=it["fid"],
                fid_token=it.get("share_fid_token") or "",
                rel_path=name,
                is_dir=bool(it.get("dir")),
            ))
        if isinstance(total, int):
            if len(out) >= total:
                return out
        elif len(lst) < _PAGE_SIZE:
            return out
    raise RuntimeError(f"share detail 分页超过 {_MAX_PAGES} 页：pdir_fid={fid}")
