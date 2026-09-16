# 夸克网盘数据自动更新链 实施计划（Plan P）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以夸克网盘分享链接为唯一外部数据源，自动完成 日K/分钟/日线资金/财报 四类数据的 下载 → 转换 → 灌入 CH → 对账，并清理其余外部源。

**Architecture:** 新工具 `platform/tools/pan_update/`（状态驱动差集 + 阶段编排，复用 `quark_client` 与既有生产者）；单入口 `make data-update` + systemd user timer（crontab 回退）。

**Tech Stack:** Python 3.13（平台 venv）、polars/pyarrow/openpyxl、ClickHouse、bash/Makefile、systemd user。

**Spec:** `knowledge/design/workspace/2026-09-16-pan-data-update-design.md`（§2.1 直链大小上限 / §6 清理 / §7 验收）

## Global Constraints

- **TDD**：先失败测试后实现；测试不得触网（fake `quark_client` 传输层）；真实验收单独标注。
- **一次提交一棵树**；证据落 `governance/evidence/verification/R30/`。
- **重任务护栏**：`FACTORLAB_MAX_MEMORY=8GB`；CH 写入仅经既有幂等脚本。
- **data/ 纪律**：raw 只增不删（除 `--prune` 显式）；state 文件在 `data/raw/pan_state.json`（gitignored）。
- **分享直链大小上限**：取链 HTTP 400 `download file size limit` = manual_required（清单+告警，不 fail 整链）。
- **不猜 schema**：资金流/财报列名以下方实测样本为准（2026-09-16 采样，样本已存 `/tmp/opencode/pansample/`，实施时复制小样本进 `tests/fixtures/`）。
- **冻结不动**：`governance/evidence/**` 历史正文、`_archive/**`、`data/`（清理任务除外，删前留清单）。

---

## 实测样本（计划内固定引用）

**资金流 `20260916.zip`**：内层 `20260916/zj.xls`（GBK、TSV、24 列；5573 行）
列：`序,代码,名称,最新,涨幅%,主力净流入,集合竞价,超大单流入,超大单流出,超大单净额,超大单净占比%,大单流入,大单流出,大单净额,大单净占比%,中单流入,中单流出,中单净额,中单净占比%,小单流入,小单流出,小单净额,小单净占比%,空`。
样例：`= "002281"`（代码带 Excel 公式壳）、` 20.3亿`、` 3922万`、` 27.01`、缺失为 ` - `。
另有 `hq.xls`（行情，不用）、`gn_detail.csv`（概念成分，不用）、`gnzj/hyzj.xls`（概念/行业资金，暂不用）。

**财报 `2026-09-04更新简化个股基本面数据.xlsx`**：sheet1、51 列；关键列：
`code, 更新日期, 总股本, 流通A股, 每股收益, 总资产, 流动资产, 固定资产, 无形资产, 股东人数, 流动负债, 长期负债, 资本公积金, 净资产, 营业收入, 营业成本, 应收账款, 营业利润, 投资收益, 经营现金流, 总现金流, 存货, 利润总额, 税后利润, 净利润, 未分配利润, 地区, 行业_, 报告期, 上市日期, 申万代码, 细分代码, 行业代码, 产业代码, 产业, 行业, 细分行业, 通达信代码, 申万细分代码, 申万行业代码, 申万产业代码, 申万产业, 申万行业, 申万细分, 市场, 名称, 拼音`。
样例：`('000001','20260815',1940591.87,...,'19910403',...,'金融','银行','银行',...,'sz','平安银行','PAY')`（总股本/流通A股单位为万股；金额列为元）。

---

### Task 1: 骨架 `config.py` + `state.py`

**Files:**
- Create: `platform/tools/pan_update/__init__.py`、`config.py`、`state.py`
- Test: `platform/tools/pan_update/tests/test_state.py`

