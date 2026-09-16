#!/usr/bin/env bash
# 终评修复波 I1/I2/I3 突变验证（红态证据；每步后自动还原源码）。
# 用法：bash /tmp/opencode/final-fix-mutations.sh > <evidence>/00-mutations.txt 2>&1
set -u
cd /data/students/gaolei/stock
PY=platform/.venv/bin/python
IM=platform/tools/ch_ingest/ingest_moneyflow.py
RC=platform/tools/ch_ingest/reconcile.py
CFG=platform/tools/pan_update/config.py

mutate() {  # $1=文件 $2=python 替换表达式
  python3 - "$1" "$2" <<'EOF'
import sys
p, expr = sys.argv[1], sys.argv[2]
s = open(p).read()
s2 = eval(expr, {}, {"s": s})
assert s2 != s, f"突变未命中：{p}"
open(p, "w").write(s2)
EOF
}

restore() { cp "/tmp/opencode/$(basename "$1")".bak "$1"; }

for f in "$IM" "$RC" "$CFG"; do cp "$f" "/tmp/opencode/$(basename "$f")".bak; done

echo "### M1 I1：删除 empty-frame 护栏（应 2 failed：write/main 均在 TRUNCATE 前拦住）"
mutate "$IM" 's.replace("    if df.height == 0:\n        raise ValueError(\"moneyflow: 空源拒绝灌入（拒绝清空 CH 表）\")\n", "")'
$PY -m pytest platform/tools/ch_ingest/tests/test_ingest_moneyflow.py -q; echo "rc=$?"
restore "$IM"

echo; echo "### M2 I2：repo_root() 忽略 FACTORLAB_STOCK_ROOT（应 1 failed：换根测试）"
mutate "$CFG" 's.replace("    override = os.environ.get(\"FACTORLAB_STOCK_ROOT\")\n    if override:\n        return Path(override)\n", "")'
$PY -m pytest platform/tools/pan_update/tests/test_config.py -q; echo "rc=$?"
restore "$CFG"

echo; echo "### M3 I2：DATA_ROOT 第二份字面拼接（绕过数据根单点；应 1 failed）"
mutate "$CFG" 's.replace("DATA_ROOT = repo_root() / \"data\"", "DATA_ROOT = Path(__file__).resolve().parents[3] / \"data\"")'
$PY -m pytest platform/tools/pan_update/tests/test_config.py -q; echo "rc=$?"
restore "$CFG"

echo; echo "### M4 I3：两表检查存根恒 True（应 8 failed：normal 仍过、缺口/空表/关键字段全抓）"
mutate "$RC" 's.replace("    good = (n == s_rows and mn == s_min", "    good = (True or n == s_rows and mn == s_min")'
$PY -m pytest platform/tools/ch_ingest/tests/test_reconcile.py -q; echo "rc=$?"
restore "$RC"

echo; echo "### M5 I3：去掉关键列空值/键异常比较（应 2 failed：key-field 漂移测试）"
mutate "$RC" 's.replace("and nulls == s_nulls and bad_code == 0 and uniq == n", "and uniq == n")'
$PY -m pytest platform/tools/ch_ingest/tests/test_reconcile.py -q; echo "rc=$?"
restore "$RC"

echo; echo "### M6 I1/I2/I3 还原后全绿（定向）"
$PY -m pytest platform/tools/ch_ingest/tests/test_ingest_moneyflow.py platform/tools/ch_ingest/tests/test_reconcile.py platform/tools/pan_update/tests/test_config.py -q; echo "rc=$?"

echo; echo "### 源码还原校验（对备份逐字节 cmp，必须全 OK）"
for f in "$IM" "$RC" "$CFG"; do
  cmp -s "$f" "/tmp/opencode/$(basename "$f")".bak && echo "OK  $(basename "$f")" || echo "DIFF!! $f"
done
