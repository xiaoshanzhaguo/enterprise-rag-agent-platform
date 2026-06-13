"""
FastAPI 后端应用入口模块。

职责：
1. 创建 FastAPI 应用实例
2. 注册聊天、工作流、RAG 等 API 路由
3. 在后端应用启动时初始化 SQLite 数据库

说明：
- 当前模块是后端服务启动入口
- 不直接编写具体业务逻辑
- 业务接口由 backend.api.chat 中的 router 统一注册
- 数据库初始化逻辑由 backend.db.init_database 负责
"""
# 用于创建后端应用实例
from fastapi import FastAPI

from backend.api.chat import router as chat_router
from backend.db import init_database


# 创建 FastAPI 应用实例
app = FastAPI(
    title="基于 RAG 的企业知识库问答与评测平台", # 接口文档里显示的项目名称
    version="0.1.0" # 当前后端版本号
)

# 注册聊天、工作流、RAG 相关接口
app.include_router(chat_router)


# 注册一个启动事件。当 FastAPI 后端启动时，自动执行下面的函数
@app.on_event("startup")
def startup_event() -> None:
    """
    后端启动时初始化 SQLite 数据库。
    函数说明：
    - 在 FastAPI 应用启动阶段自动执行
    - 调用数据库初始化函数，确保数据表和索引已创建

    :return: None。表示这个函数只做初始化动作，不返回业务数据
    """
    init_database()