**Interfaces:**
- Produces:
  - `config.Category(key, share_dir, local_root, kind)`；`config.CATEGORIES: dict[str, Category]`；
    `config.PWD_ID/PASSCODE/SHARE_ROOT_DIR/COOKIE_PATH`；`config.repo_root()`（`parents[3]`=仓库根）
  - `state.load_state(path) -> dict`（损坏 → 隔离 `*.corrupt-<ts>` 并回空骨架）
  - `state.save_state_atomic(path, state)`（tmp+fsync+os.replace）
  - `state.diff_files(state, category, entries) -> Diff(to_fetch, changed, skipped)`；entries: `list[dict(name,size,rel_path)]`
  - **T1 执行裁决（2026-09-16）**：状态键用 `_state_key(category, rel_path)` 归一化（rel_path 已含类别前缀则原样，避免双前缀）；`to_fetch = 新文件 + 变更文件`（新在前）。下方实现节选里 `f"{category}/{rel_path}"` 的直接拼接以测试语义为准作废。

- [ ] **Step 1: 失败测试**

```python
# tests/test_state.py
import json
from pathlib import Path
from pan_update import state

def test_load_state_corrupt_quarantined(tmp_path):
    p = tmp_path / "pan_state.json"
    p.write_text("{not json", encoding="utf-8")
    s = state.load_state(p)
    assert s["version"] == 1 and s["files"] == {}
    assert list(tmp_path.glob("pan_state.json.corrupt-*"))

def test_save_state_atomic_roundtrip(tmp_path):
    p = tmp_path / "pan_state.json"
    st = {"version": 1, "files": {"a": {"size": 1}}, "stages": {}, "runs": []}
    state.save_state_atomic(p, st)
    assert json.loads(p.read_text())["files"]["a"]["size"] == 1
    assert not list(tmp_path.glob("*.tmp"))

def test_diff_new_changed_skipped():
    st = {"version": 1, "files": {"daily/x.zip": {"size": 10}}, "stages": {}, "runs": []}
    entries = [
        {"name": "x.zip", "size": 10, "rel_path": "daily/x.zip"},   # skipped
        {"name": "x.zip", "size": 11, "rel_path": "daily/x.zip"},   # changed
        {"name": "y.zip", "size": 5, "rel_path": "daily/y.zip"},    # new
    ]
    d = state.diff_files(st, "daily", entries)
    assert [e["rel_path"] for e in d.to_fetch] == ["daily/y.zip", "daily/x.zip"]
    assert [e["rel_path"] for e in d.changed] == ["daily/x.zip"]
    assert [e["rel_path"] for e in d.skipped] == ["daily/x.zip"]
```

- [ ] **Step 2: 跑测试确认失败**：`cd platform && .venv/bin/python -m pytest tools/pan_update/tests/test_state.py -q`（期望 ImportError）

- [ ] **Step 3: 实现**

```python
# config.py（节选）
from dataclasses import dataclass
from pathlib import Path

PWD_ID = "1ae1c55c0a03"
PASSCODE = "QNhy"
SHARE_ROOT_DIR = "level2_detail"

def repo_root() -> Path:  # platform/tools/pan_update/config.py → stock/
    return Path(__file__).resolve().parents[3]

COOKIE_PATH = repo_root() / "quark_cookies.txt"

@dataclass(frozen=True)
class Category:
    key: str
    share_dir: str
    local_root: str
    kind: str  # "files" | "zip_days" | "month_zips" | "financials"

CATEGORIES: dict[str, Category] = {
    "daily": Category("daily", "日K线数据---复权因子-经典技术指标--bs点缠论划线", "data/raw/daily", "files"),
    "minutes": Category("minutes", "A股分钟线", "data/raw/minutes", "zip_days"),
    "fund_flow": Category("fund_flow", "日线资金--每日沪深京个股日线数据和资金流数据", "data/raw/fund_flow", "month_zips"),
    "financials": Category("financials", "财报报表---有史以来--每周更新", "data/raw/financial", "financials"),
}
```

