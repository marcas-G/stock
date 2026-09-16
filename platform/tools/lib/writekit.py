"""研究侧写侧单点（R4b）：完成标记 / 单写者锁 / 断点 state / 原子落盘。

收敛前的四套重复（R0 基线实测）：
- **parquet writer**：`convert_tick_to_parquet.MonthWriter`（被 extract_sz_cancels 复用）、
  `convert_minutes_to_parquet` 自带一份、`run_lob_batch` worker 直写、`compact_lob` 重打包；
- **flock 单写者**：convert_tick / extract_sz_cancels / run_lob_batch / compact_lob 各一份
  （`convert_minutes` 干脆没有）；
- **`_SUCCESS` 事务标记**：4 处写、1 处读（ch_ingest 灌库时跳过无标记分区）；
- **state 断点**：3 种形态（lob `_batch/state.json`、1m `output/state.json`、
  ch_ingest 的 **`state.json/` 目录** 装 119 个 `.done` 空文件、convert_minutes 的
  `_state/…/_conversion.json`）。

本模块给这四件事各**一份**实现；各工具只提供"任务/worker/输出目录"。
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq

SUCCESS_MARKER = "_SUCCESS"
_TMP_SEQ = 0        # MonthWriter 的同进程 tmp 路径唯一化（pid+序号，永不共享路径）


# ── 完成标记（_SUCCESS）────────────────────────────────────────────
def success_marker(dir_path: str | Path, name: str = SUCCESS_MARKER) -> Path:
    """该目录的完成标记路径（`name` 支持不同**粒度**的标记，见下）。"""
    return Path(dir_path) / name


def has_success(dir_path: str | Path, name: str = SUCCESS_MARKER) -> bool:
    """分区是否已完成（标记存在即可消费）。"""
    return success_marker(dir_path, name).is_file()


def mark_success(dir_path: str | Path, name: str = SUCCESS_MARKER,
                 payload: dict | None = None) -> Path:
    """写完成标记（**必须在数据落盘之后**调用）。

    粒度差异用 `name` 表达、附加信息用 `payload`，都不是第二套机制：
    - 分区级：默认 `_SUCCESS` + 空文件（与历史字节一致）；
    - 月级（跨 run 月门）：`SUCCESS_<YYYYMM>` + JSON 回执（`run_lob_batch` 的
      `{month, n_code_day, gate, parity_ok, hard_days}`，人读；消费侧只判存在性）。
    """
    p = success_marker(dir_path, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    if payload is None:
        p.write_bytes(b"")
    else:
        tmp = p.with_name(p.name + f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1),
                       encoding="utf-8")
        os.replace(tmp, p)
    return p


# ── 断点 state（统一 JSON 单文件）──────────────────────────────────
def state_path(dir_path: str | Path, name: str = "state.json") -> Path:
    return Path(dir_path) / name


def load_state(dir_path: str | Path, name: str = "state.json") -> dict:
    """读断点 JSON；不存在/空 → {}（首次运行）。"""
    p = state_path(dir_path, name)
    if not p.is_file():
        return {}
    text = p.read_text(encoding="utf-8").strip()
    return json.loads(text) if text else {}


def save_state(dir_path: str | Path, state: dict, name: str = "state.json") -> None:
    """原子写断点 JSON（tmp + os.replace；崩溃不会留半截文件）。"""
    p = state_path(dir_path, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2),
                   encoding="utf-8")
    os.replace(tmp, p)


def migrate_legacy_done_dir(path: str | Path, *, aside_suffix: str = ".legacy-20260915") -> dict:
    """把**旧形态的 state 目录**（`state.json/` 里一堆 `<key>.done` 空文件）迁移为 JSON。

    返回迁移出的 {key: True}（供调用方核对）；目录整体改名为 `<name><aside_suffix>/`
    留档（30 天内可回溯，之后按 `governance/workspace/archive-policy.md` 清理），JSON 由其后的 save 写出。

    只在"目标路径当前是目录"时动作；已是 JSON 文件 → 返回 {}（幂等）。
    """
    target = Path(path)
    if not target.is_dir():
        return {}
    migrated: dict = {}
    for f in sorted(target.glob("*.done")):
        migrated[f.stem] = True
    aside = target.with_name(target.name + aside_suffix)
    os.replace(target, aside)
    return migrated


# ── 单写者锁（flock，NB + 约定退出码）──────────────────────────────
class LockBusy(RuntimeError):
    """锁被占用（同一目录已有写者在跑）。退出码约定：调用方捕获取 3。"""


class FileLock:
    """flock 单写者门（NB：占用即抛 LockBusy，不等待）。

    **生命周期 = 对象生命周期**：内部持文件对象（不是裸 fd），引用消失 → 文件对象
    回收 → fd 关闭 → 锁自动释放（CPython 引用计数）。与四个工具的历史实现
    （`lock_f = open(lock_path)` 的局部变量）语义一致，`main()` 返回即放锁。

    用法：短临界区 `with FileLock(dir / "_batch" / ".lock"): ...`；
    长任务整程持有见 `acquire_lock()`。
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._f = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        f = open(self.path, "a")
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            f.close()
            raise LockBusy(f"已有写者持有锁: {self.path}") from exc
        self._f = f
        return self

    def release(self) -> None:
        """显式释放（幂等）。`with` 用法无需调用；`acquire_lock` 的调用方可选调用。"""
        if self._f is not None:
            fcntl.flock(self._f.fileno(), fcntl.LOCK_UN)
            self._f.close()
            self._f = None

    def __exit__(self, *exc) -> None:
        self.release()


