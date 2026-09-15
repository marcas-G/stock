# R16 `quark_download` 三个入口的传输层收成单点（2026-09-15）

用户指令："拆"。本轮最后一处"同一件事多份实现"：`quark_download_v2` 与
`quark_download_server` **各写了一份几乎逐字的传输/鉴权/下载层**（`http` / `get_stoken` /
`get_download_urls` / `download_file` / `UA` / `HOST_PC` / `PWD_ID` / cookie 读取），
`quark_share` 又各自留了一份 `UA`。现收进工具内的 `quark_client.py`（同目录共享模块，
G-TOPO 允许同工具内引用），三个入口只 import。

| 对象 | 合并口径（逐条写明，避免静默漂移） |
|---|---|
| `http` | **HTTP 错误也返回、不抛**：401/403 立即返回给调用方触发 stoken 刷新；其它异常按 2s/4s/6s 退避重试；最终返回 `last or (0, None)` |
| `get_stoken` | v2 口径（`_lock` + 缓存 + `STOKEN_TTL`）为缺省；server 口径以 `get_stoken(cache=False)`（每次现取）保留；两者都传 `PASSCODE`，失败 `assert`（不吞） |
| `get_download_urls` | 50/批、`dl-guest` 降级链接丢弃、批间 0.3s 节流；**诊断打印改为 `log` 回调**（缺省静默，server 传 `print`） |
| `download_file` | 逐字相同（含 cookie 单点） |
| cookie | 单一口径：`QUARK_COOKIE_FILE`（缺省 `/tmp/quark_cookies.txt`）→ 工具目录旁 `../quark_cookies.txt` 回退 → 都缺则 **FileNotFoundError**（server 原先 import 期读文件、缺失**静默空串**——空 Cookie 会被上游当 401，掩盖真因） |

结果：`quark_download_server.py` −89 行、`quark_download_v2.py` −86 行、`quark_share.py` −2 行；
**cookie 常量在三个入口里各有一份 → 现在只有 `quark_client` 一处理解它**。

## 等价性证据（可复跑）

`docs/verification/R16/quark-client-parity.py`（输出 `quark-client-parity.txt`）：
- `http` / `download_file` 与**原实现逐字相同**（归一化后，仅 cookie 来源与文档串差异）；
- `get_download_urls` / `get_stoken` 是声明过的重构（日志回调、`_fetch_stoken` 抽取、cache 参数），
  以**行为清单**核对（50/批、fid→url、dl-guest 丢弃、0.3s 节流；锁、缓存、TTL、口令、assert、取 stoken）。

## 执行中的偏差与抓回（本轮 4 处，全部由"对照/真跑"抓回）

| 偏差 | 暴露方式 | 处置 |
|---|---|---|
| 共享 `http` 被我"顺手简化"成**重试后抛异常** ✗ 夺走了调用方的 401→刷新 stoken 链路 | 逐段 diff 核对（`git diff HEAD`） | 从 HEAD 逐字取回原实现（含 HTTPError 分支与退避） |
| 共享 `UA` 我**手打**成 `Chrome/122.0`，原版是 `Chrome/151.0.0.0` ✗ | 同上 | 逐字回填；并写进对照脚本（`UA` 属"逐字相同"类） |
| 用"到最近空行"切片回填 UA 时**连带删掉了 `PWD_ID`/`PASSCODE`/`STOKEN_TTL`** | 身份断言测试（`getattr(QC, "HOST_PC")` 类）失败 | 常量块按清单重建；对照脚本改为按 AST 取函数体而非文本切片 |
| 新测试在同进程 `import quark_client`，但只在子进程代码串里插了路径 → 从仓库根跑全量时 `ModuleNotFoundError` | 全量 pytest（单跑工具目录时看不出） | 测试模块级补 `sys.path.insert`（与 converters 测试同款），双向各跑一遍确认 |

## 验证（真跑）

| 项 | 结果 | 证据 |
|---|---|---|
| 共享层与原实现对照 | **PASS**（逐字对照 + 行为清单） | `quark-client-parity.txt` |
| 研究侧全量（emb） | **233 passed / 3 skipped**（+2 共享层测试） | `research-emb.log` |
| T1 集合 | **42 passed** | `research-t1.log` |
| 门（结构 + 拓扑 + 数据接口 + 自检） | **全绿** | `gates.log` |
| 三个入口"同一实现"身份断言 | `http`/`get_stoken`/`get_download_urls`/`download_file`/`UA`/`HOST_PC`/`PWD_ID`/`STOKEN_TTL` 在两个入口上是**同一个对象**；`quark_share.UA` 同源 | `tests/test_quark_download.py` |

## 收尾：全树重复实现/同值常量扫描（自动，非人工点数）

| 扫描 | 判据 | 结果 |
|---|---|---|
| 重复实现 | 模块级函数按 AST 归一化后**结构相同**（≥6 行体；排除 tests/notes/diag） | **0 组**（R8–R16 前是 4 类：4 份 flock、4 份 writer、4 份 `_SUCCESS`、3 份编排循环） |
| 同值常量 | 模块级字符串常量同值出现在 **≥2 个模块** | 扫描发现 3 组 → 全部收口：`'quark_downloaded'`（DEST 收进 `quark_client`）、`'panel.parquet'`/`'summary.json'`（`parquet_artifacts` 改引 `results_fs` 的布局单点）→ **复扫 0 组** |

## 仍未做（不静默）

- 三个入口的**改名**（R4 #12②：`quark_download_v2.py`→`download_level2.py` 等）仍阻塞于用户级技能
  副本 `~/.claude/skills/quark-share-download/scripts/` 的同名副本；**本轮后同步技能需拷 2 个文件**
  （脚本 + `quark_client.py`），已在 #12② 补记；
- `quark_share.py` 的 `http_json/list_dir/walk/search` 面与前者不同（不同 API 族），本轮只共用 `UA`，
  未强行归并——归并需要真实网盘联调，属专项。