```python
# state.py（节选）
import json, os, time
from pathlib import Path

def _empty() -> dict:
    return {"version": 1, "files": {}, "stages": {}, "runs": []}

def load_state(path: Path) -> dict:
    if not path.exists():
        return _empty()
    try:
        s = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(s, dict) or "files" not in s:
            raise ValueError("非状态骨架")
        return s
    except (json.JSONDecodeError, ValueError):
        path.replace(path.with_name(f"{path.name}.corrupt-{int(time.time())}"))
        return _empty()

def save_state_atomic(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)
        fh.flush(); os.fsync(fh.fileno())
    os.replace(tmp, path)
```

```python
# state.py diff（节选）
from dataclasses import dataclass

@dataclass
class Diff:
    to_fetch: list[dict]; changed: list[dict]; skipped: list[dict]

def diff_files(state: dict, category: str, entries: list[dict]) -> Diff:
    known = {k: v for k, v in state["files"].items() if k.startswith(f"{category}/")}
    to_fetch, changed, skipped = [], [], []
    for e in entries:
        key = f"{category}/{e['rel_path']}"
        prev = known.get(key)
        if prev is None:
            to_fetch.append(e)
        elif prev.get("size") != e["size"]:
            changed.append(e); to_fetch.append(e)
        else:
            skipped.append(e)
    return Diff(to_fetch=to_fetch, changed=changed, skipped=skipped)
```

- [ ] **Step 4: 跑测试确认通过** + `pytest tools/pan_update/tests -q`
- [ ] **Step 5: 提交** `feat(tools): pan_update 骨架（config/state 差集与原子状态）`

---

### Task 2: 分享树遍历 `share.py` + 类别目录发现

**Files:**
- Create: `platform/tools/pan_update/share.py`
- Test: `platform/tools/pan_update/tests/test_share.py`

**Interfaces:**
- Consumes: `quark_client`（`get_stoken`、`http`、`HOST_PC`、`PWD_ID`）
- Produces:
  - `share.walk_dir(fid, prefix="") -> list[Entry]`（Entry: `name,size,fid,fid_token,rel_path,is_dir`）
  - `share.find_dir(root_fid, name) -> str`（fid；不存在 → KeyError）
  - `share.iter_category(listdir, start_fid) -> list[Entry]`（Entry dataclass；递归遍历；跳过目录项；`listdir` 可注入；**T1 裁决：T3 在消费边界 `dataclasses.asdict` 转换**）

- [ ] **Step 1: 失败测试（fake transport）**

```python
# tests/test_share.py
from pan_update import share

FAKE_TREE = {
    "root": [("日K线数据---复权因子-经典技术指标--bs点缠论划线", "d_daily"), ("A股分钟线", "d_min")],
    "d_daily": [("x.zip", "f1", 10), ("退市股", "d_tdx")],
    "d_tdx": [("000018_神州长城.xlsx", "f2", 3)],
    "d_min": [("2026", "d_y")],
    "d_y": [("09", "d_m")],
    "d_m": [("20260916.zip", "f3", 15)],
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
```

> 注：`iter_category` 需接受注入的 `listdir` 以离线测试；生产默认用 `quark_client` 实现（`_default_listdir`）。

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**（`Entry` dataclass、递归 DFS、分页 `_size=100`、stoken 用 `quark_client.get_stoken`、`_default_listdir` 走 `share/sharepage/detail`；目录项不入结果；rel_path 相对类别根）
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: 提交** `feat(tools): pan_update 分享树遍历与类别发现`

---

### Task 3: 下载执行 `sync.py`（含 size-limit → manual_required）

**Files:**
- Create: `platform/tools/pan_update/sync.py`
- Test: `platform/tools/pan_update/tests/test_sync.py`

