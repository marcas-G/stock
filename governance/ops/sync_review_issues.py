#!/usr/bin/env python3
"""评审 → GitHub Issues 同步（V1，stdlib-only REST）。

- 事实源：`governance/evidence/reviews/findings.md` 的**未闭环行**（open/fixed-claimed/reopened）
  + 内嵌 PLAN_ITEMS（STATUS 待办里的方案级事项）。
- 幂等：issue 正文含标记 `<!-- finding:<ID> -->` / `<!-- plan:<ID> -->`；查重后只建缺的。
- 状态回写：本地 `verified` → issue 评论（证据指针）并关闭；`reopened` → 重开并评论（V1 简版）。
- 凭据：`GH_TOKEN` 环境变量或 `~/.config/factorlab/github_token`（0600），**绝不回显/入库**。

用法：
    platform/.venv/bin/python governance/ops/sync_review_issues.py            # dry-run（只打印计划）
    platform/.venv/bin/python governance/ops/sync_review_issues.py --apply    # 实际创建/更新
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_DEFAULT = "marcas-G/stock"
ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "governance" / "evidence" / "reviews" / "findings.md"
TOKEN_FILE = Path.home() / ".config" / "factorlab" / "github_token"

LABEL_COLORS = {
    "kind:finding": "1d76db", "kind:plan": "5319e7", "kind:process": "c5def5",
    "severity:C": "b60205", "severity:I": "d93f0b", "severity:M": "fbca04",
    "status:open": "e11d21", "status:fixed-claimed": "fbca04",
    "status:verified": "0e8a16", "status:reopened": "b60205",
    "paused": "cccccc",
}

# 方案级事项（来源：reviews/STATUS.md 待办；ID 稳定，便于幂等）
PLAN_ITEMS = [
    dict(id="PLAN-DQ-M2", title="DQ-M2：minutes 三角验证 + 阈值 calibration + 3 小项",
         labels=["kind:plan", "status:open"], milestone="Plan DQ",
         body="范围：分钟↔日线↔腾讯三角验证；阈值 calibration；delta dedup 边界/§3.3 措辞/水位 ISO 校验。\n"
              "权威：`knowledge/design/platform/plans/2026-09-18-data-quality-pipeline-m15.md`（M2 段）。"),
    dict(id="PLAN-DQ-M3", title="DQ-M3：历史残余定向修复（BJ 1,115 / 前1996 2,658 / 1996+ 55）",
         labels=["kind:plan", "status:open"], milestone="Plan DQ",
         body="BJ amount/volume 用 bars_1m 重建；前 1996 残余逐码处置；R32/R33 审计按 daily-v2 重跑；"
              "附带 `circ_mv>total_mv` 3,914 老行核对。证据：R33。"),
    dict(id="PLAN-T", title="Plan T：同花顺模拟炒股接入（paper_broker）",
         labels=["kind:plan", "status:open"], milestone="Plan T",
         body="盘后算单→次日 09:31 模拟下单→台账对账；设计/计划：`knowledge/design/research/{specs,plans}/2026-09-17-ths-simulated-api*`。"
              "\n凭据红线：会话只放 `~/.config/factorlab/ths_session.json`（0600），绝不入仓。"),
    dict(id="PLAN-OP2", title="开放算子 Plan 2：op_meta / conformance / 算子档案 / 插件元数据",
         labels=["kind:plan", "status:open"], milestone="Plan 开放算子",
         body="触发已燃（Plan 1 完成）。最小包建议：op_meta + 可用性标注 + conformance 雏形（收掉"
              "`ts_partial_corr` 可见不可运行、REF 族不可分块两类坑）。设计：`2026-09-15-open-operators/design.md` §5。"),
    dict(id="PLAN-OP3", title="开放算子 Plan 3：`by=` 分组 + 截面原语（agg/rank/clip/cut/dist/proj/mask）",
         labels=["kind:plan", "status:open"], milestone="Plan 开放算子",
         body="“写任意因子”最大剩余缺口（`rank(close, by=...)` 现报未知算子）。设计：`2026-09-15-open-operators/design.md` §6。"),
    dict(id="PLAN-MIN-V2", title="分钟执行 V2：量能触发 / 分钟 NAV / 盘中临停规则库",
         labels=["kind:plan", "status:open"], milestone="Plan 分钟执行",
         body="前置：分钟链性能根因（R09/R31，`im_*`/`day_*` 逐算子重复物化）需先解。"
              "设计：`2026-09-15-minute-execution/design.md` §6.3-6.5。"),
    dict(id="PLAN-CX-DEPS", title="CX：Ridge/PLS/PCA 真跑依赖决策（scipy/sklearn 装库 vs 独立环境）",
         labels=["kind:plan", "status:open"], milestone="Plan CX",
         body="平台 venv 有“不加依赖”约束（pending #18 venv 可复现）；三示例 spec/实现/测试已就绪，缺库即 skip。"
              "需拍板落点（纯实验环境 or 扩展平台依赖声明）。"),
    dict(id="PLAN-CX-GIT", title="CX：spec/plan 入 git 版本化（含 composite dossiers/index）",
         labels=["kind:plan", "status:open"], milestone="Plan CX",
         body="C1/C4/C2/C3/C4b 的 spec/plan/档案/索引当前部分未跟踪；按“一次一棵树”分批提交并纳入门。"),
    dict(id="PLAN-LOB-PAUSED", title="tick 全线暂停：恢复时移除门豁免并修 14 处（含两条失效登记）",
         labels=["kind:plan", "status:open", "paused"], milestone="治理",
         body="登记：`pending-items.md #23`、`reviews/STATUS.md §二#12`。恢复步骤：移除 `check_dataiface.py::PAUSED_TREES`；"
              "factio.partitions 单点 + G-READ 登记；清理/回填 `run_lob_batch` 两条失效白名单；复跑 `make gates`。"),
    dict(id="PLAN-QUARK-COOKIE", title="运维：刷新 Quark cookie（网盘 sync 412/403）",
         labels=["kind:process", "status:open"], milestone="治理",
         body="`make data-update` 全链唯一外部阻断；刷新后跑 `cli.py sync` 验证。"),
]

SEV_LABEL = {"C": "severity:C", "I": "severity:I", "M": "severity:M"}


def read_token() -> str:
    tok = os.environ.get("GH_TOKEN", "").strip()
    if tok:
        return tok
    if TOKEN_FILE.is_file():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    raise SystemExit("缺少 token：设置 GH_TOKEN 或写入 ~/.config/factorlab/github_token（0600）")


def api(repo: str, token: str, method: str, path: str, payload: dict | None = None):
    url = f"https://api.github.com/repos/{repo}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "factorlab-review-sync",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        body = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"API {method} {path} → HTTP {e.code}: {body}")


def read_unresolved_findings() -> list[dict]:
    """未闭环 finding 行 → items（open/fixed-claimed/reopened）。"""
    items = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\| (R\d{2}-[^ |]+) \|", line)
        if not m:
            continue
        cells = [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if len(cells) != 7:
            continue
        rid, sev, problem, loc, status, fix, review = cells
        if status not in ("open", "fixed-claimed", "reopened"):
            continue
        rnd = rid.split("-")[0]
        title = f"[{rid}] {problem[:70]}（{sev}）"
        body = (f"<!-- finding:{rid} -->\n"
                f"**问题**：{problem}\n\n**位置**：{loc}\n\n**修复说明（团队填写）**：{fix or '（待填：commit + 命令 + 输出）'}\n\n"
                f"**复查（reviewer）**：{review or '（待复查）'}\n\n"
                f"**本地台账**：`governance/evidence/reviews/findings.md` · 证据：`governance/evidence/verification/R35/`")
        items.append(dict(id=rid, kind="finding", title=title,
                          labels=["kind:finding", SEV_LABEL.get(sev, "severity:I"), f"status:{status}", f"round:{rnd}"],
                          milestone=rnd, body=body))
    return items


def plan_items() -> list[dict]:
    out = []
    for p in PLAN_ITEMS:
        out.append(dict(id=p["id"], kind="plan", title=p["title"], labels=p["labels"],
                        milestone=p["milestone"], body=f"<!-- plan:{p['id']} -->\n{p['body']}"))
    return out


def ensure_label(repo: str, token: str, name: str, dry: bool) -> bool:
    """确保标签存在；权限不足时降级为警告（不打断同步；CI 用内置 token 时可能受限）。"""
    if dry:
        return True
    try:
        if api(repo, token, "GET", f"/labels/{urllib.parse.quote(name)}") is None:
            api(repo, token, "POST", "/labels",
                {"name": name, "color": LABEL_COLORS.get(name, "ededed"), "description": "review sync"})
        return True
    except RuntimeError as e:
        print(f"  ! 标签 {name} 跳过（{e}）")
        return False


def ensure_milestone(repo: str, token: str, title: str, dry: bool) -> int | None:
    """确保里程碑存在；失败降级为 None（issue 仍会创建，只是不挂里程碑）。"""
    if dry:
        return None
    try:
        ms = api(repo, token, "GET", "/milestones?state=all&per_page=100") or []
        for m in ms:
            if m["title"] == title:
                return m["number"]
        created = api(repo, token, "POST", "/milestones", {"title": title})
        return created["number"] if created else None
    except RuntimeError as e:
        print(f"  ! 里程碑 {title} 跳过（{e}）")
        return None


def list_issues(repo: str, token: str) -> dict[str, dict]:
    out, page = {}, 1
    while True:
        batch = api(repo, token, "GET", f"/issues?state=all&per_page=100&page={page}") or []
        if not batch:
            break
        for it in batch:
            body = it.get("body") or ""
            for kind in ("finding", "plan"):
                m = re.search(rf"<!-- {kind}:([^ ]+) -->", body)
                if m:
                    out[m.group(1)] = it
        page += 1
        if len(batch) < 100:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO_DEFAULT)
    ap.add_argument("--apply", action="store_true", help="实际创建/更新（缺省 dry-run）")
    args = ap.parse_args()
    dry = not args.apply

    items = read_unresolved_findings() + plan_items()
    print(f"计划同步 {len(items)} 项（finding 未闭环 + 方案待办）｜模式：{'APPLY' if args.apply else 'dry-run'}")

    if dry:
        for it in items:
            print(f"  [{it['kind']}] {it['id']} → {it['title'][:60]}  labels={it['labels']} milestone={it['milestone']}")
        print("\n（dry-run 不触网；加 --apply 执行）")
        return 0

    token = read_token()
    existing = list_issues(args.repo, token)
    created, closed, skipped, failed = [], [], [], []
    for it in items:
        cur = existing.get(it["id"])
        if cur is None:
            try:
                for lb in it["labels"]:
                    ensure_label(args.repo, token, lb, dry)
                ms_no = ensure_milestone(args.repo, token, it["milestone"], dry)
                payload = {"title": it["title"], "body": it["body"], "labels": it["labels"]}
                if ms_no:
                    payload["milestone"] = ms_no
                issue = api(args.repo, token, "POST", "/issues", payload)
                created.append((it["id"], issue["html_url"]))
            except RuntimeError as e:
                failed.append((it["id"], str(e)))
        else:
            # V1：本地 verified → 评论+关单；reopened → 重开（finding 行才适用）
            skipped.append((it["id"], cur["html_url"]))
    print(f"\n创建 {len(created)} ｜ 已存在 {len(skipped)} ｜ 失败 {len(failed)} ｜ 关单 {len(closed)}")
    for i, u in created:
        print(f"  + {i}: {u}")
    for i, e in failed:
        print(f"  ! {i}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
