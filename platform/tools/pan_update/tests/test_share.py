"""share.py 行为测试：注入 fake listdir（离线递归）+ fake quark_client 传输（分页/URL）。

需求源：.superpowers/sdd/2026-09-16-pan-data-update-plan/task-2-brief.md
"""
import pytest

from pan_update import config, share

# brief 原文文件项为 ("name", "fid", size) 平铺三元组，与其 fake_listdir 的
# `for name, fid2 in ...` + `isinstance(fid2, tuple)` 互斥（必然 ValueError）。
# 最小化解：文件项改为嵌套 ("name", ("fid", size))，fake_listdir/断言逐字保留。
FAKE_TREE = {
    "root": [("日K线数据---复权因子-经典技术指标--bs点缠论划线", "d_daily"), ("A股分钟线", "d_min")],
    "d_daily": [("x.zip", ("f1", 10)), ("退市股", "d_tdx")],
    "d_tdx": [("000018_神州长城.xlsx", ("f2", 3))],
    "d_min": [("2026", "d_y")],
    "d_y": [("09", "d_m")],
    "d_m": [("20260916.zip", ("f3", 15))],
}

def fake_listdir(fid):
    out = []
    for name, fid2 in FAKE_TREE[fid]:
        if isinstance(fid2, tuple):
            _, size = fid2; out.append(share.Entry(name, size, fid2[0], "tok", name, False))
        else:
            out.append(share.Entry(name, 0, fid2, "tok", name, True))
    return out

def test_iter_category_recurses_and_skips_dirs():
    entries = share.iter_category(fake_listdir, "d_daily")
    assert {e.rel_path for e in entries} == {"x.zip", "退市股/000018_神州长城.xlsx"}

def test_find_dir_missing_fails():
    import pytest
    with pytest.raises(KeyError):
        share.find_dir(fake_listdir, "root", "不存在")


# —— 增补守卫（红→绿）：元数据保真、目录项排除、前缀、生产分页与失败路径 ——

def test_iter_category_keeps_metadata_and_excludes_dir_entries():
    entries = share.iter_category(fake_listdir, "root")
    assert {e.rel_path for e in entries} == {
        "日K线数据---复权因子-经典技术指标--bs点缠论划线/x.zip",
        "日K线数据---复权因子-经典技术指标--bs点缠论划线/退市股/000018_神州长城.xlsx",
        "A股分钟线/2026/09/20260916.zip",
    }
    assert all(not e.is_dir for e in entries)  # 目录项不入结果
    by_name = {e.name: e for e in entries}
    deep = by_name["000018_神州长城.xlsx"]
    assert (deep.fid, deep.fid_token, deep.size) == ("f2", "tok", 3)
    leaf = by_name["20260916.zip"]
    assert (leaf.fid, leaf.fid_token, leaf.size) == ("f3", "tok", 15)
    assert leaf.rel_path == "A股分钟线/2026/09/20260916.zip"  # 相对类别根，非 listdir 局部路径


def test_find_dir_returns_fid_and_rejects_file_names():
    assert share.find_dir(fake_listdir, "root", "A股分钟线") == "d_min"
    assert share.find_dir(fake_listdir, "d_daily", "退市股") == "d_tdx"
    with pytest.raises(KeyError):
        share.find_dir(fake_listdir, "d_daily", "x.zip")  # 文件不是目录，同样 KeyError


def _item(name, fid, size=0, *, is_dir=False, token="tk"):
    return {"file_name": name, "fid": fid, "size": size,
            "share_fid_token": token, "dir": is_dir}


def test_default_listdir_paginates_and_maps_fields(monkeypatch):
    page1 = [_item(f"f{i}.zip", f"id{i}", 10 + i, token=f"tk{i}") for i in range(100)]
    page2 = [
        _item("last.zip", "id-last", 99, token="tk-last"),
        {"file_name": "nosize.zip", "fid": "id-ns", "dir": False},  # size/token 缺省容忍
        _item("退市股", "dir-1", 0, is_dir=True, token=""),
    ]
    urls = []

    def fake_http(url, body=None, retry=3, timeout=60):
        urls.append(url)
        page = int(url.split("_page=")[1].split("&")[0])
        return 200, {"status": 200, "data": {"list": page1 if page == 1 else page2}}

    monkeypatch.setattr(share.quark_client, "http", fake_http)
    monkeypatch.setattr(share.quark_client, "get_stoken", lambda *a, **k: "ST")

    entries = share._default_listdir("fidX")

    assert len(entries) == 103 and len(urls) == 2  # 满页继续、短页收尾
    assert "share/sharepage/detail" in urls[0]
    assert f"pwd_id={config.PWD_ID}" in urls[0]  # 与 config 单点同值（漂移即红）
    assert "stoken=ST" in urls[0]
    assert "pdir_fid=fidX" in urls[0]
    assert "_size=100" in urls[0]
    assert "_page=1" in urls[0] and "_page=2" in urls[1]
    first, last = entries[0], entries[-1]
    assert (first.name, first.size, first.fid, first.fid_token, first.rel_path, first.is_dir) == \
        ("f0.zip", 10, "id0", "tk0", "f0.zip", False)
    assert entries[100].fid_token == "tk-last"
    nosize = entries[101]
    assert (nosize.size, nosize.fid_token, nosize.is_dir) == (0, "", False)
    assert (last.name, last.size, last.fid, last.rel_path, last.is_dir) == \
        ("退市股", 0, "dir-1", "退市股", True)


def test_default_listdir_fails_loud_on_auth_error(monkeypatch):
    monkeypatch.setattr(share.quark_client, "http",
                        lambda *a, **k: (401, {"status": 401, "message": "登录已失效"}))
    monkeypatch.setattr(share.quark_client, "get_stoken", lambda *a, **k: "ST")
    with pytest.raises(RuntimeError) as ei:
        share._default_listdir("fidX")
    msg = str(ei.value)
    assert "401" in msg and "登录已失效" in msg  # cookie 失效必须显式上抛（设计 §8）


def test_walk_dir_uses_default_transport_and_prefix(monkeypatch):
    tree = {
        "d_min": [("2026", "d_y")],
        "d_y": [("09", "d_m")],
        "d_m": [("20260916.zip", ("f3", 15))],
    }
    seen = []

    def fake_http(url, body=None, retry=3, timeout=60):
        fid = url.split("pdir_fid=")[1].split("&")[0]
        seen.append(fid)
        lst = []
        for name, fid2 in tree[fid]:
            if isinstance(fid2, tuple):
                lst.append(_item(name, fid2[0], fid2[1]))
            else:
                lst.append(_item(name, fid2, is_dir=True))
        return 200, {"status": 200, "data": {"list": lst}}

    monkeypatch.setattr(share.quark_client, "http", fake_http)
    monkeypatch.setattr(share.quark_client, "get_stoken", lambda *a, **k: "ST")

    entries = share.walk_dir("d_min", prefix="minutes")

    assert [e.rel_path for e in entries] == ["minutes/2026/09/20260916.zip"]
    assert seen == ["d_min", "d_y", "d_m"]  # 真递归、真调传输（存根必败）
