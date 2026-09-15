"""P-3 面板库端口实现（parquet，results/<name>/panel.parquet）。

收敛既有四处读法（eval/correlation._load_signal、eval/cross_section._read_aligned_panel、
web 因子枚举、correlation 枚举）——语义逐字保留（含多输出 panel 的专门文案、
字符串日期容忍）。缺失文案单点在 ports.panel_store.panel_missing。
"""
from __future__ import annotations

import datetime
import pathlib

import polars as pl

from factorlab.ports.panel_store import panel_missing


class ParquetPanelStore:
    """results 目录的 panel 读取与枚举（P-3 实现）。"""

    def _path(self, results_dir: pathlib.Path, name: str) -> pathlib.Path:
        return pathlib.Path(results_dir) / name / "panel.parquet"

    # ---------------- P-3 最小面 ----------------
    def load_panel(self, results_dir: pathlib.Path, name: str) -> pl.DataFrame:
        p = self._path(results_dir, name)
        if not p.exists():
            raise panel_missing(results_dir, name)
        return pl.read_parquet(p)

    def load_dates(self, results_dir: pathlib.Path, name: str) -> pl.DataFrame:
        """只读 `date` 列（correlation 抽样周用——**不得为此整张 panel 载入**）。

        缺失语义与 `load_panel` 一致（`ports.panel_store.panel_missing`）。
        """
        p = self._path(results_dir, name)
        if not p.exists():
            raise panel_missing(results_dir, name)
        return pl.scan_parquet(p).select("date").collect()

    def list_factors(self, results_dir: pathlib.Path) -> list[str]:
        return sorted(p.parent.name for p in
                      pathlib.Path(results_dir).glob("*/panel.parquet"))

    def has_panel(self, results_dir: pathlib.Path, name: str) -> bool:
        return self._path(results_dir, name).exists()

    # ---------------- 既有读法（逐字保留语义） ----------------
    def load_signal_columns(self, results_dir: pathlib.Path, name: str,
                            dates: list | None = None) -> pl.DataFrame:
        """correlation 读法：date/code/signal（dates 非 None 时 lazy 先过滤——省内存）。"""
        p = self._path(results_dir, name)
        if not p.exists():
            raise panel_missing(results_dir, name)
        lf = pl.scan_parquet(p)
        if dates is not None:
            lf = lf.filter(pl.col("date").is_in(dates))
        return lf.select(["date", "code", "signal"]).rename({"signal": name}).collect()

    def read_aligned_panel(self, results_dir: pathlib.Path, name: str, fwd_col: str,
                           keep_fwd: bool) -> pl.DataFrame:
        """cross_section 读法：→ [date, code, name(, fwd_col)]。

        - 缺失 → FileNotFoundError（panel_missing 文案）
        - panel 无字面 signal 列（多输出 run）→ ValueError（专门文案）
        - date cast pl.Date（容忍字符串日期写盘的测试样板）
        """
        p = self._path(results_dir, name)
        if not p.exists():
            raise panel_missing(results_dir, name)
        if "signal" not in pl.read_parquet_schema(p):
            raise ValueError(
                f"因子 {name} 的 panel 缺 signal 列（多输出 run 的 panel 无字面 signal 列）"
                f"——仅支持单输出 run 的 panel")
        cols = ["date", "code", "signal"] + ([fwd_col] if keep_fwd else [])
        df = pl.scan_parquet(p).select(cols).collect().rename({"signal": name})
        if df.schema["date"] == pl.String:
            df = df.with_columns(pl.col("date").str.to_date())
        else:
            df = df.with_columns(pl.col("date").cast(pl.Date, strict=False))
        return df
