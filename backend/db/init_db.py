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
- 当前项目按最新 schema 重新规划数据库结构，不再保留旧表迁移逻辑
- 如果本地旧表结构和最新 schema 不一致，应删除旧 app.db 后重新启动项目生成新库

典型调用流程：启动项目 -> init_database() -> 创建表 -> 创建索引 -> 数据库初始化完成
"""

from backend.db.connection import get_connection, resolve_sqlite_path
# 导入建表 SQL 和建索引 SQL
from backend.db.schema import CREATE_INDEX_SQL, CREATE_TABLE_SQL


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
