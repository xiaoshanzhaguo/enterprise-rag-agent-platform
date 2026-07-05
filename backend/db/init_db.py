"""
数据库初始化模块。

职责：
1. 创建项目运行所需的数据表
2. 创建项目运行所需的数据库索引
3. 提供数据库连接测试能力
4. 提供数据库实际存储位置查询能力

说明：
- 当前模块基于 SQLite 实现
- 数据表结构来自 schema.py
- 数据库连接能力来自 connection.py
- 当前项目按最新 schema 重新规划数据库结构
- 测试阶段如果本地 app.db 结构已过期，可删除后重新启动项目生成新库

典型调用流程：启动项目 -> init_database() -> 创建表 -> 创建索引 -> 数据库初始化完成
"""

from backend.db.connection import get_connection, resolve_sqlite_path
# 导入建表 SQL 和建索引 SQL
from backend.db.schema import CREATE_INDEX_SQL, CREATE_TABLE_SQL, TABLE_COLUMN_MIGRATIONS


def _get_existing_columns(connection, table_name: str) -> set[str]:
    """
    读取 SQLite 表中已经存在的列名。

    为什么需要这个函数：
    1. 本地开发时 data/app.db 往往已经存在。
    2. CREATE TABLE IF NOT EXISTS 不会修改旧表结构。
    3. 先查已有列，再只补缺失列，可以保证初始化逻辑重复执行也安全。

    :param connection: SQLite 数据库连接
    :param table_name: 表名
    :return: 当前表已有列名集合
    """
    # PRAGMA table_info(table_name) 会返回当前表的列信息，其中 name 是列名
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    # get_connection() 已经把 row_factory 设为 sqlite3.Row，因此可以通过 row["name"] 读取列名
    return {row["name"] for row in rows}


def _apply_column_migrations(connection) -> None:
    """
    给旧版 SQLite 表补充新增列。

    说明：
    - 当前只做新增列迁移，不做删除列、改类型、搬数据等高风险操作。
    - 新增列要么允许 NULL，要么带 DEFAULT，保证旧数据可以平滑升级。
    - 迁移必须在创建索引之前执行，否则索引引用新列时会报 no such column。
    """
    # 遍历 schema.py 中声明的轻量迁移清单
    for table_name, columns in TABLE_COLUMN_MIGRATIONS.items():
        # 读取当前旧表已经有哪些列
        existing_columns = _get_existing_columns(connection, table_name)
        # 逐个补充缺失列
        for column_name, column_definition in columns:
            # 如果列已经存在，就跳过，避免重复 ALTER TABLE 报错
            if column_name in existing_columns:
                continue

            # SQLite 不支持 ADD COLUMN IF NOT EXISTS，所以这里依赖上面的 PRAGMA 判断
            connection.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
            )


def init_database(database_url: str | None = None) -> None:
    """
    创建项目运行所需的数据表和索引。如果表或索引已存在，则不会重复创建。

    函数说明：
    1. 获取 SQLite 数据库连接。
    2. 按 schema.py 中定义的 CREATE_TABLE_SQL 创建数据表。
    3. 按 schema.py 中定义的 CREATE_INDEX_SQL 创建索引。
    4. 提交事务，让建表和建索引操作正式生效。

    :param database_url: 可选的 SQLite 数据库连接地址。如果不传，则底层连接工具会使用 settings.database_url
    :return: None
    """
    # 获取数据库连接
    with get_connection(database_url) as connection:
        # 依次执行所有建表 SQL
        for statement in CREATE_TABLE_SQL:
            connection.execute(statement)

        # 先给旧表补新增列，再创建依赖这些列的索引。
        # 否则旧库里没有 department/process_type 时，idx_documents_department 会启动失败。
        _apply_column_migrations(connection)

        # 依次执行所有建索引 SQL
        for statement in CREATE_INDEX_SQL:
            connection.execute(statement)

        # 提交事务，将所有修改正式写入数据库
        connection.commit()


def check_database_connection(database_url: str | None = None) -> bool:
    """
    检查数据库是否能够正常连接。

    函数说明：
    1. 打开 SQLite 数据库连接。
    2. 执行最简单的 SELECT 1 查询。
    3. 如果没有抛出异常，则说明数据库连接可用。

    :param database_url: 可选的 SQLite 数据库连接地址。如果不传，则底层连接工具会使用 settings.database_url
    :return: bool，True 表示数据库连接成功
    """
    # 打开数据库连接
    with get_connection(database_url) as connection:
        # 执行最简单的测试查询
        connection.execute("SELECT 1")
    return True


def get_database_location(database_url: str | None = None) -> str:
    """
    获取数据库实际存储位置。

    函数说明：
    1. 调用 resolve_sqlite_path() 解析 SQLite 数据库路径。
    2. 将 Path 对象转换为字符串，方便日志或调试展示。

    :param database_url: 可选的 SQLite 数据库连接地址。如果不传，则底层连接工具会使用 settings.database_url
    :return: 数据库文件的绝对路径字符串
    """
    # 返回解析后的数据库路径
    return str(resolve_sqlite_path(database_url))
