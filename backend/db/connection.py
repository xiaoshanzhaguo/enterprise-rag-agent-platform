"""
SQLite 连接工具模块。

职责：
1. 负责解析 SQLite 数据库连接地址，并转换为本地文件路径或内存数据库标识
2. 在连接数据库前，自动创建数据库文件所在目录，避免因目录不存在导致连接失败
3. 提供统一的 SQLite 连接创建入口，并在连接建立后完成基础配置

说明：
- 当前模块仅面向 SQLite 数据库
- 支持 sqlite:///相对路径、sqlite:///绝对路径，以及 sqlite:///:memory: 内存数据库
- 适合作为项目中的底层数据库连接工具，被初始化脚本、DAO 层或服务层复用
"""
# 用来创建和操作 SQLite 数据库连接
import sqlite3
# 用来处理文件路径和目录路径
from pathlib import Path

# 用来读取数据库连接配置，例如 settings.database_url
from backend.config import settings


# 项目根目录，用于将相对数据库路径转换为基于项目根目录的绝对路径
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def resolve_sqlite_path(database_url: str | None = None) -> Path | str:
    """
    将 sqlite:/// 格式的数据库 URL 解析为本地文件路径或 :memory: 标识。

    :param database_url: SQLite 数据库连接地址；如果不传，则默认使用 settings.database_url
    :return: 文件数据库时返回 Path 对象；内存数据库时返回字符串 ":memory:"
    """
    # 优先使用函数传入的 database_url；如果未传，则使用全局配置中的数据库地址
    url = database_url or settings.database_url

    # 如果使用的是 SQLite 内存数据库，则直接返回特殊标识 ":memory:"
    if url == "sqlite:///:memory:":
        return ":memory:"

    # 当前模块仅支持 sqlite:/// 开头的数据库 URL，其他类型直接报错
    if not url.startswith("sqlite:///"):
        raise ValueError("当前只支持 sqlite:/// 格式的数据库连接地址。")

    # 去掉 sqlite:/// 前缀，得到真实的路径部分
    raw_path = url.removeprefix("sqlite:///")

    # 将路径字符串转换为 Path 对象，便于后续处理
    db_path = Path(raw_path)

    # 如果当前路径不是绝对路径，则基于项目根目录补成完整路径
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path

    # 返回解析后的绝对路径
    return db_path.resolve()


def ensure_database_directory(database_url: str | None = None) -> Path | str:
    """
    为当前 SQLite 数据库文件创建父目录。

    :param database_url: SQLite 数据库连接地址；如果不传，则默认使用 settings.database_url
    :return: 解析后的数据库路径，可能是 Path 或 ":memory:"
    """
    # 先把数据库 URL 解析成文件路径或内存数据库标识
    db_path = resolve_sqlite_path(database_url)

    # 如果当前是文件数据库，则先创建数据库文件所在的父目录，避免后续连接时因目录不存在而失败
    if isinstance(db_path, Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)

    return db_path


def get_connection(database_url: str | None = None) -> sqlite3.Connection:
    """
    打开一个启用了外键约束的 SQLite 连接。

    :param database_url: SQLite 数据库连接地址；如果不传，则默认使用 settings.database_url
    :return: sqlite3.Connection 数据库连接对象
    """
    # 确保数据库路径可用，并且数据库文件所在目录已存在
    db_path = ensure_database_directory(database_url)

    # 创建 SQLite 连接
    connection = sqlite3.connect(db_path)

    # 让查询结果支持按列名访问，例如 row["id"]
    connection.row_factory = sqlite3.Row

    # 显式开启 SQLite 外键约束
    connection.execute("PRAGMA foreign_keys = ON")

    # 返回配置完成的连接对象
    return connection
