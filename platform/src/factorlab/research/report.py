"""R31 Task 5：report 组门面（报告浏览/摘要/静态 URL/启动 Web）。

只装配不实现：
- list/show：`surfaces.cli.main.collect_result_rows` / `adapters.results_fs`（与
  `factor list/show`、Web 首页同一摘要单点）；
- serve：`surfaces.web.app.create_app`（现有只读 Web）→ `uvicorn.run`（测试注入
  替身断言，不长跑；`url` 命令只算路径不启服务）。

URL 口径：`http://<host>:<port>/factor/<name>`（Web 现有因子详情路由）。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from factorlab.config import settings
from factorlab.research import envelope, registry
from factorlab.research.factor import _jsonify

_PRETTY = registry.ParamSpec("pretty", kind="bool", help="缩进 JSON（人读）")
_JSON = registry.ParamSpec("json", kind="bool",
                           help="输出单个 JSON 信封（默认口径，恒开）")
_REPORT_HINT = ("先 `flab factor run <spec.yaml>` 产出带 summary 的报告；"
                "命令目录 `flab describe --json`")


def _safe_name(name: str) -> bool:
    """因子报告名 = results/<name> 单层目录（拒绝路径穿越，与 Web 同语义）。"""
    return bool(name) and name not in (".", "..") and not any(
        c in name for c in "/\\:")


def _summary_path(name: str) -> Path:
    from factorlab.adapters import results_fs
    return results_fs.summary_path(Path(settings.results_dir), name)


def _url_path(name: str) -> str:
    return f"/factor/{name}"


# ================================================================
# list / show
# ================================================================

def report_list(args: Any) -> envelope.Envelope:
    """报告列表（results_dir 下带 summary 的因子报告；新报告在前）。"""
    from factorlab.surfaces.cli.main import collect_result_rows
    rows = collect_result_rows(settings.results_dir)
    reports = [{k: v for k, v in row.items() if k != "_sort"} for row in rows]
    for row in reports:
        row["url_path"] = _url_path(row["name"])
    return envelope.ok("report.list",
                       _jsonify({"n": len(reports), "reports": reports}))


def report_show(args: Any) -> envelope.Envelope:
    """单报告摘要（summary.json 真读；缺失 → NOT_FOUND）。"""
    from factorlab.adapters import results_fs
    name = args.name
    if not _safe_name(name):
        return envelope.fail("report.show", "NOT_FOUND", f"非法报告名: {name!r}",
                             hint=_REPORT_HINT)
    path = Path(settings.results_dir) / name / "summary.json"
    if not path.is_file():
        return envelope.fail("report.show", "NOT_FOUND",
                             f"报告 {name} 不存在（{path}）", hint=_REPORT_HINT)
    try:
        summary = results_fs.read_summary(path)
    except (ValueError, OSError) as exc:
        return envelope.fail("report.show", "DATA", f"summary 读取失败: {exc}",
                             hint="重跑 `flab factor run <spec>`")
    return envelope.ok("report.show",
                       _jsonify({"name": name, "summary": summary,
                                 "url_path": _url_path(name)}),
                       artifacts={"summary": str(path)})


# ================================================================
# url / serve
# ================================================================

def report_url(args: Any) -> envelope.Envelope:
    """报告静态 URL（不启服务）：校验存在 → 返回 path/url。"""
    name = args.name
    base = str(getattr(args, "base", None) or "http://127.0.0.1:8000").rstrip("/")
    if not _safe_name(name):
        return envelope.fail("report.url", "NOT_FOUND", f"非法报告名: {name!r}",
                             hint=_REPORT_HINT)
    path = Path(settings.results_dir) / name / "summary.json"
    if not path.is_file():
        return envelope.fail("report.url", "NOT_FOUND",
                             f"报告 {name} 不存在（{path}）", hint=_REPORT_HINT)
    return envelope.ok("report.url",
                       {"name": name, "base": base,
                        "path": _url_path(name),
                        "url": f"{base}{_url_path(name)}"},
                       artifacts={"summary": str(path)})


def report_serve(args: Any) -> envelope.Envelope:
    """启动只读 Web 报告服务（同步阻塞；测试注入 `uvicorn.run` 替身）。"""
    import uvicorn

    from factorlab.surfaces.web.app import create_app
    results_dir = Path(settings.results_dir)
    host = str(getattr(args, "host", None) or "127.0.0.1")
    port = int(getattr(args, "port", None) or 8000)
    app = create_app(results_dir)
    uvicorn.run(app, host=host, port=port)
    return envelope.ok("report.serve",
                       {"host": host, "port": port,
                        "url": f"http://{host}:{port}/",
                        "reports_dir": str(results_dir)},
                       artifacts={"results_dir": str(results_dir)})


# ================================================================
# 注册（registry 单点；describe 自动可见）
# ================================================================

def _register(name: str, *, handler, description: str, params: Any = (),
              defaults: dict[str, Any] | None = None, examples: Any = (),
              output_schema: dict[str, Any] | None = None) -> None:
    registry.register(
        registry.CommandSpec(
            name=name,
            params=tuple(params),
            defaults={"json": True, "pretty": False, **(defaults or {})},
            description=description,
            examples=tuple(examples),
            output_schema=output_schema or {"type": "object"},
        ),
        handler,
    )


_register(
    "report.list", handler=report_list,
    params=(_JSON, _PRETTY),
    description="报告列表（带 summary 的因子报告；新报告在前 + url_path）",
    examples=("flab report list --json",),
    output_schema={"type": "object", "properties": {
        "n": {"type": "integer"}, "reports": {"type": "array"}}},
)

_register(
    "report.show", handler=report_show,
    params=(registry.ParamSpec("name", kind="str", positional=True, required=True,
                               help="报告名（results/<name>）"),
            _JSON, _PRETTY),
    defaults={"name": None},
    description="单报告摘要（summary.json 真读；缺失 → NOT_FOUND）",
    examples=("flab report show momentum_20d_turnrank_top2 --json",),
    output_schema={"type": "object", "properties": {
        "name": {"type": "string"}, "summary": {"type": "object"},
        "url_path": {"type": "string"}}},
)

_register(
    "report.url", handler=report_url,
    params=(registry.ParamSpec("name", kind="str", positional=True, required=True,
                               help="报告名"),
            registry.ParamSpec("base", kind="str",
                               help="服务基址（缺省 http://127.0.0.1:8000）"),
            _JSON, _PRETTY),
    defaults={"base": "http://127.0.0.1:8000"},
    description="报告静态 URL（不启服务；缺失 → NOT_FOUND）",
    examples=("flab report url momentum_20d_turnrank_top2 --json",),
    output_schema={"type": "object", "properties": {
        "name": {"type": "string"}, "base": {"type": "string"},
        "path": {"type": "string"}, "url": {"type": "string"}}},
)

_register(
    "report.serve", handler=report_serve,
    params=(registry.ParamSpec("port", kind="int", help="端口（缺省 8000）"),
            registry.ParamSpec("host", kind="str", help="绑定地址（缺省 127.0.0.1）"),
            _JSON, _PRETTY),
    defaults={"port": 8000, "host": "127.0.0.1"},
    description="启动只读 Web 报告服务（同步阻塞；url 命令不启服务）",
    examples=("flab report serve --port 8000 --host 127.0.0.1",),
    output_schema={"type": "object", "properties": {
        "host": {"type": "string"}, "port": {"type": "integer"},
        "url": {"type": "string"}, "reports_dir": {"type": "string"}}},
)