**Interfaces:**
- Consumes: `state.diff_files`、`share.iter_category`、`quark_client.get_download_urls/download_file`
- Produces:
  - `sync.SyncReport(downloaded, unchanged, manual, failed)`（`manual/failed`: `list[dict(name, reason)]`）
  - `sync.sync_category(state, category, *, entries, transport, dest_root, dry_run=False) -> SyncReport`
  - transport 协议：`list_urls(items) -> dict[fid,url]`（抛 `SizeLimitExceeded(name)` 或按项返回 None+原因）、
    `download(url, out, size) -> bool`

- [ ] **Step 1: 失败测试**

```python
# tests/test_sync.py
from pan_update import sync, state as st

def _entries():
    return [
        {"name": "a.zip", "size": 10, "rel_path": "a.zip", "fid": "1", "fid_token": "t"},
        {"name": "big.parquet", "size": 367_000_000, "rel_path": "big.parquet", "fid": "2", "fid_token": "t"},
    ]

class T:
    def __init__(self): self.dl = []
    def list_urls(self, items):
        out = {}
        for i in items:
            if i["size"] > 100_000_000:
                i["_blocked_reason"] = "size limit"
            else:
                out[i["fid"]] = f"http://fake/{i['fid']}"
        return out
    def download(self, url, out, size):
        self.dl.append((url, str(out))); out.write_bytes(b"x" * size); return True

def test_sync_downloads_small_and_marks_big_manual(tmp_path):
    s = {"version": 1, "files": {}, "stages": {}, "runs": []}
    rep = sync.sync_category(s, "financials", entries=_entries(), transport=T(),
                             dest_root=tmp_path, dry_run=False)
    assert rep.downloaded == ["a.zip"]
    assert rep.manual == [{"name": "big.parquet", "reason": "size limit"}]
    assert (tmp_path / "a.zip").read_bytes() == b"x" * 10
    assert s["files"]["financials/a.zip"]["size"] == 10

def test_sync_dry_run_writes_nothing(tmp_path):
    s = {"version": 1, "files": {}, "stages": {}, "runs": []}
    t = T()
    rep = sync.sync_category(s, "daily", entries=_entries(), transport=t,
                             dest_root=tmp_path, dry_run=True)
    assert rep.downloaded == [] and rep.to_fetch == ["a.zip", "big.parquet"]
    assert t.dl == [] and not list(tmp_path.iterdir()) and s["files"] == {}
```

> 测试要求最终实现暴露 `rep.to_fetch`（dry-run 清单）。

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**：`diff_files` 结果 → 取链（`transport.list_urls`）→ 逐文件 `download` 到 `dest_root/<rel_path>.part`，size 校验通过后 `os.replace` → 写 state（成功后）；`manual` 收集 blocked；`failed` 收集下载失败；`dry_run` 只返回清单不落盘；目录自动 mkdir。
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: 提交** `feat(tools): pan_update 下载执行（差集/断点/size-limit manual 分类）`

---

### Task 4: 阶段编排 `stages.py` + 日志/flock

**Files:**
- Create: `platform/tools/pan_update/stages.py`
- Test: `platform/tools/pan_update/tests/test_stages.py`

**Interfaces:**
- Produces:
  - `stages.run_cmd(cmd: list[str], *, log, env=None) -> None`（失败抛 `StageError(stage, cmd, rc, tail)`）
  - `stages.STAGE_CHAINS: dict[str, list[list[str]]]`（类别 → 命令序列，见各 Task 填实）
  - `stages.run_category_stage(state, category, phase, *, runner=run_cmd)`
  - `stages.single_instance(lock_path)`（`fcntl.flock` 非阻塞，占用 → `RuntimeError`）

- [ ] **Step 1: 失败测试**

```python
# tests/test_stages.py
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
```

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**（含 `single_instance` flock、日志函数 `open_log(category, date)` 落 `runs/platform/logs/`）
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: 提交** `feat(tools): pan_update 阶段编排（续跑/失败上抛/单实例锁/日志）`

---

### Task 5: 日K 类别接线（全量前缀修正 + daily 阶段链）

