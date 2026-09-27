"""仓库中已发现的工具拓扑回归锁定。"""

import check_tool_layering


def test_ashare_ingest_check_inputs_does_not_import_colliding_module():
    """数据自检脚本的契约模块名不得与其他工具同名。"""
    findings = [
        finding for finding in check_tool_layering.check()
        if finding.startswith("platform/tools/ashare_ingest/check_inputs.py:")
    ]
    assert findings == []
