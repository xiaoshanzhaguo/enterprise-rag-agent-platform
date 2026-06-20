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

# 导入临时文件工具，用于健康检查时验证向量库目录是否可写
import tempfile

# 用于创建后端应用实例
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from backend.config import settings
from backend.api.chat import router as chat_router
from backend.db import init_database
from backend.db.init_db import check_database_connection, get_database_location
from backend.rag.vector_store import resolve_vector_store_dir


# 创建 FastAPI 应用实例
app = FastAPI(
    title="基于 RAG 的企业知识库问答与评测平台", # 接口文档里显示的项目名称
    version="0.1.0" # 当前后端版本号
)

# 注册聊天、工作流、RAG 相关接口
app.include_router(chat_router)


def check_vector_store_writable() -> tuple[bool, str, str | None]:
    """
    检查向量库目录是否可解析和写入。

    函数说明：
    1. 复用 RAG 向量库模块中的目录解析逻辑。
    2. 写入一个很小的临时探针文件，再立即删除。
    3. 不初始化 Chroma collection，也不加载 embedding 模型，保证健康检查足够轻量。

    :return: 三元组，依次为是否可写、向量库绝对路径、错误信息
    """
    try:
        # 获取向量库的真实目录
        vector_store_dir = resolve_vector_store_dir()
        # 只验证目录写入能力，不创建 Chroma collection，避免健康检查触发重依赖加载。
        with tempfile.TemporaryFile(mode="w", encoding="utf-8", dir=vector_store_dir) as probe_file:
            probe_file.write("ok")
            probe_file.flush()
        return True, str(vector_store_dir), None
    except Exception as exc:
        return False, str(settings.vector_store_dir), str(exc)


def build_health_payload() -> dict:
    """
    构造后端健康检查响应。

    响应内容覆盖：
    - 服务是否存活
    - SQLite 是否可连接
    - 当前 RAG 检索模式
    - 向量库目录是否可写

    :return: 可直接序列化为 JSON 的健康检查结果
    """
    try:
        database_ok = check_database_connection()
        database_error = None
    except Exception as exc:
        database_ok = False
        database_error = str(exc)

    try:
        database_location = get_database_location()
    except Exception as exc:
        # 如果路径解析也失败，仍返回原始配置值，方便排查 DATABASE_URL 配置问题。
        database_location = settings.database_url
        database_error = database_error or str(exc)

    vector_store_writable, vector_store_path, vector_store_error = check_vector_store_writable()

    # 数据库和向量目录都可用，才认为后端依赖处于健康状态。
    status = "ok" if database_ok and vector_store_writable else "unhealthy"

    return {
        "status": status, # 整体状态
        "service": "enterprise-rag-agent-backend", # 服务名称，方便判断/health是属于哪个服务
        "database": { # 数据库状态
            "type": "sqlite", # 数据库类型
            "ok": database_ok, # 是否连接成功
            "location": database_location, # 数据库位置
            "error": database_error, # 错误信息
        },
        "rag": { # 当前 RAG 检索配置
            "retrieval_mode": settings.rag_retrieval_mode, # 检索模式
            "keyword_fallback_enabled": settings.rag_keyword_fallback_enabled, # 关键词 fallback 是否开启
        },
        "vector_store": { # 向量库目录状态
            "ok": vector_store_writable, # 向量库整体是否正常
            "path": vector_store_path, # 向量库路径
            "writable": vector_store_writable, # 是否可写
            "error": vector_store_error, # 错误信息
        },
    }


@app.get("/health")
def health_check() -> JSONResponse:
    """
    返回后端健康状态，便于本地演示、Docker 排错和部署探活。

    :return: JSONResponse。健康时返回 200，关键依赖不可用时返回 503。
    """
    payload = build_health_payload()
    status_code = 200 if payload["status"] == "ok" else 503
    return JSONResponse(content=payload, status_code=status_code)


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
