#!/usr/bin/env python3
"""xscore 研究实验流水线（Prefect 3）。

DAG：
    score(分组 × 模型)  →  portfolio(执行口径 × 域)  →  report

- 每个 score 节点保持 walk-forward 训练并落 `signal.npz` + `metrics.json` + `manifest.json`；
- portfolio 节点对信号做周频长多评估（T+1 开盘 / T 日收盘 × 全市场 / Q1-Q3）；
- Prefect 缓存键 = 面板指纹 + 分组列 + 模型 + 折参数 + 代码指纹 → 输入不变则秒级跳过；
- 计算 step 以 **platform venv** 子进程执行（依赖隔离）；本流程只编排；
- flow 开始锁箱判定（T12b）：config=候选，起点钉死身份（panel 签名 + config **内容 sha**），
  碰箱即登记 **final**（同候选幂等复用；收尾复用起点 fp，不重算）；
  `off`/无 state 不读不写台账（unknown/[]；roll 由 controller 执行）；
  配额不足/stale/panel 缺失均在计算前 fail fast（指引 `factorlab lockbox status`/`roll`）；
- flow 收尾写/刷新 `<out>/manifest.json` **并双写 campaign 级 `<out>/../manifest.json`**
  （T10 锁箱纪律：platform_commit/panel_sig/config_path/window_id/sample_role/access_ids；
  已有字段保留，`access_ids` = 既有 ∪ 本次登记 id），并把 `result_ref` 回填为该 run 目录。

运行：
    research/.venv/bin/python research/tools/xscore/pipeline/flows.py \
        --config research/tools/xscore/pipeline/configs/m0-split.yaml
（带 UI：先 `governance/ops/install_prefect_server.sh` 起 server，再设
  PREFECT_API_URL=http://127.0.0.1:4200/api 运行本命令）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import yaml
from prefect import flow, task
from prefect.task_runners import ThreadPoolTaskRunner

HERE = Path(__file__).resolve().parent
STOCK = HERE.parents[3]
QR = Path("/data/students/gaolei/quantresearch")
PLATFORM_PY = STOCK / "platform/.venv/bin/python"
CODE_FILES = [HERE / "xlib.py", HERE / "data_prep.py", HERE / "score_once.py",
              HERE / "portfolio_once.py", HERE / "flows.py",
              HERE.parent / "score_model.py", HERE.parent / "aggregators.py",
              HERE.parent / "expansion.py", HERE.parent / "normalize.py",
              HERE.parent / "calibrate.py"]
sys.path.insert(0, str(HERE))
import xlib as lib  # noqa: E402


def _code_sig() -> str:
    h = hashlib.sha256()
    for f in CODE_FILES:
        if f.is_file():
            h.update(f.read_bytes())
    return h.hexdigest()[:16]


def _resolve_groups(cfg: dict) -> dict[str, list[int]]:
    import numpy as np
    members = [str(x) for x in np.load(cfg["panel"], allow_pickle=False)["members"]]
    groups: dict[str, list[int]] = {}
    for name, spec in cfg["groups"].items():
        if spec == "*":
            groups[name] = list(range(len(members)))
        elif isinstance(spec, dict) and spec.get("source") == "reference":
            ref = yaml.safe_load((QR / "factor/_reference.yaml").read_text())
            names = [m["name"] for m in ref["scales"][spec["scale"]]]
            missing = [n for n in names if n not in members]
            if missing:
                raise ValueError(
                    f"分组 {name!r} 的参考库成员不在面板：{missing}——"
                    "先 `make xpipe-data`（ref-sync 自动补算；缺 spec 会 fail-fast）")
            groups[name] = [members.index(n) for n in names]
        else:
            unknown = [n for n in spec if n not in members]
            if unknown:
                raise ValueError(
                    f"分组 {name!r} 含非面板成员：{unknown}——"
                    f"面板成员共 {len(members)} 个（缺产物先 `make xpipe-data`）")
            groups[name] = [members.index(n) for n in spec]
        if not groups[name]:
            raise ValueError(f"分组 {name!r} 解析为空（检查成员名/参考库 scales）")
    return groups


@task(cache_key_fn=lambda ctx, params: params["key"], cache_expiration=timedelta(days=7))
def data_prep_task(key: str, cfg: dict) -> str:
    """确保数据/面板缓存存在（幂等；成员集合来自 config：data.members ∪ factors）。"""
    if not cfg.get("data", {}).get("ensure", True):
        return "skipped"
    caches = cfg["data"].get("cache_dir", str(QR / "data/cache"))
    argv = [str(HERE / "data_prep.py"), "--cache-dir", caches,
            "--only", cfg["data"].get("only", "panel,open_adj,mv,limits,amount")]
    if cfg.get("config_path"):
        argv += ["--config", str(cfg["config_path"])]
    _run(argv)
    return caches


def _factor_key(cfg: dict) -> str:
    import hashlib
    import json as _json
    payload = []
    for entry in cfg.get("factors") or []:
        spec = Path(entry["spec"])
        payload.append([str(spec), lib.file_sig(spec),
                        list(entry.get("window", [])) or None])
    digest = hashlib.sha256(
        _json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    return f"factor|{digest}|{_code_sig()}"


@task(cache_key_fn=lambda ctx, params: params["key"], cache_expiration=timedelta(days=30))
def factor_task(key: str, cfg: dict) -> str:
    """按 config.factors 补算缺失 `_5y` 信号（幂等；锁箱 final 登记同源）。"""
    import data_prep as dp
    entries = cfg.get("factors") or []
    if not entries:
        return "no-factors"
    extra = [(Path(e["spec"]).stem, Path(e["spec"])) for e in entries]
    res = dp.compute_missing_members(extra_members=extra,
                                     member_names=[n for n, _ in extra])
    if res["errors"]:
        raise RuntimeError(f"factors 补算失败：{res['errors']}")
    return f"computed={res['computed']}"


def _run(step: list[str]) -> None:
    env = {**os.environ, "FACTORLAB_PIPELINE": "1"}   # E1：流水线标记（子进程）
    r = subprocess.run([str(PLATFORM_PY), *step], capture_output=True, text=True,
                       env=env)
    if r.returncode != 0:
        raise RuntimeError(f"step 失败 rc={r.returncode}\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
    print(r.stdout.strip())


def _score_key(context, parameters, cfg, group_name, model):
    return (f"{cfg['panel_sig']}|{group_name}|{model}|"
            f"{cfg['folds']}|{cfg.get('subsample', 0)}|{_code_sig()}")


def _portfolio_key(context, parameters, cfg, score_dir, exec_mode, domain):
    sig = lib.file_sig(Path(score_dir) / "signal.npz")
    return (f"{sig}|{exec_mode}|{domain}|{cfg['portfolio']}|{cfg['panel']}|"
            f"{_code_sig()}")


@task(cache_key_fn=lambda ctx, params: params["key"], cache_expiration=timedelta(days=30))
def score_task(key: str, name: str, cols: list[int], model: str, cfg: dict) -> str:
    out = Path(cfg["out"]) / "scores" / name
    _run([str(HERE / "score_once.py"), "--name", name, "--cols",
          ",".join(map(str, cols)), "--model", model, "--panel", cfg["panel"],
          "--out", str(out), "--train-days", str(cfg["folds"][0]),
          "--step", str(cfg["folds"][1]), "--test-days", str(cfg["folds"][2]),
          "--subsample", str(cfg.get("subsample", 0))])
    return str(out)


@task(cache_key_fn=lambda ctx, params: params["key"], cache_expiration=timedelta(days=30))
def portfolio_task(key: str, score_dir: str, exec_mode: str, domain: str, cfg: dict) -> str:
    import data_prep as dp
    out = Path(score_dir) / f"portfolio_{exec_mode}_{domain}.json"
    pf = cfg["portfolio"]
    cache_dir = Path(cfg.get("data", {}).get("cache_dir", QR / "data/cache"))
    aux = dp.aux_cache_paths(Path(cfg["panel"]), cache_dir)
    _run([str(HERE / "portfolio_once.py"), "--signal", str(Path(score_dir) / "signal.npz"),
          "--exec", exec_mode, "--domain", domain,
          "--panel", str(aux["panel"]), "--open-cache", str(aux["open_adj"]),
          "--mv", str(aux["mv"]), "--limits", str(aux["limits"]),
          "--adv", str(aux["amount"]),
          "--every", str(pf["every"]), "--q", str(pf["q"]),
          "--fee-bps", str(pf["fee_bps"]),
          "--limit-policy", str(pf.get("limit_policy", "block")),
          "--min-adv", str(pf.get("min_adv", 0.0)), "--out", str(out)])
    return str(out)


@task
def report_task(cfg: dict, score_dirs: list[str]) -> str:
    out = Path(cfg["out"])
    rows = []
    for sd in score_dirs:
        sd = Path(sd)
        met = json.loads((sd / "metrics.json").read_text()) if (sd / "metrics.json").is_file() else {}
        ic = met.get("ic", {})
        for pf in sorted(p for p in sd.glob("portfolio_*.json") if not p.name.endswith(".manifest.json")):
            p = json.loads(pf.read_text())
            c = p.get("config", {})
            exec_mode = c.get("exec_mode", p.get("exec_mode", "?"))
            domain = c.get("mv_scope", p.get("domain", "?"))
            rows.append((sd.name, f"{exec_mode}/{domain}", p["ann"], p["bench"],
                         p["excess"], p["ir"], ic.get("mean"), ic.get("t_nw")))
    lines = ["# xscore 流水线报告", "",
             f"- 配置：`{cfg['config_path']}`", f"- 面板：`{cfg['panel']}`（{cfg['panel_sig']}）",
             f"- 代码指纹：`{_code_sig()}`", f"- 分组：{list(cfg['groups'])}｜模型：{cfg['models']}",
             "", "| 信号 | 口径 | 年化 | 基准 | 超额 | IR | IC | IC t |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r[0]} | {r[1]} | {r[2]*100:.2f}% | {r[3]*100:.2f}% | "
                     f"{r[4]*100:.2f}% | {r[5]:.2f} | {r[6]:.4f} | {r[7]:.1f} |")
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(out / "REPORT.md")


def _base_updates(cfg: dict) -> dict:
    return {"platform_commit": lib.git_commit(), "panel_sig": cfg["panel_sig"],
            "config_path": cfg["config_path"]}


def _register_lockbox(cfg: dict) -> dict:
    """flow 开始：钉死候选身份（panel 签名 + config 内容 sha）→ 碰箱登记 final →
    写 manifest（fail-fast：配额/stale/panel 缺失都在计算前报错）。

    返回起点 context；收尾 `_write_manifest(cfg, ctx)` **必须复用**（不得重算 fp，
    否则首尾之间 panel 变化会双登记/双耗配额）。
    """
    out = Path(cfg["out"])
    ctx = lib.lockbox_register(panel=Path(cfg["panel"]), panel_sig=cfg["panel_sig"],
                               config_path=cfg["config_path"])
    lib.lockbox_finalize(ctx, run_manifest=out / "manifest.json",
                         campaign_manifest=out.parent / "manifest.json",
                         base_updates=_base_updates(cfg))
    return ctx


def _write_manifest(cfg: dict, ctx: dict | None = None) -> str:
    """flow 收尾：复用起点 context（无起点则现算）+ `result_ref` 回填 + 双写 manifest。

    campaign 级 = `<out>/../manifest.json`（与 `research_tidy`/G-LOCKBOX 权威层同层）；
    两处字段相同，`access_ids` = 各自既有非空 ∪ 本次 id（不被清空、不丢旧 id）。
    """
    out = Path(cfg["out"])
    if ctx is None:
        ctx = _register_lockbox(cfg)
    lib.lockbox_finalize(ctx, run_manifest=out / "manifest.json",
                         campaign_manifest=out.parent / "manifest.json",
                         base_updates=_base_updates(cfg), result_ref=str(out))
    return " ".join(str(p) for p in (out / "manifest.json", out.parent / "manifest.json"))


@flow(name="xscore-pipeline", log_prints=True)
def xscore_pipeline(config_path: str) -> str:
    os.environ.setdefault("FACTORLAB_PIPELINE", "1")   # E1：进程内（xlib 登记/子进程继承）
    cfg = yaml.safe_load(Path(config_path).read_text())
    cfg["config_path"] = str(config_path)
    cfg["panel_sig"] = lib.file_sig(Path(cfg["panel"]))
    cfg.setdefault("folds", [252, 63, 63])
    cfg.setdefault("subsample", 40000)
    cfg.setdefault("data", {})
    cfg.setdefault("models", ["M0a"])
    cfg.setdefault("portfolio", {"exec": ["open", "close"], "domains": ["all", "Q1Q3"],
                                 "every": 5, "q": 0.1, "fee_bps": 7,
                                 "limit_policy": "block", "min_adv": 0.0})
    # 面板存在则先登记（拒跑先于重链）；自定义成员集首次运行面板尚不存在，
    # 必须先建面板（factor/data_prep）再登记（登记要求 artifact 非 missing）。
    def _prepare_data() -> None:
        if cfg.get("factors"):
            factor_task.submit(key=_factor_key(cfg), cfg=cfg).result()
        if cfg["data"].get("ensure", True):
            data_prep_task.submit(
                key=f"data|{cfg['panel_sig']}|{lib.file_sig(Path(config_path))}"
                    f"|{_code_sig()}",
                cfg=cfg).result()

    if Path(cfg["panel"]).is_file():
        lockbox_ctx = _register_lockbox(cfg)
        _prepare_data()
    else:
        _prepare_data()
        lockbox_ctx = _register_lockbox(cfg)
    groups = _resolve_groups(cfg)
    print(f"[groups] " + ", ".join(f"{k}={len(v)}" for k, v in groups.items()))

    score_futs = []
    for gname, cols in groups.items():
        for model in cfg["models"]:
            name = f"{gname}_{model}"
            sd = score_task.submit(key=_score_key(None, {}, cfg, gname, model),
                                   name=name, cols=cols, model=model, cfg=cfg)
            score_futs.append(sd)
    score_dirs = [f.result() for f in score_futs]
    futs = []
    for sd in score_dirs:
        for exec_mode in cfg["portfolio"]["exec"]:
            for domain in cfg["portfolio"]["domains"]:
                futs.append(portfolio_task.submit(
                    key=_portfolio_key(None, {}, cfg, sd, exec_mode, domain),
                    score_dir=sd, exec_mode=exec_mode, domain=domain, cfg=cfg))
    [f.result() for f in futs]
    report = report_task(cfg, score_dirs)
    manifest = _write_manifest(cfg, lockbox_ctx)
    print(f"[done] {report} manifest={manifest}")
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--max-workers", type=int, default=2)
    args = ap.parse_args()
    xscore_pipeline.with_options(task_runner=ThreadPoolTaskRunner(max_workers=args.max_workers))(
        args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
