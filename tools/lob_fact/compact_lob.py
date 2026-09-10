#!/usr/bin/env python
"""lob_fact 体积重打包 (W5 体积杠杆落地) —— 只读源 + 内容校验 + 原子替换

背景: W4 记录的 202608 体积比 1.461× 经复算为 **1.538×** (>1.5 预算; W4 memo 把
MiB 值当 MB 记 + 比值算错)。修正路径 = 纯编码杠杆 (行组几何 / zstd 级), **不改任何
语义**: 本工具按目标几何流式重写逐 date parquet, 重写前后**内容摘要** (行数 + 批级
sha256 链, 与编码无关) 必须全等才 `os.replace` 替换, 否则抛 CompactError 且原文件
与 tmp 都不留。

单写者纪律: 批算 (`run_lob_batch.py`) 持 `_batch/.lock` 独占 flock 期间拒绝运行
(退出码 3) —— 重打包与批算共享同一把锁, 不得并发写同一棵树。

CLI::

    python compact_lob.py --root /data/students/gaolei/stock/lob_fact \\
        --months 202508,202608 [--rgr 1048576] [--level 9] \\
        [--lock <_batch/.lock>] [--out compact.json] [--dry-run]

退出码: 0 = 全部成功 (或 dry-run); 3 = 批算锁被占; 1 = 有文件失败 (报告恒落盘)。
"""
import argparse
import datetime
import fcntl
import glob
import hashlib
import json
import os
import time

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

LOB_TABLES = ('lob_events', 'lob_sweep_meta', 'lob_checkpoints')
ROW_GROUP_ROWS = 1_048_576     # W4d 定标 (events 最优几何)
ZSTD_LEVEL = 9                 # 实测: events −4.8% vs level 3 (同几何)
BATCH_ROWS = 262_144           # 读取批 (内存 ~百 MB 量级)


class CompactError(RuntimeError):
    pass


# ---------- 内容摘要 (编码无关等价判据) ----------

def _canon(schema):
    """去 dictionary 化 + 去 metadata 的规范 schema (摘要只认逻辑内容)"""
    fields = [pa.field(f.name, f.type.value_type
                       if pa.types.is_dictionary(f.type) else f.type)
              for f in schema]
    return pa.schema(fields)


def _ipc_bytes(t):
    """Table → Arrow IPC stream 字节 (跨 pyarrow 版本稳定)"""
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, t.schema) as w:
        w.write_table(t)
    return sink.getvalue().to_pybytes()


def _fill_null(col):
    """null 槽 → 该类型零值 (仅摘要用): **null 槽底层值是 Arrow 未定义区**, 读回随
    编码器/路径变 (真实案例: lob_events 20260803 `id` 692 null 槽 src=243683 /
    重写件=0) → 直接哈希原始字节会误报"内容变了"。掩码另流哈希, 故 null≠零值。"""
    if col.null_count == 0:
        return col
    t = col.type
    if pa.types.is_integer(t) or pa.types.is_floating(t):
        v = 0
    elif pa.types.is_boolean(t):
        v = False
    elif pa.types.is_string(t) or pa.types.is_large_string(t):
        v = ''
    elif pa.types.is_date(t):
        v = datetime.date(1970, 1, 1)
    elif pa.types.is_timestamp(t):
        v = datetime.datetime(1970, 1, 1)
    else:                                        # 未支持类型不许静默降级
        raise ValueError(f'摘要不支持的类型 (null 填充未定义): {t}')
    return pc.fill_null(col, pa.scalar(v, type=t))


def _canon_batch(t):
    """批 → 内容判据字节 = IPC(逻辑值, null 槽填零) ⊕ IPC(null 掩码 int8)。

    只认逻辑内容: 行组几何 / 压缩级 / dictionary / **null 槽未定义残留** 全不影响;
    非 null 值变、掩码变、行数变 → 必变。"""
    names = [f.name for f in t.schema]
    vals = pa.table([_fill_null(t[i]) for i in range(t.num_columns)],
                    schema=pa.schema([pa.field(n, t.schema.field(n).type)
                                      for n in names]))
    mask = pa.table([pc.cast(pc.is_null(t[i]), pa.int8())
                     for i in range(t.num_columns)], names=names)
    return _ipc_bytes(vals) + _ipc_bytes(mask)


