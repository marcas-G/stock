#!/usr/bin/env python3
"""Task 10 minutes 试点月同步驱动（2026/09，12 个缺失交易日）。

裁决（T10 任务指令）：分钟本轮只取「最近 1 个月缺失日」做试点，全量补齐留定时首跑。
本脚本不引入新逻辑——复用生产代码路径：
  share._default_listdir → cli._category_entries → sync.sync_category（与 CLI sync 同一函数、
  同一 state 文件、同一 QuarkTransport），仅把 entries 预过滤为 <year>/<month> 前缀。
用法（在仓根）：
  FACTORLAB_MAX_MEMORY=8GB platform/.venv/bin/python \
    governance/evidence/verification/R30/task10/minutes_pilot_sync.py 2026/09
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "platform" / "tools"))

from pan_update import cli, config, stages, state as st, sync  # noqa: E402
from pan_update.share import _default_listdir  # noqa: E402


def main() -> int:
    prefix = sys.argv[1] if len(sys.argv) > 1 else "2026/09"
    if not prefix.endswith("/"):
        prefix += "/"
    guard = cli.apply_memory_guard(os.environ)
    if guard:
        print(f"memory guard: {guard} bytes (RLIMIT_AS)", flush=True)

    state = st.load_state(cli.STATE_PATH)
    dest_root = cli._dest_root(cli.RAW_ROOT, "minutes")
    entries = [e for e in cli._category_entries(_default_listdir, "minutes")
               if e.rel_path.startswith(prefix)]
    print(f"minutes pilot prefix={prefix!r} entries={len(entries)} "
          f"total={sum(e.size for e in entries)/1e6:.1f} MB", flush=True)

    with stages.single_instance(cli.LOCK_PATH):
        rep = sync.sync_category(
            state, "minutes", entries=entries, dest_root=dest_root,
            transport=sync.QuarkTransport(
                log=lambda line: print(line, flush=True)),
            dry_run=False, workers=8, state_path=cli.STATE_PATH)
        if rep.downloaded or rep.adopted:
            state.setdefault("stages", {})["minutes"] = {}
            print("[freshness] 有新增 → 清 minutes 阶段标记", flush=True)
        st.save_state_atomic(cli.STATE_PATH, state)

    print(f"[minutes] to_fetch={len(rep.to_fetch)} downloaded={len(rep.downloaded)} "
          f"adopted={len(rep.adopted)} unchanged={len(rep.unchanged)} "
          f"manual={len(rep.manual)} failed={len(rep.failed)}", flush=True)
    for rel in rep.downloaded:
        print(f"  got    {rel}", flush=True)
    for rel in rep.adopted:
        print(f"  adopt  {rel}", flush=True)
    for it in rep.manual:
        print(f"  manual {it}", flush=True)
    for it in rep.failed:
        print(f"  failed {it}", flush=True)
    return 1 if rep.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
