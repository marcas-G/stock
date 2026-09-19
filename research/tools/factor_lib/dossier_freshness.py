"""档案缺失时效门（R31 ci-bootstrap ③）：`test_index.py` 缺档的提交时效宽限。

背景：挖矿在途（spec yaml 已写、run 未跑完、档案未落盘）会让"yaml ↔ md 一一镜像"
门恒红，阻塞无关提交。本模块把"缺档案"拆成两种：
- **PENDING**：该 yaml 的最近 git 提交（或未提交/无历史）在 `GRACE_HOURS`（默认 72h）
  内——宽限放行，调用方打印警告；挖矿轮末（写档案 + `make index`）后自然转 OK；
- **STALE**：yaml 提交已超宽限仍缺档案（或 git 无法判定，如非仓库/浅克隆）
  ——门照旧失败，防止"缺档过期不管"。

判定完全基于**真实 git 提交时间**（`git log -1 --format=%ct -- <yaml>`）：
- yaml 未提交 / 无历史 → `commit_ts is None` → PENDING（新因子在途）；
- 档案已存在 → OK，**不触发 git 查询**（既有档案路径零变更、零开销）；
- 非 git 仓库 / 浅克隆（提交历史不在）→ RuntimeError（无法判定 → 门失败）。

CI 注意：必须 `fetch-depth: 0`（完整历史）——浅克隆下旧 yaml 的提交时间不可得，
本模块会显式拒绝判定而不是静默当"未提交"放行。
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

GRACE_HOURS = 72.0
OK = "OK"
PENDING = "PENDING"
STALE = "STALE"

#: 提交时间查询函数签名：(repo_root, rel_path) -> epoch 秒 | None（无历史）
CommitTsFn = Callable[[Path, str], "int | None"]
#: 进程内浅克隆判定 memo（key = repo_root 字符串）
_SHALLOW_MEMO: dict[str, bool] = {}


def _is_shallow(repo_root: Path | str, git: str) -> bool:
    key = str(repo_root)
    if key not in _SHALLOW_MEMO:
        r = subprocess.run(
            [git, "-C", key, "rev-parse", "--is-shallow-repository"],
            capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(
                f"git 不可用/非仓库，无法判定 {key} 的提交时效: {r.stderr.strip()}")
        _SHALLOW_MEMO[key] = r.stdout.strip() == "true"
    return _SHALLOW_MEMO[key]


def last_commit_ts(repo_root: Path | str, rel_path: str, *,
                   git: str = "git") -> int | None:
    """`rel_path` 最近一次 git 提交的 epoch 秒；未提交/无历史 → None。

    git 本身失败（非仓库等）→ RuntimeError；浅克隆一律拒绝判定（grafted 根会把
    文件"算作根提交的新增"，返回错误时间——见模块 docstring，CI 必须 fetch-depth: 0）。
    """
    if _is_shallow(repo_root, git):
        raise RuntimeError(
            f"浅克隆仓库无法判定 {rel_path} 的提交时效（CI 必须 fetch-depth: 0）")
    r = subprocess.run(
        [git, "-C", str(repo_root), "log", "-1", "--format=%ct", "--", rel_path],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(
            f"git log 失败，无法判定 {rel_path} 的提交时效: {r.stderr.strip()}")
    out = r.stdout.strip()
    return int(out) if out else None


@dataclass(frozen=True)
class MirrorVerdict:
    """单 spec 的镜像门判定结果。"""

    state: str
    spec_name: str
    md: str
    yaml: str
    commit_ts: int | None = None


def assess(*, doc_exists: bool, commit_ts: int | None, now: float,
           grace_hours: float = GRACE_HOURS) -> str:
    """纯判定：档案存在 → OK；缺档案按提交时效 → PENDING / STALE。"""
    if doc_exists:
        return OK
    if commit_ts is None:
        return PENDING
    if now - commit_ts < grace_hours * 3600.0:
        return PENDING
    return STALE


def assess_spec(spec: dict, *, root: Path | str, now: float | None = None,
                grace_hours: float = GRACE_HOURS,
                commit_ts_fn: CommitTsFn = last_commit_ts) -> MirrorVerdict:
    """单个 spec（`build_index.load_specs` 形状）的镜像门判定。

    spec 需含 `name` / `yaml` / `md`（相对 root 的 POSIX 路径）。
    档案存在时不调用 commit_ts_fn。
    """
    doc = Path(root) / spec["md"]
    if doc.is_file():
        return MirrorVerdict(OK, spec["name"], spec["md"], spec["yaml"])
    ts = commit_ts_fn(Path(root), spec["yaml"])
    state = assess(doc_exists=False, commit_ts=ts,
                   now=time.time() if now is None else now,
                   grace_hours=grace_hours)
    return MirrorVerdict(state, spec["name"], spec["md"], spec["yaml"], ts)


def require_mirror_docs(specs: list[dict], *, root: Path | str,
                        now: float | None = None,
                        grace_hours: float = GRACE_HOURS,
                        commit_ts_fn: CommitTsFn = last_commit_ts
                        ) -> list[MirrorVerdict]:
    """逐 spec 判定；STALE → AssertionError（门红），返回 PENDING 列表。

    PENDING 由调用方打印警告；git 无法判定（RuntimeError）原样上抛 = 门失败。
    """
    pending: list[MirrorVerdict] = []
    stale: list[MirrorVerdict] = []
    for s in specs:
        v = assess_spec(s, root=root, now=now, grace_hours=grace_hours,
                        commit_ts_fn=commit_ts_fn)
        if v.state == PENDING:
            pending.append(v)
        elif v.state == STALE:
            stale.append(v)
    if stale:
        detail = "；".join(
            f"{v.spec_name}: {v.yaml} 最近提交 {time.strftime('%Y-%m-%d %H:%M', time.localtime(v.commit_ts))}"
            for v in stale)
        raise AssertionError(
            f"缺档案超 {grace_hours:g}h 宽限（STALE）: {detail}")
    return pending