**Files:**
- Modify: `platform/tools/ashare_ingest/import_daily.py`（`_build_tasks`：全量识别从 `FULL_SNAPSHOT_MARK='07月31日'` 改为 `19910101至` 前缀，保留旧标记兼容）
- Modify: `platform/tools/pan_update/stages.py`（`STAGE_CHAINS["daily"]` 填实）
- Test: `platform/tools/ashare_ingest/tests/test_import_daily_full_prefix.py`

**Interfaces:**
- Consumes: `import_daily`（不变签名）、`ch_ingest` 三脚本
- Produces：`STAGE_CHAINS["daily"] = [[<venv>, import_daily.py], [<venv>, ingest_daily.py], [<venv>, derive_stk_limit.py], [<venv>, adj_backfill.py]]`

- [ ] **Step 1: 失败测试**

```python
# tests/test_import_daily_full_prefix.py
import import_daily as idl

def test_full_snapshot_prefix_detected():
    class P:
        def __init__(self, n): self.name = n
    fulls = [P("19910101至20260831A股日k线.zip"), P("19910101至上月底07月31日A股日k线.zip")]
    incr = [P("2026-09-01至2026-09-16A股日k线.zip")]
    assert all(idl._is_full_snapshot(p) for p in fulls)
    assert not any(idl._is_full_snapshot(p) for p in incr)

def test_newest_full_selected():
    class P:
        def __init__(self, n): self.name = n
    ps = [P("19910101至20260731A股日k线.zip"), P("19910101至20260831A股日k线.zip")]
    assert idl._newest_full(ps).name == "19910101至20260831A股日k线.zip"
```

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**：新增 `_is_full_snapshot(path)`（前缀或旧标记）、`_newest_full(paths)`（解析 `至YYYYMMDD` 取最大）；`_build_tasks` 用它们替换现有 `FULL_SNAPSHOT_MARK` 判断（旧标记保留兼容）。
- [ ] **Step 4: 跑测试** + `pytest tools/ashare_ingest/tests -q`（既有全绿）
- [ ] **Step 5: 提交** `fix(tools): import_daily 全量快照识别支持网盘命名（19910101至前缀）` + `feat(tools): pan_update daily 阶段链`

---

### Task 6: 分钟类别接线（zip_days → convert → ingest）

**Files:**
- Modify: `platform/tools/pan_update/stages.py`：`STAGE_CHAINS["minutes"] = [[<venv>, convert_minutes_to_parquet.py], [<venv>, ingest_bars.py]]`
- Test: `platform/tools/pan_update/tests/test_minutes_wiring.py`

- [ ] **Step 1: 失败测试**：断言 `STAGE_CHAINS["minutes"]` 两条命令路径存在且含 `platform/tools/converters/convert_minutes_to_parquet.py` / `platform/tools/ch_ingest/ingest_bars.py`；断言 `Category.kind == "zip_days"` 的 rel_path 直接映射 `local_root/<年>/<月>/<文件名>.zip`（`share.iter_category` 产出的 rel_path 与本地布局一致，用小树 fixture 验证）。
- [ ] **Step 2-4: 红 → 实现 → 绿**
- [ ] **Step 5: 提交** `feat(tools): pan_update minutes 阶段链`

---

### Task 7: 日线资金（解析器 + CH `moneyflow` + 读路径列映射）

**Files:**
- Create: `platform/tools/pan_update/parse_fund_flow.py`
- Create: `platform/tools/ch_ingest/ingest_moneyflow.py`（读 `data/raw/fund_flow/*.zip` 内 zj.xls → CH）
- Modify: `platform/tools/ch_ingest/ddl.sql`（新表 `moneyflow`）
- Modify: `platform/src/factorlab/adapters/read/source.py`（`_MONEYFLOW_MAP` + LEFT JOIN）
- Test: `platform/tools/pan_update/tests/test_fund_flow_parse.py`；`platform/tests/test_source_moneyflow.py`

