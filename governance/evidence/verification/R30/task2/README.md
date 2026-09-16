# R30 Task2 证据 — pan_update 分享树遍历（share.py）

- 任务：`.superpowers/sdd/2026-09-16-pan-data-update-plan/task-2-brief.md`
- 代码提交：`d60e5eb feat(tools): pan_update 分享树遍历与类别发现`（`share.py` + `tests/test_share.py`）
- 运行环境：`platform/.venv`（Python 3.13）
- 测试面：brief 逐字 2 条 + 增补守卫 5 条（元数据保真/目录项排除/find_dir 成功+拒绝文件/生产分页与字段映射/失败上抛/walk_dir 前缀）

| 文件 | 内容 | 命令（按原样执行） |
|---|---|---|
| `01-red.txt` | 测试先红：`ImportError: cannot import name 'share' from 'pan_update'`（功能缺失，非笔误） | `platform/.venv/bin/python -m pytest platform/tools/pan_update/tests/test_share.py -q` |
| `02-green.txt` | 实现后全绿：单文件 7 passed + `pan_update/tests` 14 passed | 同上；`platform/.venv/bin/python -m pytest platform/tools/pan_update/tests -q` |
| `03-gtopo.txt` | G-TOPO 0 处（`from quark_download import quark_client` 未触发 R3 跨工具判定） | `platform/.venv/bin/python governance/ops/check_tool_layering.py` |
| `04-mutation.txt` | 7 种存根突变逐一被测试抓住；恢复原文后复跑 14 passed | `python3 governance/evidence/verification/R30/task2/mutation.py` |
| `05-tools-suite.txt` | 回归：`platform/tools` 全量 355 passed（58.69s） | `platform/.venv/bin/python -m pytest platform/tools -q` |

脚本：`mutation.py`（复现 04；含修复轮后共 9 类突变；跑完 try/finally 还原并断言逐字节一致）。

## 修复轮 1（独立评审 I1/I2；代码提交 `413930d fix(tools): Plan P T2 分页按 _total 终止 + 脚本直启导入兜底`）

- **I1 分页静默截断**：`_default_listdir` 解析 `metadata._total`（int）→ 已收 ≥ total 才终止（短页也继续翻）；无 total/非 int → 保持短页终止 + `_MAX_PAGES` 兜底。
- **I2 脚本直启导入**：`from quark_download import quark_client` 包 try/except `ModuleNotFoundError` → 兜底插入 `platform/tools` 再导入。

| 文件 | 内容 | 命令（按原样执行） |
|---|---|---|
| `05-fix-round1-redgreen.txt` | 红：新增 3 测试中 I1(`_total=250` 短页翻满)/I2(裸跑直启) 2 failed（exit 1）；绿：10 passed；裸场景两命令 exit 0；pan_update 17 passed | `platform/.venv/bin/python -m pytest platform/tools/pan_update/tests/test_share.py -q` 等（文件内逐条） |
| `06-fix-round1-mutation.txt` | 9 种存根突变全被抓（新增"忽略 metadata._total"/"去直启兜底"）；恢复后 17 passed | `python3 governance/evidence/verification/R30/task2/mutation.py` |
| `07-fix-round1-regression.txt` | 回归：`platform/tools` 全量 358 passed（55.54s）；G-TOPO 0 处 | `platform/.venv/bin/python -m pytest platform/tools -q`；`platform/.venv/bin/python governance/ops/check_tool_layering.py` |

## 裁决（brief 内部冲突；按 spec + TDD“测试逐字为准”处理）

1. **`find_dir` 签名**：brief 接口行 `find_dir(root_fid, name) -> str` 与 brief 逐字测试 `find_dir(fake_listdir, "root", "不存在")`（3 参、首位为注入传输）互斥。
   spec §3 未定义 find_dir 签名，测试是唯一可执行需求 → 实现 `find_dir(listdir, root_fid, name)`；生产调用传 `_default_listdir`。
2. **FAKE_TREE 与 fake_listdir 互斥**：brief 文件项为平铺三元组 `("x.zip", "f1", 10)`，但 fake_listdir 逐字代码 `for name, fid2 in ...` + `isinstance(fid2, tuple)` 要求嵌套二元组（平铺必然 `ValueError: too many values to unpack`，测试无法运行）。
   最小化解：仅把 FAKE_TREE 文件项改为 `("x.zip", ("f1", 10))`，`fake_listdir` 与两条断言逐字保留。

接口裁决在案：`iter_category(listdir, start_fid) -> list[Entry]`（Entry dataclass：name/size/fid/fid_token/rel_path/is_dir），T3 在消费边界 `dataclasses.asdict` 转换。
