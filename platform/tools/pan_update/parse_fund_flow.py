"""pan_update 公共面：资金流源解析（Plan P T7 + R30 项 2 板块/成分扩充）。

实现核心在 `lib/moneyflow.py`（G-TOPO R3：跨工具共享代码必须落 lib/——
`ch_ingest/ingest_moneyflow.py` 与本模块共用同一份实现，不复制解析逻辑）；
本模块是 pan_update 侧的稳定导入面（brief 接口行 `parse_amount/parse_code/parse_zj`）。

项 2 新增：`parse_board_code/parse_zj_sector`（hyzj/gnzj 板块资金）、
`parse_gn_detail`（概念成分快照）、`load_sector_frames/load_concept_frames`（zip 全量帧）。
CLI 把 raw zip 解析为 fact（沿用 financials 的 fact 目录风格 + `.prev` 轮换）：
`data/fact/moneyflow_sector/moneyflow_sector.parquet`、
`data/fact/concept_members/concept_members.parquet`，
再由 `ch_ingest/ingest_moneyflow.py` 灌 CH（`STAGE_CHAINS["fund_flow"]` 两步）。

用法：
    from pan_update import parse_fund_flow as pf
    df = pf.parse_zj(open("zj.xls", "rb").read().decode("gbk"), trade_date)
    python parse_fund_flow.py [--root DIR] [--sector-fact P] [--concept-fact P]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from lib import moneyflow as _core
except ModuleNotFoundError:  # 脚本直启（无 conftest 铺路）→ 补 platform/tools 再导入
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from lib import moneyflow as _core

from pan_update import config

parse_amount = _core.parse_amount
parse_code = _core.parse_code
parse_zj = _core.parse_zj
parse_board_code = _core.parse_board_code
parse_zj_sector = _core.parse_zj_sector
parse_gn_detail = _core.parse_gn_detail
load_sector_frames = _core.load_sector_frames
load_concept_frames = _core.load_concept_frames
UnsupportedSectorFormat = _core.UnsupportedSectorFormat
NUM_COLUMNS = _core.NUM_COLUMNS
market_suffix = _core.market_suffix
SECTOR_OUT_COLUMNS = _core.SECTOR_OUT_COLUMNS
SECTOR_OUT_SCHEMA = _core.SECTOR_OUT_SCHEMA
CONCEPT_OUT_COLUMNS = _core.CONCEPT_OUT_COLUMNS
CONCEPT_OUT_SCHEMA = _core.CONCEPT_OUT_SCHEMA

DEFAULT_RAW = config.RAW_ROOT / "fund_flow"
DEFAULT_SECTOR_FACT = config.FACT_ROOT / _core.SECTOR_FACT_RELPATH
DEFAULT_CONCEPT_FACT = config.FACT_ROOT / _core.CONCEPT_FACT_RELPATH


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=None,
                    help=f"资金流原始根（缺省 {DEFAULT_RAW}）")
    ap.add_argument("--sector-fact", type=Path, default=None,
                    help=f"板块资金 fact（缺省 {DEFAULT_SECTOR_FACT}）")
    ap.add_argument("--concept-fact", type=Path, default=None,
                    help=f"概念成分 fact（缺省 {DEFAULT_CONCEPT_FACT}）")
    args = ap.parse_args(argv)
    root = Path(args.root) if args.root is not None else DEFAULT_RAW
    if not root.is_dir():
        raise FileNotFoundError(f"资金流原始目录不存在：{root}")
    sector = load_sector_frames(root)
    members = load_concept_frames(root)
    if sector.height == 0:
        raise ValueError("moneyflow_sector: 空源拒绝写 fact（拒绝下游清表）")
    if members.height == 0:
        raise ValueError("concept_members: 空源拒绝写 fact（拒绝下游清表）")
    # 延迟导入：write_fact 与 financials 共用（同工具内复用，不跨工具）
    from pan_update.parse_fundamentals_xlsx import write_fact

    s_out = write_fact(sector, args.sector_fact or DEFAULT_SECTOR_FACT)
    c_out = write_fact(members, args.concept_fact or DEFAULT_CONCEPT_FACT)
    print(f"  moneyflow_sector 源：{sector.height:,} rows / "
          f"{sector['trade_date'].n_unique()} days / "
          f"{sector['board_code'].n_unique()} boards", flush=True)
    print(f"  concept_members  源：{members.height:,} rows / "
          f"{members['trade_date'].n_unique()} days / "
          f"{members['board_code'].n_unique()} boards", flush=True)
    print(f"  fact: {s_out}", flush=True)
    print(f"  fact: {c_out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