**Interfaces:**
- Produces:
  - `parse_fund_flow.parse_amount(s: str) -> float|None`（`亿`=1e8/`万`=1e4/`-`→None）
  - `parse_fund_flow.parse_code(s: str) -> str`（去 `= "…"` 壳，zfill(6)）
  - `parse_fund_flow.parse_zj(text: str, trade_date: datetime.date) -> pl.DataFrame`
    （列：`ts_code`(加后缀映射同 `market_of`), `trade_date`, `main_net_inflow`, `auction`,
    `super_in/out/net`, `super_net_pct`, `big_in/out/net`, `big_net_pct`,
    `mid_in/out/net`, `mid_net_pct`, `small_in/out/net`, `small_net_pct`）
  - CH 表（Nullable(Float64) 金额列 + `ts_code String/trade_date Date`）
  - 读路径：`_MONEYFLOW_MAP = {"main_net_inflow": "main_net_inflow", ...}`，`load_daily` 请求这些列时 LEFT JOIN `moneyflow`

- [ ] **Step 1: 失败测试（单位/空值逐值）**

```python
# tests/test_fund_flow_parse.py
import datetime
from pan_update import parse_fund_flow as pf

def test_parse_amount_units_and_missing():
    assert pf.parse_amount(" 20.3亿") == 20.3e8
    assert pf.parse_amount(" 3922万") == 3922e4
    assert pf.parse_amount(" 27.01") == 27.01
    assert pf.parse_amount(" - ") is None

def test_parse_code_strips_formula():
    assert pf.parse_code('= "002281"') == "002281"
    assert pf.parse_code(" 600519") == "600519"

def test_parse_zj_row_values():
    hdr = ("序\t代码\t名称\t最新\t涨幅%\t主力净流入\t集合竞价\t超大单流入\t超大单流出\t超大单净额\t超大单净占比%"
           "\t大单流入\t大单流出\t大单净额\t大单净占比%\t中单流入\t中单流出\t中单净额\t中单净占比%"
           "\t小单流入\t小单流出\t小单净额\t小单净占比%\t\n")
    row = ('1\t= "002281"\t光迅科技\t185.21\t10.00\t20.3亿\t3922万\t30.7亿\t-10.7亿\t20.0亿\t27.01\t'
           '17.5亿\t-5亿\t12.5亿\t15.0\t-3亿\t2亿\t-1亿\t-8.0\t5亿\t-4亿\t1亿\t3.3\t\n')
    df = pf.parse_zj(hdr + row, datetime.date(2026, 9, 16))
    assert df.height == 1
    r = df.row(0, named=True)
    assert r["ts_code"] == "002281.SZ" and r["trade_date"] == datetime.date(2026, 9, 16)
    assert r["main_net_inflow"] == 20.3e8 and r["super_out"] == -10.7e8
    assert r["small_net_pct"] == 3.3
```

- [ ] **Step 2-4: 红 → 实现 → 绿**（解析器 + DDL + ingest + source.py JOIN；`test_source_moneyflow.py` 用 dualbridge seed `moneyflow` 断言 `load_daily(cols=["main_net_inflow"])` 取到值、缺行→null）
- [ ] **Step 5: 提交** `feat(tools): 资金流解析与 CH moneyflow（含平台读路径列映射）`

---

### Task 8: 财报（xlsx 快照 → fact + CH `fundamentals`）

**Files:**
- Create: `platform/tools/pan_update/parse_fundamentals_xlsx.py`
- Create: `platform/tools/ch_ingest/ingest_fundamentals.py`（fact → CH）
- Modify: `platform/tools/ch_ingest/ddl.sql`（新表 `fundamentals`）
- Test: `platform/tools/pan_update/tests/test_fundamentals_parse.py`

