"""pan_update：夸克网盘数据自动更新链（Plan P）。

设计：knowledge/design/workspace/2026-09-16-pan-data-update-design.md
架构：config（单点常量/类别映射）→ share（分享树遍历）→ state（状态差集）
     → sync（下载）→ stages（阶段编排）→ verify（对账）→ cli（入口）。
"""
