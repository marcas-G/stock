# R30 task3b 证据：quark_client `get_stoken` 缓存路径补 `_lock`/`_state`

- 背景：Plan P T3 复查发现 diff 外生产阻断——`get_stoken()` 默认 `cache=True` 引用
  `_lock`/`_state`，但二者从未定义（R16 合并遗留）；实测 `NameError: name '_lock' is not defined`。
  影响 share `_default_listdir`、sync `list_urls`/重取链、T10 真跑。
- 修复：`platform/tools/quark_download/quark_client.py` 模块级补
  `_lock = threading.Lock()`、`_state: dict = {"stoken": None, "ts": 0.0}`（`import threading` 原有）。
  对外语义不变：`cache=False` 每次现取、不读写缓存；`cache=True` ttl 内命中；`force=True` 强制现取。
- 测试：`platform/tools/quark_download/tests/test_quark_client_stoken.py`（离线 monkeypatch `http`）。

## 复现命令与结果

| 证据 | 命令 | 结果 |
|---|---|---|
| `01-red.txt` | `platform/.venv/bin/python -m pytest platform/tools/quark_download/tests/test_quark_client_stoken.py -q`（HEAD 版 quark_client） | **3 failed**：`NameError: name '_lock' is not defined`（缓存路径）+ `_state` 属性缺失（cache=False 断言） |
| `02-green.txt` | 修复后同上 + quark_download 全量 + pan_update 全量 + G-TOPO | stoken **3 passed**、quark_download **12 passed**、pan_update **40 passed**、G-TOPO 0 |
| `03-tools-suite.txt` | `platform/.venv/bin/python -m pytest platform/tools -q` | **384 passed**（上轮 381 + 3） |
| `04-mutation.txt` | `python3 governance/evidence/verification/R30/task3b-quark-stoken/mutation.py` | **4/4 突变被抓**；恢复后 12 passed，文件 sha 与修复版一致 |

## 突变清单（04）

1. 去掉 `_lock` 定义 → NameError 复发；
2. 去掉 `_state` 定义 → 缓存状态不存在（测试不注入 state，定义缺失必红）；
3. `cache=True` 不查缓存 → 两次调用打两次 http；
4. `cache=False` 也写缓存 → 语义改变。

## 备注

- 测试 fixture 逐测试清空 `_state`（缺失时不注入，避免掩盖定义缺失）；红灯即用户报告场景的本机复现。
- `mutation.py` 带 `try/finally` 恢复（上一版因目标缩进写错中途 assert 导致未恢复，已修正并重跑）。