def acquire_lock(path: str | Path) -> FileLock:
    """取锁并**保持持有**：调用方把返回值绑到活得足够久的变量上。

    长任务（批算 / 转换 / 抽取）在 `main()` 开头取一把整程持有——绑到函数局部变量即可，
    函数返回（或进程退出）时自动释放；短临界区用 `with FileLock(...)`。
    占用 → `LockBusy`（调用方打印并退出，惯例退出码见各工具）。
    """
    lock = FileLock(path)
    return lock.__enter__()


class MonthWriter:
    """每月每表一个 parquet 文件，累积到一定行数 flush 一个 row group（流式，不整月驻留）。

    R9：本类原在 `converters/convert_tick_to_parquet.py`，是工具侧**第二套**
    落盘实现（writekit 已有 `atomic_write_df`）。移入此处后研究侧只有一个写模块，三项能力
    **一项不少**——它们都来自真实事故：
    ① 唯一 tmp 路径 + fsync + `os.replace` 原子提交（多实例 O_TRUNC 互踩，4 进程毁数据事故）；
    ② 追加前 size 单调性（外部截断瞬间抓，写后检查看不到——稀疏恢复）；
    ③ 提交前物理块完整性 `st_blocks*512 >= size*0.95`（100% blocks 丢失事故）。
    

    2026-08-26 互踩修复: 写入唯一 tmp 路径 (part-000.parquet.tmp.<pid>), 全部写完后
    校验通过才 os.replace 到最终路径。write_table 返回时 pyarrow 内部缓冲可能尚未
    全部落 fd (实测误报), 因此 append 只做尺寸单调性检查 (外部 O_TRUNC 必致 size
    骤降); 文件静止期 (writer.close() + fsync 后) 做物理块完整性校验
    (st_blocks*512 >= st_size*0.95) 与结构/行数校验, 通过才原子提交——
    防止多实例并发写同一路径时静默毁数据 (4 进程互踩事故, 100% blocks 丢失)。"""

    def __init__(self, base, name, y, m, schema, use_dictionary=("code", "order_type", "bs")):
        """`schema` **必填**：写入契约由调用方显式给出（不从模块全局 SCHEMAS 隐式取，
        WS6c 起抽取类工具就按这个口径调用）。`use_dictionary` 与历史默认一致——
        换值会改 parquet 字节布局，批算产物即不可比对。
        """
        # 懒导入：writekit 其余部分**不依赖平台**（模块级不 import factorlab），
        # 分区规则仍取单点；调用方在此之前都已 `_env.ensure_platform()`。
        from factorlab.core.factio.partitions import partition_dir
        self.name = name
        self.schema = schema
        ddir = str(partition_dir(Path(base), table=name, year=int(y), month=int(m)))
        os.makedirs(ddir, exist_ok=True)
        self.final_path = os.path.join(ddir, 'part-000.parquet')
        # 唯一 tmp 路径: pid+序号, 任何来源的重复创建都绝不共享路径 (O_TRUNC
        # 互踩的物理前提是共享路径); 校验通过后 os.replace 原子提交
        global _TMP_SEQ
        _TMP_SEQ += 1
        self.path = os.path.join(ddir, f'part-000.parquet.tmp.{os.getpid()}.{_TMP_SEQ}')
        self.writer = pq.ParquetWriter(self.path, self.schema,
                                       compression='zstd', compression_level=3,
                                       use_dictionary=list(use_dictionary))
        self.rows = 0
        self.max_size = os.path.getsize(self.path)  # 活跃写者文件只增不减

    def append(self, tab):
        if tab.num_rows:
            # 截断检测必须在 write_table 之前 (2026-08-26 漏洞实测): 外部 O_TRUNC
            # 后受害进程 fd offset 不变, 继续写会让 size 从 0 恢复到 >= 原大小 —
            # 写后检查完全看不到异常 (事故精确模式: 截断到 1024/5532 后 1 秒内
            # 稀疏恢复到 1.6GB). 单写者下写前 size 必须 == max_size, 截断必破坏
            # 该等式, 且此刻尚未写新数据, 检测无竞态.
            st = os.stat(self.path)
            if st.st_size != self.max_size:
                # 触发即取证: 列出所有转换相关进程 + 持有本文件 fd 的进程 + 锁状态
                import subprocess
                diag = [f'  本进程 pid={os.getpid()}']
                try:
                    out = subprocess.run(
                        ['ps', '-eo', 'pid,ppid,etime,cmd'], capture_output=True,
                        text=True, timeout=10).stdout
                    for line in out.splitlines():
                        if 'convert_tick' in line or 'spawn_main' in line:
                            diag.append('  ' + line.strip())
                except Exception as e:
                    diag.append(f'  ps 失败: {e}')
                diag.append(f'  持有 {self.path} fd 的进程:')
                # R9 修复：原写法 `sorted(os.listdir('/proc'), key=int)` 在存在非数字
                # 条目（如 /proc/fb）时**先崩在排序**——诊断路径本身反而掩盖了原始错误
                # （2026-08-26 事故复盘时未触发，故一直没暴露）。
                for p in sorted((e for e in os.listdir('/proc') if e.isdigit()), key=int):
                    try:
                        for fd in os.listdir(f'/proc/{p}/fd'):
                            tgt = os.readlink(f'/proc/{p}/fd/{fd}')
                            if self.path.split('/')[-1] in tgt:
                                diag.append(f'    pid {p}: fd {fd} → {tgt}')
                    except OSError:
                        pass
                raise RuntimeError(
                    f'文件被外部截断/修改! {self.path} size={st.st_size} 期望={self.max_size} '
                    f'(pid={os.getpid()}) — 诊断:\n' + '\n'.join(diag))
            self.writer.write_table(tab)
            self.rows += tab.num_rows
            self.max_size = os.path.getsize(self.path)

    def close(self):
        self.writer.close()  # 所有内部缓冲落 fd, 文件此刻静止
        fd = os.open(self.path, os.O_RDONLY)
        try:
            os.fsync(fd)     # 强制写回 → st_blocks 反映真实物理分配
        finally:
            os.close(fd)
        # 提交校验: 结构可读 + schema 一致 + 行数一致 + 物理分配完整
        pf = pq.ParquetFile(self.path)
        if pf.schema_arrow != self.schema:
            raise RuntimeError(f'schema 不一致: {self.path}')
        if pf.metadata.num_rows != self.rows:
            raise RuntimeError(f'行数不一致: {self.path} '
                               f'metadata={pf.metadata.num_rows} 期望={self.rows}')
        if not blocks_complete(self.path):
            st = os.stat(self.path)
            got = st.st_blocks * 512
            raise RuntimeError(
                f'提交前稀疏化检测失败: {self.path} size={st.st_size} '
                f'blocks={st.st_blocks} (仅 {got/st.st_size:.1%} 物理分配) → '
                f'文件有空洞, 放弃提交, 该月将重转')
        os.replace(self.path, self.final_path)
        dfd = os.open(os.path.dirname(self.final_path), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
        return self.final_path, self.rows


# ── 物理块完整性（提交前判据；R9 从 MonthWriter.close 抽出以便单独测）────
def blocks_complete(path: str | Path, ratio: float = 0.95) -> bool:
    """文件的**物理分配**是否与逻辑大小相符（稀疏/空洞文件判 False）。

    为什么不用 size：O_TRUNC 后继续写会让 size 从 0 稀疏恢复到原大小，写后检查完全
    看不到异常（2026-08-26 事故精确模式：blocks 100% 丢失而 size 毫发无损）。
    """
    st = os.stat(path)
    if st.st_size == 0:
        return True
    return st.st_blocks * 512 >= st.st_size * ratio


# ── 原子落盘（parquet）────────────────────────────────────────────
def atomic_write_df(df: pl.DataFrame, path: str | Path) -> Path:
    """DataFrame → parquet 原子落盘（同目录 tmp + fsync + os.replace）。

    与历史实现同语义（`MonthWriter`/`_atomic_write_df`）：写失败不留半截文件、
    不破坏既有文件；**校验留给调用方**（如行数/摘要对账后再 os.replace 的场景用
    `atomic_replace_with_validation`）。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp", prefix=f".{p.name}.")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        df.write_parquet(tmp)
        with open(tmp, "rb") as f:
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return p


def atomic_write_bytes(data: bytes, path: str | Path) -> Path:
    """任意字节内容原子落盘（manifest 等小文件用）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp", prefix=f".{p.name}.")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_bytes(data)
        with open(tmp, "rb") as f:
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return p