def file_digest(path, batch=BATCH_ROWS):
    """流式内容摘要 → (sha256 链 hex, rows)。

    同内容异编码 (行组几何/压缩级/dictionary 编码/null 槽残留) → 同摘要;
    值变、掩码变或截断 → 摘要必变。
    实现: 逐批规范化为无 dictionary 的 Table → `_canon_batch` 字节链入。"""
    f = pq.ParquetFile(path)
    sch = _canon(f.schema_arrow)
    h = hashlib.sha256()
    n = 0
    for b in f.iter_batches(batch_size=batch):
        t = pa.Table.from_batches([b]).cast(sch)
        h.update(_canon_batch(t))
        n += t.num_rows
    return h.hexdigest(), n


# ---------- 重写 (目标几何 + 压缩级; 缓冲至 rgr 整倍落组) ----------

def _rewrite(src, dst, schema, rgr, level):
    """源 → 目标文件 (行组界 = rgr 整倍, 尾组余数); 返回落盘行数。

    与批算写盘同构: 缓冲 ≥rgr 行 → 按 rgr 切组落; 余数 <rgr 留待下帧/收尾。"""
    r = pq.ParquetFile(src)
    with open(dst, 'wb') as fh:
        w = pq.ParquetWriter(fh, schema, compression='zstd',
                             compression_level=level)
        buf, buf_rows, n = [], 0, 0
        try:
            for b in r.iter_batches(batch_size=BATCH_ROWS):
                buf.append(b)
                buf_rows += b.num_rows
                if buf_rows >= rgr:
                    t = pa.Table.from_batches(buf, schema=schema)
                    buf, buf_rows = [], 0
                    off = 0
                    while off + rgr <= t.num_rows:
                        w.write_table(t.slice(off, rgr))
                        n += rgr
                        off += rgr
                    if off < t.num_rows:
                        buf = [t.slice(off).to_batches()[0]]
                        buf_rows = t.num_rows - off
            if buf_rows:
                t = pa.Table.from_batches(buf, schema=schema)
                w.write_table(t)
                n += t.num_rows
        finally:
            w.close()
            fh.flush()
            os.fsync(fh.fileno())
    return n


# ---------- 逐文件重打包 (校验 + 原子替换) ----------

def compact_file(path, rgr=ROW_GROUP_ROWS, level=ZSTD_LEVEL, _mutate=None):
    """同目录原子替换; 校验失败 → CompactError (原文件与 tmp 均不动)。

    _mutate: 测试专用故障注入钩子 (写完 tmp、校验前调用; 生产路径 None)。"""
    before = os.path.getsize(path)
    src_digest, src_rows = file_digest(path)
    schema = pq.ParquetFile(path).schema_arrow
    d = os.path.dirname(path)
    tmp = os.path.join(d, '.%s.tmp.%d' % (os.path.basename(path), os.getpid()))
    try:
        n = _rewrite(path, tmp, schema, rgr, level)
        if _mutate is not None:
            _mutate(tmp)
        try:
            dig, rows = file_digest(tmp)
        except Exception as e:                 # 重写件不可读/损坏 → 同属校验不过
            raise CompactError(f'内容校验不过 (重写件不可读): {e!r}') from e
        if rows != src_rows or dig != src_digest or n != src_rows:
            raise CompactError(
                f'内容校验不过 (原文件未替换): rows {rows}/{n} vs 源 {src_rows}, '
                f'digest {dig[:12]} vs {src_digest[:12]}')
        os.replace(tmp, path)
        dfd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return dict(path=path, rows=src_rows, digest=src_digest, digest_equal=True,
                before_bytes=before, after_bytes=os.path.getsize(path))


