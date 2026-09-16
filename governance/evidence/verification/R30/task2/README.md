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

脚本：`mutation.py`（复现 04；7 类突变：iter_category 空存根 / `_walk` 不递归 / find_dir 忽略名 / find_dir 不校验 is_dir / 去分页 / 空 stoken / walk_dir 丢 prefix；跑完 try/finally 还原并断言逐字节一致）。

## 裁决（brief 内部冲突；按 spec + TDD“测试逐字为准”处理）

1. **`find_dir` 签名**：brief 接口行 `find_dir(root_fid, name) -> str` 与 brief 逐字测试 `find_dir(fake_listdir, "root", "不存在")`（3 参、首位为注入传输）互斥。
   spec §3 未定义 find_dir 签名，测试是唯一可执行需求 → 实现 `find_dir(listdir, root_fid, name)`；生产调用传 `_default_listdir`。
2. **FAKE_TREE 与 fake_listdir 互斥**：brief 文件项为平铺三元组 `("x.zip", "f1", 10)`，但 fake_listdir 逐字代码 `for name, fid2 in ...` + `isinstance(fid2, tuple)` 要求嵌套二元组（平铺必然 `ValueError: too many values to unpack`，测试无法运行）。
   最小化解：仅把 FAKE_TREE 文件项改为 `("x.zip", ("f1", 10))`，`fake_listdir` 与两条断言逐字保留。

接口裁决在案：`iter_category(listdir, start_fid) -> list[Entry]`（Entry dataclass：name/size/fid/fid_token/rel_path/is_dir），T3 在消费边界 `dataclasses.asdict` 转换。
