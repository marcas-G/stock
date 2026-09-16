"""月分片写入骨架（R11）：把"逐单元缓冲 → 阈值 flush → 月文件 append → 收尾提交"收成单点。

**为什么**：两个 tick 转换器（`converters/convert_tick_to_parquet` 与
`lob_fact/pipeline/extract_sz_cancels`）各自的 `on_result` 里逐行重复同一套流程，且都带着
两起真实事故的修复痕迹——重复代码意味着"修好一处、另一处照样中招"。骨架把这套流程（含两条
事故回归）收进 `lib/`，工具只提供三件事：输出根、schema、flush 阈值，以及"key → (表名, 月)"
的映射。

两条事故（测试逐条锁）：
1. `setdefault(key, MonthWriter(...))` 每次 flush 都会**新建** writer 并 O_TRUNC 截断活跃
   tmp 文件（2026-08-26；稀疏恢复掩盖异常）→ 本实现用显式 `if key not in self.writers`；
2. flush 后忘记归零计数器 → 之后每个单元都 flush（行组爆炸）→ 本实现 flush 即归零。

语义与历史逐字节一致：`pa.concat_tables(缓冲)` 后 `MonthWriter.append`，收尾 flush 尾部 +
`close()` 原子提交 + 该分区落 `_SUCCESS`。
"""
from __future__ import annotations

import os
from typing import Callable

import pyarrow as pa

from lib import writekit as W


class MonthPartitionSink:
    """`key → 一个月的 parquet 分片`，按单元数阈值 flush 成 row group。

    - `base_dir`：分片根（`{base}/{表名}/year=/month=/part-000.parquet`，规则在 writekit.MonthWriter）
    - `schema_of(name)`：表名 → pyarrow schema（沿用工具自己的冻结 schema 单点）
    - `flush_units`：每累积多少个单元写一个 row group（= 历史 `FLUSH_ZIPS`）
    - `kind_of(key)`：key → (表名, 'YYYYMM')；key 由调用方定义（可带表名，也可只是月份）
    """

    def __init__(self, base_dir, *, schema_of: Callable[[str], pa.Schema],
                 flush_units: int, kind_of: Callable[[object], tuple[str, str]]):
        if flush_units < 1:
            raise ValueError(f"flush_units 必须 >= 1（收到 {flush_units}）")
        self.base_dir = str(base_dir)
        self._schema_of = schema_of
        self._flush_units = int(flush_units)
        self._kind_of = kind_of
        self.writers: dict[object, W.MonthWriter] = {}
        self.buffers: dict[object, list] = {}
        self.n_buf: dict[object, int] = {}
        self._closed = False

    def add(self, key, table) -> None:
        """缓冲一个单元；该 key 累积到 `flush_units` 就写一个 row group。"""
        if self._closed:
            raise RuntimeError("sink 已收尾（close_all 之后不得再 add）")
        self.buffers.setdefault(key, []).append(table)
        self.n_buf[key] = self.n_buf.get(key, 0) + 1
        if self.n_buf[key] >= self._flush_units:
            self._flush(key)

    def _writer(self, key) -> W.MonthWriter:
        # 事故 1 的修法：显式 if（绝不用 setdefault —— 那会每次新建 writer 并截断活跃 tmp）
        if key not in self.writers:
            name, ym = self._kind_of(key)
            self.writers[key] = W.MonthWriter(self.base_dir, name, ym[:4], ym[4:],
                                              schema=self._schema_of(name))
        return self.writers[key]

    def _flush(self, key) -> None:
        tabs = self.buffers.get(key)
        if not tabs:
            return
        self._writer(key).append(pa.concat_tables(self.buffers.pop(key)))
        self.n_buf[key] = 0            # 事故 2 的修法：flush 即归零

    def close_all(self, *, mark_success: bool = True) -> dict[object, tuple[str, int]]:
        """flush 尾部缓冲 → 逐 writer `close()` 原子提交 → 落 `_SUCCESS`。

        返回 `{key: (最终路径, 行数)}`。重复调用显式报错（静默重复提交会掩盖状态机错误）。
        """
        if self._closed:
            raise RuntimeError("close_all 只能调用一次（重复提交等于把状态机错误藏起来）")
        self._closed = True
        for key in list(self.buffers):
            self._flush(key)
        out: dict[object, tuple[str, int]] = {}
        for key, w in sorted(self.writers.items(), key=lambda kv: str(kv[0])):
            path, rows = w.close()
            out[key] = (path, rows)
            if mark_success:
                W.mark_success(os.path.dirname(path))
        return out