def plan_files(root, months, tables=LOB_TABLES):
    """[(table, path)] — 月份/表缺目录即跳过 (清单不虚构); 字典序确定。"""
    out = []
    for m in months:
        for t in tables:
            pat = os.path.join(root, t, f'year={m[:4]}', f'month={m[4:6]}', '*.parquet')
            for p in sorted(glob.glob(pat)):
                out.append((t, p))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', default='/data/students/gaolei/stock/lob_fact')
    ap.add_argument('--months', required=True, help='逗号分隔 YYYYMM')
    ap.add_argument('--tables', default=','.join(LOB_TABLES))
    ap.add_argument('--rgr', type=int, default=ROW_GROUP_ROWS)
    ap.add_argument('--level', type=int, default=ZSTD_LEVEL)
    ap.add_argument('--lock', default=None, help='批算锁文件 (占用则拒绝, 退出码 3)')
    ap.add_argument('--out', default=None, help='报告 JSON 路径')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)
    months = [m.strip() for m in a.months.split(',') if m.strip()]

    locked = False
    lf = None
    if a.lock:
        os.makedirs(os.path.dirname(a.lock), exist_ok=True)
        lf = open(a.lock, 'a')
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(f'批算实例持锁 ({a.lock}) → 拒绝重打包 (单写者纪律)', flush=True)
            rep = dict(dry_run=a.dry_run, locked=True, months=months,
                       n_files=0, bytes_before=0, bytes_after=0, files=[],
                       generated_at=time.strftime('%Y-%m-%d %H:%M:%S'))
            if a.out:
                with open(a.out, 'w') as f:
                    json.dump(rep, f, ensure_ascii=False, indent=1)
            return 3

    files = plan_files(a.root, months, tuple(a.tables.split(',')))
    recs, nb, na, n_fail = [], 0, 0, 0
    for t, p in files:
        sz = os.path.getsize(p)
        nb += sz
        if a.dry_run:
            print(f'[dry-run] {t} {p} {sz / 2**20:.1f}MiB', flush=True)
            recs.append(dict(table=t, path=p, before_bytes=sz, after_bytes=None,
                             rows=None, digest_equal=None))
            continue
        try:
            info = compact_file(p, rgr=a.rgr, level=a.level)
            na += info['after_bytes']
            recs.append(dict(table=t, path=p, before_bytes=info['before_bytes'],
                             after_bytes=info['after_bytes'], rows=info['rows'],
                             digest_equal=info['digest_equal']))
            print(f'{t} {os.path.basename(p)} {info["before_bytes"] / 2**20:.1f}→'
                  f'{info["after_bytes"] / 2**20:.1f}MiB '
                  f'({(info["after_bytes"] - info["before_bytes"]) / info["before_bytes"] * 100:+.1f}%) '
                  f'rows={info["rows"]} 摘要等={info["digest_equal"]}', flush=True)
        except Exception as e:                       # 失败不静默: 记录 + 继续其它文件
            n_fail += 1
            na += sz
            recs.append(dict(table=t, path=p, before_bytes=sz, after_bytes=sz,
                             rows=None, digest_equal=False, error=repr(e)[:300]))
            print(f'{t} {os.path.basename(p)} 失败: {e!r}', flush=True)
    rep = dict(dry_run=a.dry_run, locked=locked, months=months,
               rgr=a.rgr, level=a.level, n_files=len(recs), n_fail=n_fail,
               bytes_before=nb, bytes_after=na if not a.dry_run else None,
               files=recs, generated_at=time.strftime('%Y-%m-%d %H:%M:%S'))
    print(f'合计 {len(recs)} 文件, {nb / 2**20:.1f}MiB'
          + (f' → {na / 2**20:.1f}MiB ({(na - nb) / nb * 100:+.1f}%)'
             if not a.dry_run and nb else '') + f' 失败 {n_fail}', flush=True)
    if a.out:
        with open(a.out, 'w') as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
        print(f'report -> {a.out}', flush=True)
    if lf is not None:
        fcntl.flock(lf, fcntl.LOCK_UN)
        lf.close()
    return 1 if n_fail else 0


if __name__ == '__main__':
    raise SystemExit(main())
