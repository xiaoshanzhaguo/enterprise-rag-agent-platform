"""
数据库辅助模块。

职责：
1. 对外统一暴露数据库初始化能力
2. 作为 backend.db 包的公共导出入口
"""

# 导入数据库初始化函数
from backend.db.init_db import init_database

# 指定当前模块对外公开导出的对象
__all__ = ["init_database"]
