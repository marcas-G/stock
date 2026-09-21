"""挖矿作业服务面（surfaces 层）：SQLite 队列 + 子进程 runner + FastAPI。

分层遵循 `surfaces → app → ports → core`：本包只做服务运营（作业记录/校验/进程编排/
HTTP 契约），计算全部委托镜像内的 `factorlab` CLI（固定命令集，规格 §4）。
"""
