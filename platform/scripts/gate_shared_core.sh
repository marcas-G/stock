#!/bin/bash
# 共享核单副本门（DER-002 / REQ-Q-008 / REQ-Q-010）——CI-less 环境的手工门。
# 用法：在 quant-platform-main 工作树内 `bash scripts/gate_shared_core.sh`（退出码 0 = 全过）。
set -u
REPO=$(git rev-parse --show-toplevel)
RESEARCH="$REPO/../quant-platform-research"
fail=0

# 1) research 分支不得携带平台 src/tests（物理单副本）
if git -C "$REPO" ls-tree -r research --name-only | grep -qE '^(src|tests)/'; then
    echo "FAIL: research 分支仍携带平台 src/tests 副本"; fail=1
else
    echo "OK: research 分支无平台副本"
fi

# 2) research 树不得含平台手册（同一漂移类）
if git -C "$REPO" ls-tree -r research --name-only \
        | grep -qE '(^|/)(interface|catalog|data-ops-playbook|teajoin-guide)\.md$'; then
    echo "FAIL: research 分支仍携带平台手册副本"; fail=1
else
    echo "OK: research 分支无平台手册副本"
fi

# 3) 研究侧平台路径注入必须收敛到 tools/_env.py 单点
#    模式在"行内容"内锚定（sys.path.insert(... 与 quant-platform 同一行）——
#    否则 grep -rn 的文件路径前缀会造成全线假阳性。
#    豁免：_env.py 单点本身 + notes/ 历史诊断（与 pytest 门一致）。
if [ -d "$RESEARCH/tools" ] && grep -rEn --include=*.py \
        "sys\.path\.insert\(.*quant-platform" "$RESEARCH/tools" 2>/dev/null \
        | grep -v "/_env\.py:" | grep -v "/notes/" | grep -q .; then
    echo "FAIL: 研究侧存在直写平台路径的 sys.path 注入（应走 _env.py）"; fail=1
else
    echo "OK: 研究侧平台路径注入收敛到 _env.py"
fi

exit $fail
