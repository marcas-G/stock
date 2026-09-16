"""R30 Task8 存根突变检查：财报解析/ts_code 推导/fact 轮换/灌入替换为存根，指定测试必须失败。

不修改工作区最终状态：每个突变跑完立即恢复原文（最后逐字节断言与原文一致）。
复现：python3 governance/evidence/verification/R30/task8/mutation.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
PY = REPO / "platform" / ".venv" / "bin" / "python"

LIB = REPO / "platform/tools/lib/fundamentals.py"
PARSE = REPO / "platform/tools/pan_update/parse_fundamentals_xlsx.py"
INGEST = REPO / "platform/tools/ch_ingest/ingest_fundamentals.py"
DDL = REPO / "platform/tools/ch_ingest/ddl.sql"

T_PARSE = "platform/tools/pan_update/tests/test_fundamentals_parse.py"
T_INGEST = "platform/tools/ch_ingest/tests/test_ingest_fundamentals.py"

STUB = '''

def parse_xlsx(path):  # MUTATION: 硬编码存根
    import datetime as _dt
    row = {"ts_code": "000001.SZ", "updated_date": _dt.date(2026, 8, 15),
           "report_period": "6", "list_date": _dt.date(1991, 4, 3), "market": "sz",
           "industry": "银行", "sw_industry": "全国性银行", "sw_sub": "股份制银行"}
    row.update({c: None for c in FU.NUM_COLUMNS})
    return pl.DataFrame([row] * 200, schema=FU.OUT_SCHEMA)
'''

# name -> (path, old, new, tests)；old=None 表示 append
MUTS = {
    # —— 映射/schema/ts_code 推导（lib）——
    "lib: 总股本/流通A股 映射互换": (
        LIB,
        '    ("总股本", "total_shares"),\n    ("流通A股", "float_a_shares"),',
        '    ("总股本", "float_a_shares"),\n    ("流通A股", "total_shares"),', [T_PARSE]),
    "lib: ts_code_of 忽略市场列（优先级丢失）": (
        LIB, "    if mk in MARKET_SUFFIX:", "    if False:", [T_PARSE]),
    "lib: ts_code_of 缺市场回退禁用": (
        LIB, "    return code6 + market_suffix(code6)", "    return code6", [T_PARSE]),
    # —— 解析（行筛选/缺失标记/表头/硬编码存根/数值/轮换/选源）——
    "parse: 丢 updated_date 缺失行过滤（保留占位行）": (
        PARSE,
        "        if updated is None:\n            dropped += 1\n            continue\n",
        "", [T_PARSE]),
    "parse: `None` 字符串不视为缺失": (
        PARSE, '_BLANK = {"", "None", "-", "—"}', '_BLANK = {"", "-", "—"}',
        [T_PARSE]),
    "parse: 表头缺列校验关闭": (
        PARSE,
        '    if missing:\n        raise ValueError(f"财报表头缺列：{missing}")',
        '    if False:\n        raise ValueError(f"财报表头缺列：{missing}")', [T_PARSE]),
    "parse: parse_xlsx 硬编码存根（200 行首行复制）": (
        PARSE, None, STUB, [T_PARSE]),
    "parse: 数值解析恒 None": (
        PARSE, "        return float(t)", "        return None", [T_PARSE]),
    "parse: write_fact 不轮换 .prev": (
        PARSE,
        "    if out.exists():\n        os.replace(out, out.with_name(out.name + \".prev\"))\n",
        "", [T_PARSE]),
    "parse: find_latest_xlsx 取最小（最早）": (
        PARSE, "    return cands[-1]", "    return cands[0]", [T_PARSE]),
    # —— 灌入（幂等/读校验/DDL 同步/批量）——
    "ingest: write 丢 TRUNCATE（重跑翻倍）": (
        INGEST, '    client.command(f"TRUNCATE TABLE {db}.{TABLE}")\n', "",
        [T_INGEST]),
    "ingest: load_fact 缺文件校验移除": (
        INGEST,
        '    if not path.is_file():\n'
        '        raise FileNotFoundError(f"财报 fact 不存在：{path}（先跑 parse_fundamentals_xlsx.py）")\n',
        "", [T_INGEST]),
    "ingest: load_fact 列漂移校验关闭": (
        INGEST, "    if list(df.columns) != list(fundamentals.OUT_COLUMNS):",
        "    if False:", [T_INGEST]),
    "ingest: create_sql 排序键反转": (
        INGEST,
        '            f"ENGINE = MergeTree ORDER BY (updated_date, ts_code)")',
        '            f"ENGINE = MergeTree ORDER BY (ts_code, updated_date)")', [T_INGEST]),
    "ingest: 批量切片步长错（漏行）": (
        INGEST, "    for i in range(0, df.height, batch_size):",
        "    for i in range(0, df.height, batch_size + 1):", [T_INGEST]),
    "ddl: fundamentals 块去掉 net_profit 行": (
        DDL, "    net_profit     Nullable(Float64),    -- 净利润（元）\n", "",
        [T_INGEST]),
}


def run_pytest(tests: list[str]) -> int:
    proc = subprocess.run([str(PY), "-m", "pytest", *tests, "-q"],
                          cwd=REPO, capture_output=True, text=True)
    return proc.returncode


def main() -> int:
    originals = {p: p.read_text(encoding="utf-8") for p in {LIB, PARSE, INGEST, DDL}}
    caught = 0
    try:
        for name, (path, old, new, tests) in MUTS.items():
            src = originals[path]
            if old is None:
                mutated = src + new
            else:
                if old not in src:
                    print(f"[HARNESS-ERROR] 替换串不存在：{name}")
                    return 2
                mutated = src.replace(old, new)
            path.write_text(mutated, encoding="utf-8")
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
    rc = run_pytest([T_PARSE, T_INGEST])
    print(f"恢复后两组测试：rc={rc}")
    return 0 if caught == len(MUTS) and rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
