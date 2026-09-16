"""R30 Task7 存根突变检查：资金流解析/读路径/灌入替换为存根，指定测试必须失败。

不修改工作区最终状态：每个突变跑完立即恢复原文（最后逐字节断言与原文一致）。
复现：python3 governance/evidence/verification/R30/task7/mutation.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
PY = REPO / "platform" / ".venv" / "bin" / "python"

MONEYFLOW = REPO / "platform/tools/lib/moneyflow.py"
PAN_PARSE = REPO / "platform/tools/pan_update/parse_fund_flow.py"
SOURCE = REPO / "platform/src/factorlab/adapters/read/source.py"
INGEST = REPO / "platform/tools/ch_ingest/ingest_moneyflow.py"

T_PARSE = "platform/tools/pan_update/tests/test_fund_flow_parse.py"
T_INGEST = "platform/tools/ch_ingest/tests/test_ingest_moneyflow.py"
T_SOURCE = "platform/tests/test_source_moneyflow.py"

# name -> (path, old, new, tests)
MUTS = {
    # —— 解析器（逐值/单位/缺省标记/壳/后缀/表头/位置）——
    "moneyflow: 亿 单位降一档（e8→e7）": (
        MONEYFLOW, '("亿", "e8")', '("亿", "e7")', [T_PARSE]),
    "moneyflow: em-dash 缺省标记未处理（只剩 -）": (
        MONEYFLOW, '_MISSING = {"-", "—"}', '_MISSING = {"-"}', [T_PARSE]),
    "moneyflow: parse_code 不补零（吞掉 zfill）": (
        MONEYFLOW, "return m.group(0).zfill(6)", "return m.group(0)", [T_PARSE]),
    "moneyflow: 北交所 920 后缀分支禁用": (
        MONEYFLOW, 'if code6.startswith("920"):\n        return ".BJ"',
        'if False:\n        return ".BJ"', [T_PARSE]),
    "moneyflow: parse_zj 表头校验注释掉": (
        MONEYFLOW, "if header[:len(HEADER)] != HEADER or any(header[len(HEADER):]):",
        "if False:", [T_PARSE]),
    "moneyflow: 数值列起点错位（5→4）": (
        MONEYFLOW, "_FIRST_NUM_POS = 5", "_FIRST_NUM_POS = 4", [T_PARSE]),
    "moneyflow: parse_zj trade_date 硬编码": (
        MONEYFLOW, '"trade_date": trade_date}', '"trade_date": datetime.date(2000, 1, 1)}',
        [T_PARSE]),
    "moneyflow: 短行静默跳过（raise→continue）": (
        MONEYFLOW,
        'raise ValueError(\n                f"第 {lineno} 行列数不足：{len(fields)} < {len(HEADER) + 1}")',
        "continue", [T_PARSE]),
    "moneyflow: 未知代码前缀静默补 SZ（不报错）": (
        MONEYFLOW, 'raise ValueError(f"无法识别代码板块: {code6!r}")',
        'return ".SZ"', [T_PARSE]),
    # —— 读路径（映射/三处 JOIN/分类）——
    "source: _MONEYFLOW_MAP 掉一列（small_net_pct）": (
        SOURCE, '    "small_net_pct": "small_net_pct",\n', "", [T_SOURCE]),
    "source: load_daily duckdb 去掉 moneyflow JOIN": (
        SOURCE,
        '        sql += " LEFT JOIN moneyflow f ON d.trade_date = f.trade_date AND d.ts_code = f.ts_code"\n'
        '    if "idx_ret" in requested:',
        '        pass\n    if "idx_ret" in requested:', [T_SOURCE]),
    "source: load_daily ch 去掉 moneyflow JOIN": (
        SOURCE,
        '        sql += (f" LEFT JOIN {db}.moneyflow f"\n'
        '                f" ON d.trade_date = f.trade_date AND d.ts_code = f.ts_code")\n'
        '    if "idx_ret" in requested:',
        '        pass\n    if "idx_ret" in requested:', [T_SOURCE]),
    "source: fill_state duckdb 去掉 moneyflow JOIN": (
        SOURCE,
        '        sql += " LEFT JOIN moneyflow f ON d.trade_date = f.trade_date AND d.ts_code = f.ts_code"\n'
        "    if want_idx:",
        '        pass\n    if want_idx:', [T_SOURCE]),
    "source: fill_state ch 去掉 moneyflow JOIN": (
        SOURCE,
        '        sql += (f" LEFT JOIN {db}.moneyflow f"\n'
        '                f" ON d.trade_date = f.trade_date AND d.ts_code = f.ts_code")\n'
        "    if want_idx:",
        '        pass\n    if want_idx:', [T_SOURCE]),
    "source: _classify_columns 不认 moneyflow 列": (
        SOURCE, "        if c in _MONEYFLOW_MAP:\n            money.append(c)\n            continue\n",
        "", [T_SOURCE]),
    # —— 灌入（zip→帧语义/DDL/幂等）——
    "ingest: 月/日 zip 去重取先（keep=last→first）": (
        INGEST, '.unique(subset=["ts_code", "trade_date"], keep="last")',
        '.unique(subset=["ts_code", "trade_date"], keep="first")', [T_INGEST]),
    "ingest: 解码错编（gbk→utf-8）": (
        INGEST, 'zf.read(name).decode("gbk")', 'zf.read(name).decode("utf-8")',
        [T_INGEST]),
    "ingest: write 丢 TRUNCATE（重跑翻倍）": (
        INGEST, '    client.command(f"TRUNCATE TABLE {db}.{TABLE}")\n', "", [T_INGEST]),
    "ingest: create_sql 少一列（DDL 漂移）": (
        INGEST, '*(f"{c} Nullable(Float64)" for c in moneyflow.NUM_COLUMNS)',
        '*(f"{c} Nullable(Float64)" for c in moneyflow.NUM_COLUMNS[:-1])', [T_INGEST]),
    "parse_fund_flow: 公共面不转发（parse_zj 存根）": (
        PAN_PARSE, "parse_zj = _core.parse_zj",
        "def parse_zj(text, trade_date):\n    return None", [T_PARSE]),
}


def run_pytest(tests: list[str]) -> int:
    proc = subprocess.run([str(PY), "-m", "pytest", *tests, "-q"],
                          cwd=REPO, capture_output=True, text=True)
    return proc.returncode


def main() -> int:
    originals = {p: p.read_text(encoding="utf-8")
                 for p in {MONEYFLOW, PAN_PARSE, SOURCE, INGEST}}
    caught = 0
    try:
        for name, (path, old, new, tests) in MUTS.items():
            src = originals[path]
            if old not in src:
                print(f"[HARNESS-ERROR] 替换串不存在：{name}")
                return 2
            path.write_text(src.replace(old, new), encoding="utf-8")
            rc = run_pytest(tests)
            ok = rc != 0
            caught += ok
            print(f"{'CAUGHT' if ok else 'NOT-CAUGHT'}  rc={rc}  {name}")
            path.write_text(src, encoding="utf-8")
    finally:
        for path, src in originals.items():
            path.write_text(src, encoding="utf-8")
            assert path.read_text(encoding="utf-8") == src, f"恢复失败：{path}"
    print(f"\n{caught}/{len(MUTS)} 突变被测试抓住；工作区已恢复逐字节一致")
    rc = run_pytest([T_PARSE, T_INGEST, T_SOURCE])
    print(f"恢复后三组测试：rc={rc}")
    return 0 if caught == len(MUTS) and rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