**Interfaces:**
- Produces：
  - `parse_fundamentals_xlsx.parse_xlsx(path) -> pl.DataFrame`
    （列：`ts_code, updated_date, report_period, list_date, market, industry, sw_industry, sw_sub,
    total_shares, float_a_shares, eps, total_assets, current_assets, fixed_assets, intangible_assets,
    shareholders, current_liab, long_liab, capital_reserve, net_assets, revenue, operating_cost,
    op_profit, invest_income, op_cashflow, total_cashflow, inventory, total_profit, net_profit,
    undist_profit`；单位沿用源：股本=万股、金额=元；日期 `YYYYMMDD`→Date）
  - fact：`data/fact/fundamentals/fundamentals_snapshot.parquet`（周快照，覆盖写；旧版留 1 份 `.prev`）
  - CH `fundamentals`：同列 schema（`updated_date` 为唯一日期键；主键 `(updated_date, ts_code)`）
- **限制声明**：这是**当期快照**（非历史 PIT 序列）；PIT 历史待多期快照累积或人工 `*_financial.parquet`（manual_required）。

- [ ] **Step 1: 失败测试**：用样本 xlsx 复制到 `tests/fixtures/fin_sample.xlsx`（实施时从 `/tmp/opencode/pansample/` 复制并按需裁剪前 200 行）；断言 `parse_xlsx` 行数/首行逐值（`ts_code=="000001.SZ"`、`updated_date==date(2026,8,15)`、`total_shares≈1940591.87`、`market=="sz"`、`industry=="银行"`、`net_profit==25696000.0`）。
- [ ] **Step 2-4: 红 → 实现 → 绿**（openpyxl read_only 迭代；列名映射表固定；`None`/`'None'`→null；`ts_code` 后缀由 `市场`/代码段推导，复用 `market_of` 逻辑）
- [ ] **Step 5: 提交** `feat(tools): 财报 xlsx 快照解析 + CH fundamentals`

---

### Task 9: CLI / Makefile / 定时器 / 手册

**Files:**
- Create: `platform/tools/pan_update/cli.py`、`README.md`
- Create: `governance/ops/install_pan_timer.sh`（user systemd unit；失败回退打印 crontab 行）
- Modify: `Makefile`（`data-update` 目标）
- Test: `platform/tools/pan_update/tests/test_cli.py`

**Interfaces:**
- Produces：`pan_update sync|build|publish|verify|all [--categories a,b] [--dry-run] [--prune]`；
  `make data-update`；`install_pan_timer.sh install|uninstall|status`
- cookie 检查：启动时 `quark_client.cookies()` 失败 → 明确报错（提示更新 `quark_cookies.txt`）exit 2

- [ ] **Step 1: 失败测试**：`--dry-run` 打印每类别差集清单且不下载（fake transport）；`--categories bogus` → exit 2；cookie 缺失（monkeypatch 路径）→ exit 2 且文案含 `quark_cookies.txt`；`sync` 成功但 `build` 失败 → 退出非零、state 阶段未标。
- [ ] **Step 2-4: 红 → 实现 → 绿**
- [ ] **Step 5: 文档**：README（目录映射/命令/定时/cookie 维护/manual_required 处理）；`Makefile`:
  ```make
  data-update:
  	FACTORLAB_MAX_MEMORY=8GB $(PLATFORM_PY) platform/tools/pan_update/cli.py all
  ```
  安装脚本（systemd user unit `pan-data-update.timer` 每日 08:10；`systemctl --user` 不可用 → 打印 crontab 行与手工说明）
- [ ] **Step 6: 提交** `feat(tools): pan_update CLI/Makefile/定时器与手册`

---

### Task 10: 真实端到端验收（R30 证据）

