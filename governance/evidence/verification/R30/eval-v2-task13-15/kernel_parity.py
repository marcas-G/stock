#!/usr/bin/env python3
"""R30 Task15 Step2：评估内核「迁移前 vs 迁移后」逐值对拍（R08 复算三面板）。

精度口径：迁移为**纯代码搬移**（非 docstring 行 diff 为空，见
`task15-parity-code-identity.txt`）。但 polars 并行 group_by 归约在最后一次运算
（每 decile 组均值的组均值）不保证跨调用 bit 级稳定（同实现连续调用亦产生
~1e-17 相对差；`task15-parity-nondeterminism.txt` 证明）——因此对拍采用
结构全等 + 数值 `rel_tol=1e-9`（远严于 R08 复算 1e-6，且覆盖该噪声 7 个数量级）。

冻结输入（2026-09-17 从 runs/platform/<name>/weekly.parquet 复制，sha256 见
task15-parity-panels.sha256；R08 复算用例同源三因子）：
  /tmp/opencode/eval-v2/parity-panels/{low_vol_20d,max_effect_20d_high,intraday_high_time}/weekly.parquet

用法：
  # 迁移前（旧实现，独立 quant_core 包）
  platform/.venv/bin/python .../kernel_parity.py --impl quant_core \
      --out .../task15-parity-old.json
  # 迁移后（合并实现）
  platform/.venv/bin/python .../kernel_parity.py --impl factorlab \
      --out .../task15-parity-new.json
  # 逐值对比（NaN 归一为字符串 "NaN" 后 JSON 全等）
  platform/.venv/bin/python .../kernel_parity.py --compare old.json new.json

口径：桥接层同款过滤（signal/target null 行剔除；NaN 留给 kernel is_finite 语义）。
direction 冻结自 2026-09-17 runs summary（low_vol_20d=+1；其余=-1）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import polars as pl

PANELS = Path("/tmp/opencode/eval-v2/parity-panels")
TARGET = "forward_return_5d"
FACTORS = {  # name -> (direction, panel_path)
    "low_vol_20d": (1, PANELS / "low_vol_20d" / "weekly.parquet"),
    "max_effect_20d_high": (-1, PANELS / "max_effect_20d_high" / "weekly.parquet"),
    "intraday_high_time": (-1, PANELS / "intraday_high_time" / "weekly.parquet"),
}


def _load_impl(name: str):
    if name == "quant_core":
        import quant_core
        return quant_core.evaluate_factor
    from factorlab.core.eval import kernel
    return kernel.evaluate_factor


def _normalize(obj):
    if isinstance(obj, float):
        return "NaN" if (math.isnan(obj) or math.isinf(obj)) else repr(obj)
    if isinstance(obj, dict):
        return {k: _normalize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize(v) for v in obj]
    return obj


def capture(impl_name: str) -> dict:
    fn = _load_impl(impl_name)
    out = {}
    for name, (direction, panel_path) in FACTORS.items():
        sha = hashlib.sha256(panel_path.read_bytes()).hexdigest()
        df = pl.read_parquet(panel_path).filter(
            pl.col("signal").is_not_null() & pl.col(TARGET).is_not_null())
        args = (df["date"].dt.strftime("%Y-%m-%d").to_list(), df["code"].to_list(),
                df["signal"].to_list(), df[TARGET].to_list())
        result = fn(*args, "_factor", direction)
        out[name] = {"panel_sha256": sha, "direction": direction,
                     "rows": df.height, "result": _normalize(result)}
    return out


def _diff(old, new, path: str, rtol: float = 1e-9) -> tuple[list[str], float]:
    """递归比较：结构/键/字符串/整型全等；浮点 rel_tol；"NaN" 双方一致。"""
    if isinstance(old, dict) and isinstance(new, dict):
        if set(old) != set(new):
            return [f"{path}: keys {set(old) ^ set(new)}"], 0.0
        diffs, mr = [], 0.0
        for k in old:
            d, m = _diff(old[k], new[k], f"{path}.{k}", rtol)
            diffs += d
            mr = max(mr, m)
        return diffs, mr
    if isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            return [f"{path}: len {len(old)} != {len(new)}"], 0.0
        diffs, mr = [], 0.0
        for i, (a, b) in enumerate(zip(old, new)):
            d, m = _diff(a, b, f"{path}[{i}]", rtol)
            diffs += d
            mr = max(mr, m)
        return diffs, mr
    if old == new:
        return [], 0.0
    try:
        fa, fb = float(old), float(new)
    except (TypeError, ValueError):
        return [f"{path}: {old!r} != {new!r}"], 0.0
    rel = abs(fa - fb) / max(abs(fa), abs(fb), 1e-300)
    return ([f"{path}: rel={rel:.2e}"] if rel > rtol else []), rel


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--impl", choices=["quant_core", "factorlab"])
    ap.add_argument("--out", type=Path)
    ap.add_argument("--compare", nargs=2, type=Path, metavar=("OLD", "NEW"))
    args = ap.parse_args()
    if args.compare:
        old = json.loads(args.compare[0].read_text(encoding="utf-8"))
        new = json.loads(args.compare[1].read_text(encoding="utf-8"))
        assert set(old) == set(new)
        diffs, max_rel = _diff(old, new, path="")
        if not diffs:
            print(f"OK：结构全等 + 数值一致（rel_tol=1e-9；max_rel≈{max_rel:.2e}，"
                  "三面板全字段；NaN 双方一致）")
            return
        raise SystemExit(f"MISMATCH：{diffs[:10]}")
    assert args.impl and args.out, "--impl/--out 必填"
    payload = capture(args.impl)
    args.out.write_text(json.dumps(payload, indent=1, ensure_ascii=False),
                        encoding="utf-8")
    print(f"captured {args.impl} → {args.out}（3 面板）")


if __name__ == "__main__":
    main()
