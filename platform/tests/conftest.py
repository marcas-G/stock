import os
from pathlib import Path

import pytest

# 平台库（main 工作树 data/factorlab.duckdb，只读引用——M4a 起唯一数据源；
# 旧只读库（date/code 列）已废弃：无 stock_basic/adj_factor 等平台表）。
# 默认按本文件相对定位到工作树（移动/换机不再失效）；FACTORLAB_REAL_DB 可覆盖。
REAL_DB = os.environ.get(
    "FACTORLAB_REAL_DB",
    str(Path(__file__).resolve().parents[1] / "data" / "factorlab.duckdb"),
)


@pytest.fixture
def real_db_path():
    if not os.path.exists(REAL_DB):
        pytest.skip(f"真实数据库不存在: {REAL_DB}")
    return REAL_DB


# ================================================================
# CH 后端 fixtures（读路径双后端：ch 腿共用设施）
#   ch_client：真 CH 客户端（不可达 → 集成测试整体 skip）
#   ch_db：    临时库 factorlab_test_<uuid>（monkeypatch settings.ch_database），teardown DROP
#   ch_prod：  生产库（缺表/空 → skip）；e2e 只读消费
#   duckdb_rd / ch 文件内参数化用 data_backend fixture 由各测试文件自行组织
# ================================================================
import uuid

from factorlab.config import settings
from factorlab.adapters import ch_read


@pytest.fixture(scope="session")
def ch_client():
    """真 CH client；CH 不可达时整个测试 skip（ch 腿集成入口）。"""
    try:
        return ch_read.get_client()
    except RuntimeError:
        pytest.skip("ClickHouse 不可达（ch 腿跳过）")


@pytest.fixture()
def ch_db(ch_client, monkeypatch):
    """临时库 factorlab_test_<uuid>：monkeypatch settings.ch_database 后 yield (client, db)。

    client 单例默认库快照无关紧要——读路径 SQL 全部带 {settings.ch_database}. 前缀，
    查询实际落在临时库。teardown 用 SYNC 确保立即回收。
    """
    db = f"factorlab_test_{uuid.uuid4().hex[:8]}"
    ch_client.command(f"CREATE DATABASE IF NOT EXISTS {db}")
    monkeypatch.setattr(settings, "ch_database", db)
    yield ch_client, db
    ch_client.command(f"DROP DATABASE IF EXISTS {db} SYNC")


@pytest.fixture(scope="session")
def ch_prod(ch_client):
    """真 CH 生产库（settings.ch_database 快照）；daily 缺失/为空 → skip。"""
    db = settings.ch_database
    tables = {r[0] for r in ch_client.query(
        "SELECT name FROM system.tables WHERE database = %(db)s",
        parameters={"db": db}).result_rows}
    if "daily" not in tables:
        pytest.skip(f"生产库 {db} 无 daily 表（e2e 跳过）")
    n = ch_client.query(f"SELECT count() FROM {db}.daily").result_rows[0][0]
    if not n:
        pytest.skip(f"生产库 {db}.daily 为空（e2e 跳过）")
    return ch_client


# ================================================================
# env：双腿参数化环境（duckdb 平台文件 | CH 临时库），参数化文件共用
#   测试体：env.seed({"table": ([(col, kind), ...], [rows...])})  → env.rd 被测
#   duckdb 文件每测试新建（tmp_path 函数级）；ch 临时库经 ch_db fixture
# ================================================================
import dualbridge
from factorlab.app.bootstrap import open_read


class _Env:
    backend = ""

    def seed(self, tables):
        raise NotImplementedError

    @property
    def rd(self):
        raise NotImplementedError


class _EnvDuck(_Env):
    backend = "duckdb"

    def __init__(self, path):
        self.path = path
        self._rd = None

    def seed(self, tables):
        # rd 为 read_only 连接：已打开时同文件 rw 建库会冲突——先关再灌，
        # 之后按需重开（支持同测试多次 env.seed 替换表的再态场景）
        if self._rd is not None:
            self._rd.close()
            self._rd = None
        dualbridge.seed_duckdb(self.path, tables)

    @property
    def rd(self):
        if self._rd is None:
            self._rd = open_read(db_path=self.path)
        return self._rd


class _EnvCh(_Env):
    backend = "ch"

    def __init__(self, client, db):
        self.client = client
        self.db = db
        self._rd = None

    def seed(self, tables):
        dualbridge.seed_ch(self.client, self.db, tables)

    @property
    def rd(self):
        if self._rd is None:
            self._rd = open_read(data_backend="ch")
        return self._rd


# ================================================================
# 注册表隔离（WS5 修复）：每测试后恢复平台算子族基线
#   背景：多个测试文件用 reset_registry() + 注册测试算子（插件/os 用例），
#   个别文件不清理 → 同进程中后续测试看到"平台算子缺失/测试算子残留"的
#   registry（实测顺序相关的假失败：test_ops 先于 test_catalog 跑时 red）。
#   成本：每测试 4 次幂等注册（dict 写入级），2469 测试可忽略。
# ================================================================
@pytest.fixture(autouse=True)
def _registry_isolated():
    yield
    from factorlab.core.ops.registration import ensure_all_ops_registered
    from factorlab.core.ops.registry import reset_registry
    reset_registry()
    ensure_all_ops_registered()


@pytest.fixture(params=["duckdb", "ch"])
def env(request, tmp_path, ch_client, monkeypatch):
    """双腿参数化环境：yield env（seed 灌数 + .rd 被测句柄）。CH 不可达自动 skip。

    不依赖 ch_db fixture——duckdb 腿在 CH 不可达时仍须可跑。
    """
    if request.param == "duckdb":
        yield _EnvDuck(tmp_path / "t.duckdb")
        return
    db = f"factorlab_test_{uuid.uuid4().hex[:8]}"
    ch_client.command(f"CREATE DATABASE IF NOT EXISTS {db}")
    monkeypatch.setattr(settings, "ch_database", db)
    yield _EnvCh(ch_client, db)
    ch_client.command(f"DROP DATABASE IF EXISTS {db} SYNC")