- [ ] **Step 1: dry-run 清单**：`make data-update --dry-run`（或直接 CLI）→ 应列出 9/15-16 增量日K、分钟 9 月日 zip、资金 9 月日 zip、财报 xlsx；大件（日K 全量/财务 parquet）进 manual_required
- [ ] **Step 2: 真跑**：`FACTORLAB_MAX_MEMORY=8GB ... cli.py sync --categories daily,minutes,fund_flow,financials`（分钟只取缺失天；财报取 xlsx）
- [ ] **Step 3: build/publish/verify**：daily 链跑通（引入 9/15-16 数据 → CH daily max 前移）；fund_flow/financials 解析入库；`reconcile` 全绿
- [ ] **Step 4: 幂等**：二次 `make data-update` → 无下载、阶段跳过、exit 0；`factorlab run` 最小 spec 读新数据可用
- [ ] **Step 5: 证据落盘** `governance/evidence/verification/R30/`（dry-run/真跑/对账/幂等/CH 查询截图文本）；提交 `test(tools): Plan P 真实端到端验收（R30）`

---

### Task 11: 清理其他外部源（代码/本地文件/文档）

**Files:**
- Delete: `platform/src/factorlab/adapters/{fetcher.py,mirror_db.py,rebuild.py,refresh.py}`；`platform/tools/ashare_ingest/import_index.py`
- Modify: `platform/src/factorlab/surfaces/cli/main.py`（删 `data rebuild|update|refresh|verify` 子命令与引用）、`config.py`（去 teajoin 项）、`app/bootstrap.py`/相关 imports
- Modify: tests（删除/改写对应测试；`data rebuild` 双后端测试辅助如需保留 → 迁 `tests/_doubles`）
- Modify: `knowledge/contracts/{interface.md,data-ops-playbook.md}`、`governance/workspace/{data-map.md,pending-items.md}`、skills（ch-pipeline/data）
- Delete（本地，先清单+核对）: `data/raw/daily/daily_pre.parquet`；`data/ref/000905.SH.parquet`（网盘指数目录存在 → 删；若核对无等价则冻结保留）；确认 jqdata golden 是否在网盘（全树清单核对：在则删，不在则冻结并标注）
- Test: `platform/tests/test_dataiface_clean.py`（断言 CLI 无 `data rebuild` 子命令；生产代码无 `teajoin|TanJoinClient|import_index` 引用）

- [ ] **Step 1: grep 清单**：`grep -rn "teajoin\|TanJoinClient\|mirror_db\|rebuild_all\|refresh\|import_index" platform/src platform/tests platform/tools --include=*.py` → 逐条归类（删/改/测试辅助迁移）
- [ ] **Step 2: 代码删除 + 引用清理 + tests 调整**（平台全量定向跑）
- [ ] **Step 3: 本地文件删除**（清单+sha256 存档；`du -sh data/` 前后）
- [ ] **Step 4: 文档/技能同步**（data-map 外部源列 → "夸克网盘（唯一）"；playbook 重写为网盘手册）
- [ ] **Step 5: 回归**：`make gates` 绿；平台全量 ≥ 基线；`make test-research`；提交按树分提

---

## Self-Review（对 spec 覆盖）

| Spec 要求 | 对应 Task |
|---|---|
| §2 四类映射/目标端（含 CH 新表） | T5/T6/T7/T8 |
| §2.1 直链大小上限/manual_required | T3（分类）+ T9（清单/告警）+ T10（验收） |
| §3 架构（config/state/share/sync/stages/verify/cli） | T1-T4/T9 |
| §4 增量规则（日K 前缀、分钟差集、资金月+日、财报最新版） | T5/T6/T7/T8 |
| §5 自动化（make/timer/cookie/日志/flock/护栏） | T4/T9 |
| §6 清理三块 | T11 |
| §7 测试与验收 | 各 Task 单测 + T10 + T11 Step5 |
| §8 风险（cookie/限流/半成品/失败续跑/清理误删/定时不可用） | T1/T3/T4/T9/T11 |

## 未覆盖（有意）

- Level2 接入统一入口（保留手工）；指数/期货类别；jqdata golden 重生成；fundamentals PIT 历史回溯（待多期快照累积/人工 parquet）。
