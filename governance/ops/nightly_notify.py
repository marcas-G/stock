#!/usr/bin/env python3
"""nightly_notify.py —— 夜间深检失败通知（规格 §5）。

按 marker `<!-- nightly:YYYY-MM-DD -->` 幂等：查 open+closed issue（state=all），
已存在 → 追加评论；不存在 → 创建 issue（title `[nightly] <profile> verify 失败 <date>`，
labels `kind:process`,`status:open`；正文含失败步骤 + 日志尾 30 行 + 日志路径 +
复现命令 `make verify-deep`）。

另供 nightly-verify.sh 调用的 `--last-json`：只写 `<LOG_DIR>/last.json`
（rc/timestamp/log_path/profile/failed_steps），不发任何网络请求。

凭据：`GH_TOKEN`/`GITHUB_TOKEN` 环境变量，或 `~/.config/factorlab/github_token`（0600），
绝不回显。API 基址：`--api-base` 或 `GITHUB_API_BASE`（默认 https://api.github.com）。

用法：
  nightly_notify.py --rc 1 --log <path> [--profile deep] [--date YYYY-MM-DD]
  nightly_notify.py --last-json <path> --rc 0 --log <path> --profile fast
  nightly_notify.py --self-test        # 无网络自检
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

OPS_DIR = Path(__file__).resolve().parent
DEFAULT_REPO = "marcas-G/stock"
DEFAULT_TOKEN_FILE = "~/.config/factorlab/github_token"
DEFAULT_API_BASE = "https://api.github.com"
CST = timezone(timedelta(hours=8))
LOG_TAIL_LINES = 30
FAILED_STEPS_LIMIT = 20

VERIFY_STEP_RE = re.compile(r"\[verify\]\s+step=.*?\brc=([1-9][0-9]*)\b")
FALLBACK_FAILED_RE = re.compile(r"^(?:FAILED|ERROR)\b")


def today_cst() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d")


def now_cst() -> str:
    return datetime.now(CST).replace(microsecond=0).isoformat()


def read_lines(log_path: str | os.PathLike) -> list[str]:
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()


def extract_failed_steps(log_path: str | os.PathLike,
                         limit: int = FAILED_STEPS_LIMIT) -> list[str]:
    lines = read_lines(log_path)
    steps = [line for line in lines if VERIFY_STEP_RE.search(line)]
    if steps:
        return steps[-limit:]
    return [line for line in lines if FALLBACK_FAILED_RE.match(line)][-limit:]


def tail_lines(log_path: str | os.PathLike, n: int = LOG_TAIL_LINES) -> list[str]:
    return read_lines(log_path)[-n:]


def marker_for(date: str) -> str:
    return f"<!-- nightly:{date} -->"


def issue_title(profile: str, date: str) -> str:
    return f"[nightly] {profile} verify 失败 {date}"


def build_body(date: str, profile: str, rc: int, log_path: str, *,
               repeated: bool = False) -> str:
    failed = extract_failed_steps(log_path)
    tail = tail_lines(log_path)
    head = "再次失败" if repeated else "失败"
    out = [
        marker_for(date),
        "",
        f"## nightly {profile} verify {head}（{date}）",
        "",
        f"- 退出码：`{rc}`",
        f"- 日志路径：`{log_path}`",
        f"- 复现：`make verify-deep`"
        f"（等价 `bash governance/ops/verify.sh --profile {profile}`）",
        "",
        "### 失败步骤",
        "",
    ]
    if failed:
        out += [f"- `{line.strip()}`" for line in failed]
    else:
        out.append("（日志中无 `[verify] step=... rc=≠0` 行）")
    out += [
        "",
        "<details><summary>日志尾 30 行</summary>",
        "",
        "```text",
        *tail,
        "```",
        "",
        "</details>",
        "",
    ]
    return "\n".join(out)


def read_token(token_file: str) -> str:
    for env in ("GH_TOKEN", "GITHUB_TOKEN"):
        tok = os.environ.get(env, "").strip()
        if tok:
            return tok
    path = Path(token_file).expanduser()
    if path.is_file():
        tok = path.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    raise SystemExit(
        f"缺少 token：设置 GH_TOKEN/GITHUB_TOKEN 或写入 {path}（0600）"
    )


def api(base: str, repo: str, token: str, method: str, path: str,
        payload: dict | None = None):
    url = f"{base.rstrip('/')}/repos/{repo}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "factorlab-nightly-notify",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise SystemExit(
            f"GitHub API {method} {url} 失败：HTTP {exc.code} {detail}"
        ) from exc


def find_issue_by_marker(base: str, repo: str, token: str,
                         marker: str) -> dict | None:
    page = 1
    while page <= 10:
        issues = api(
            base, repo, token, "GET",
            f"/issues?state=all&per_page=100&page={page}"
            "&sort=created&direction=desc",
        )
        for issue in issues:
            if "pull_request" in issue:
                continue
            if marker in (issue.get("body") or ""):
                return issue
        if len(issues) < 100:
            return None
        page += 1
    return None


def write_last_json(path: str, rc: int, log: str, profile: str) -> None:
    payload = {
        "rc": rc,
        "timestamp": now_cst(),
        "log_path": log,
        "profile": profile,
        "failed_steps": extract_failed_steps(log),
    }
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def self_test() -> int:
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "sample.log"
        log.write_text(
            "\n".join(
                [f"noise-{i:02d}" for i in range(1, 36)]
                + ["[verify] step=platform-pytest rc=1", "[verify] step=gates rc=0"]
            ) + "\n",
            encoding="utf-8",
        )
        failed = extract_failed_steps(log)
        assert failed == ["[verify] step=platform-pytest rc=1"], failed
        assert len(tail_lines(log)) == 30, "日志尾必须截 30 行"
        body = build_body("2026-09-21", "deep", 1, str(log))
        assert marker_for("2026-09-21") in body
        assert "make verify-deep" in body
        assert "noise-35" in body and "noise-01" not in body
        assert issue_title("deep", "2026-09-21") == \
            "[nightly] deep verify 失败 2026-09-21"
        fallback = Path(td) / "fallback.log"
        fallback.write_text("FAILED tests/x.py::t\nERROR tests/y.py::u\n",
                            encoding="utf-8")
        assert extract_failed_steps(fallback) == [
            "FAILED tests/x.py::t", "ERROR tests/y.py::u"
        ]
    print("[self-test] ok")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="nightly verify 失败通知（幂等 issue）")
    parser.add_argument("--date", default=today_cst(), help="marker 日期（默认北京今天）")
    parser.add_argument("--profile", default="deep", help="verify 档位（默认 deep）")
    parser.add_argument("--rc", type=int, default=1, help="verify 退出码")
    parser.add_argument("--log", default=None, help="verify 日志路径")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--api-base",
                        default=os.environ.get("GITHUB_API_BASE", DEFAULT_API_BASE))
    parser.add_argument("--token-file",
                        default=os.environ.get("GITHUB_TOKEN_FILE", DEFAULT_TOKEN_FILE))
    parser.add_argument("--last-json", default=None,
                        help="仅写 last.json（rc/timestamp/log_path/profile/failed_steps）")
    parser.add_argument("--dry-run", action="store_true", help="只打印将提交的正文")
    parser.add_argument("--self-test", action="store_true", help="无网络自检")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        return self_test()
    if args.last_json:
        if not args.log:
            raise SystemExit("--last-json 需要 --log")
        write_last_json(args.last_json, args.rc, args.log, args.profile)
        return 0
    if not args.log:
        raise SystemExit("通知模式需要 --log")

    body = build_body(args.date, args.profile, args.rc, args.log)
    title = issue_title(args.profile, args.date)
    if args.dry_run:
        print(f"# {title}\n")
        print(body)
        return 0

    token = read_token(args.token_file)
    marker = marker_for(args.date)
    existing = find_issue_by_marker(args.api_base, args.repo, token, marker)
    if existing:
        number = existing["number"]
        comment = build_body(args.date, args.profile, args.rc, args.log,
                             repeated=True)
        result = api(args.api_base, args.repo, token, "POST",
                     f"/issues/{number}/comments", {"body": comment})
        print(f"已存在 issue #{number}，追加评论：{result.get('html_url', '')}")
        return 0
    created = api(args.api_base, args.repo, token, "POST", "/issues", {
        "title": title,
        "body": body,
        "labels": ["kind:process", "status:open"],
    })
    print(f"已创建 issue：{created.get('html_url', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
