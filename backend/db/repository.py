"""
数据库持久化模块（Repository Layer）。

职责：
1. 管理聊天会话(chat_sessions)的数据读写
2. 管理聊天消息(chat_messages)的数据读写
3. 管理上传文档(documents)及文本块(document_chunks)的数据写入
4. 管理 RAG 查询记录(rag_queries)及命中记录(rag_hits)的数据写入
5. 提供历史会话恢复能力
6. 提供会话删除能力
7. 将数据库记录转换为前端可直接使用的数据结构

说明：
- 当前模块属于 Repository 层
- 负责数据库 CRUD 操作
- 不负责业务逻辑处理
- Service 层调用本模块完成数据持久化

数据流：ChatRequest -> ChatService -> Repository -> SQLite
"""

# 哈希算法模块，给上传文档生成唯一指纹
import hashlib
import json
from typing import Any

# 导入数据库连接工具，获取SQLite连接
from backend.db.connection import get_connection
from backend.utils.workflow_formatter import WORKFLOW_STEP_TITLE_MAP, format_workflow_blocks


def _message_row_to_dict(row) -> dict[str, Any]:
    """
    将数据库消息记录转换为前端消息结构。

    功能：
    1. 提取 role 和 content
    2. 保留 raw_content
    3. 自动识别工作流结果
    4. 将工作流 JSON 转换为可展示格式

    :param row: chat_messages 表中的一条数据库记录
    :return: 前端消息字典
    """
    message = {
        "role": row["role"],
        "content": row["content"],
    }

    if row["raw_content"]:
        message["raw_content"] = row["raw_content"]

    if row["role"] == "assistant":
        try:
            parsed_content = json.loads(row["content"])
        except (TypeError, json.JSONDecodeError):
            parsed_content = None

        # 判断当前内容是否为工作流结果（包含 summary/analysis/suggestion 任意步骤）
        if isinstance(parsed_content, dict) and any(
            step_name in parsed_content
            for step_name in WORKFLOW_STEP_TITLE_MAP
        ):
            workflow_blocks = {
                key: value
                for key, value in parsed_content.items()
                if key in WORKFLOW_STEP_TITLE_MAP and isinstance(value, str)
            }
            message["workflow_blocks"] = workflow_blocks
            message["content"] = format_workflow_blocks(workflow_blocks)

    return message


def ensure_chat_session(session_id: str | None, mode: str = "unknown", title: str | None = None) -> None:
    """
    确保聊天会话记录存在，并刷新会话基础信息。

    函数说明：
    1. 如果 session_id 为空，则直接返回，不执行数据库操作。
    2. 如果当前 session_id 对应的会话不存在，则创建一条新的会话记录。
    3. 如果当前 session_id 已存在，则更新会话的基础元数据：
       - 如果原 mode 是 unknown，则更新为当前传入的 mode
       - 如果原 title 为空，则补充当前传入的 title
       - 每次调用都会刷新 updated_at 时间

    :param session_id: 当前会话ID。用于唯一标识一个聊天会话
    :param mode: 当前会话模式。例如：内容分析、结构优化、工作流优化等。如果为空，则使用“unknown”
    :param title: 当前会话标题，可为空。后续可以用于会话列表展示
    :return: None。该函数只负责确保数据库中的会话记录存在，不返回具体数据
    """
    if not session_id:
        return

    session_mode = mode or "unknown"
    # 获取数据库连接，代码执行结束后自动关闭连接
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO chat_sessions (id, mode, title)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                mode = CASE
                    WHEN chat_sessions.mode = 'unknown' THEN excluded.mode
                    ELSE chat_sessions.mode
                END,
                title = COALESCE(chat_sessions.title, excluded.title),
                updated_at = CURRENT_TIMESTAMP
            """,
            (session_id, session_mode, title),
        )
        connection.commit()


def get_session_messages(session_id: str | None) -> list[dict[str, Any]]:
    """
    获取指定会话的全部消息。

    功能：
    1. 查询当前会话消息
    2. 按消息顺序排序
    3. 转换为前端消息结构

    :param session_id: 会话ID
    :return: 消息列表
    """
    if not session_id:
        return []

    with get_connection() as connection:
        # 获取查询结果中的所有记录
        rows = connection.execute(
            """
            SELECT role, content, raw_content
            FROM chat_messages
            WHERE session_id = ?
            ORDER BY message_order ASC, id ASC
            """,
            (session_id,),
        ).fetchall()

    # 将所有记录转成前端格式
    return [_message_row_to_dict(row) for row in rows]


def load_latest_mode_sessions(mode_names: list[str]) -> dict[str, dict[str, Any]]:
    """
    加载各模式最近一次聊天会话。

    功能：
    1. 查找每个模式最新会话
    2. 加载历史消息
    3. 转换为前端会话结构

    :param mode_names: 模式名称列表
    :return: 前端会话数据
    """
    mode_sessions: dict[str, dict[str, Any]] = {}

    with get_connection() as connection:
        for mode_name in mode_names:
            session = connection.execute(
                """
                SELECT id
                FROM chat_sessions
                WHERE mode = ?
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """,
                (mode_name,),
            ).fetchone()

            if not session:
                continue

            rows = connection.execute(
                """
                SELECT role, content, raw_content
                FROM chat_messages
                WHERE session_id = ?
                ORDER BY message_order ASC, id ASC
                """,
                (session["id"],),
            ).fetchall()

            mode_sessions[mode_name] = {
                "session_id": session["id"],
                "messages": [_message_row_to_dict(row) for row in rows],
            }

    return mode_sessions


def delete_chat_session(session_id: str | None) -> None:
    """
    删除指定聊天会话。

    功能：
    1. 删除 chat_sessions 记录
    2. 自动触发外键级联删除
    3. 删除消息、文档、RAG记录

    :param session_id: 会话ID
    :return: None
    """
    if not session_id:
        return

    with get_connection() as connection:
        connection.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
        connection.commit()


def save_chat_message(
    session_id: str | None,
    role: str,
    content: str,
    raw_content: str | None = None,
    mode: str = "unknown",
) -> int | None:
    """
    保存聊天消息到数据库。

    功能：
    1. 确保当前会话存在
    2. 自动计算消息顺序(message_order)
    3. 保存消息内容
    4. 更新会话最后更新时间

    :param session_id: 当前会话ID
    :param role: 消息角色(user/assistant/system)
    :param content: 展示给用户的消息内容
    :param raw_content: 原始消息内容，可为空
    :param mode: 当前会话模式
    :return: 新插入消息的数据库主键ID；保存失败时返回None
    """
    if not session_id or not content:
        return None

    ensure_chat_session(session_id=session_id, mode=mode)

    with get_connection() as connection:
        # 获得下一条消息序号
        next_order = connection.execute(
            "SELECT COALESCE(MAX(message_order), 0) + 1 AS next_order FROM chat_messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()["next_order"]

        # 获得刚插入数据的主键。cursor是这次 SQL 执行后的“结果游标对象”
        # 数据库执行完这条 SQL 后，给 Python 返回一个操作结果对象
        cursor = connection.execute(
            """
            INSERT INTO chat_messages (session_id, role, content, raw_content, message_order)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, role, content, raw_content, next_order),
        )
        connection.execute(
            "UPDATE chat_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (session_id,),
        )
        connection.commit()

        # 返回刚刚这次 INSERT 插入的新记录的自增主键 ID
        return int(cursor.lastrowid)


def save_document_with_chunks(
    session_id: str,
    file_name: str | None,
    chunks: list[str],
    mode: str = "unknown",
    source_type: str = "upload",
) -> list[dict[str, Any]]:
    """
    保存上传文档及其切分后的文本块。

    功能：
    1. 保存文档信息
    2. 生成文档内容哈希值
    3. 保存文本块(document_chunks)
    4. 返回文本块元数据

    :param session_id: 当前会话ID
    :param file_name: 文件名
    :param chunks: 切分后的文本块列表
    :param mode: 当前会话模式
    :param source_type: 文档来源类型
    :return: 文本块元数据列表
    """
    if not session_id or not chunks:
        return []

    ensure_chat_session(session_id=session_id, mode=mode)
    # 生成文档指纹
    content_hash = hashlib.sha256("\n\n".join(chunks).encode("utf-8")).hexdigest()

    with get_connection() as connection:
        document_cursor = connection.execute(
            """
            INSERT INTO documents (session_id, file_name, content_hash, source_type)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, file_name, content_hash, source_type),
        )
        document_id = int(document_cursor.lastrowid)

        saved_chunks = []
        for index, chunk_text in enumerate(chunks, start=1):
            chunk_cursor = connection.execute(
                """
                INSERT INTO document_chunks (document_id, chunk_index, chunk_text, text_length)
                VALUES (?, ?, ?, ?)
                """,
                (document_id, index, chunk_text, len(chunk_text)),
            )
            saved_chunks.append(
                {
                    "chunk_id": index,
                    "db_chunk_id": int(chunk_cursor.lastrowid),
                    "document_id": document_id,
                    "text": chunk_text,
                }
            )

        connection.execute(
            "UPDATE chat_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (session_id,),
        )
        connection.commit()
        return saved_chunks


def save_rag_query_with_hits(
    session_id: str | None,
    query_text: str,
    top_k: int,
    matched_chunks: list[dict[str, Any]],
    mode: str = "unknown",
) -> int | None:
    """
    保存一次 RAG 查询记录及其命中的文档块结果。

    函数说明：
    1. 先确保当前 session_id 对应的聊天会话存在。
    2. 将用户本次 RAG 查询内容保存到 rag_queries 表。
    3. 遍历本次检索命中的 matched_chunks，将每个命中文档块保存到 rag_hits 表。
    4. 每条 rag_hits 记录会保存：
       - 当前查询 ID
       - 命中的数据库文档块 ID
       - 命中排名
       - 检索分数
    5. 最后更新 chat_sessions 的 updated_at 时间。

    :param session_id: 当前会话 ID。如果为空，则不执行保存，直接返回None
    :param query_text: 用户本次 RAG 查询文本
    :param top_k: 本次 RAG 检索返回的片段数量
    :param matched_chunks: 本次 RAG 检索命中的文本块列表。每个元素通常包含 db_chunk_id、score、chunk_id、text 等字段。其中 db_chunk_id 用于关联 document_chunks 表中的真实数据库记录。
    :param mode: 当前会话模式。例如：内容分析、结构优化、工作流优化等。如果为空，则使用“unknown”
    :return: 保存成功时，返回本次 rag_queries 表中新插入记录的主键 ID；如果 session_id 或 query_text 为空，则返回None
    """
    if not session_id or not query_text:
        return None

    ensure_chat_session(session_id=session_id, mode=mode)

    with get_connection() as connection:
        query_cursor = connection.execute(
            """
            INSERT INTO rag_queries (session_id, query_text, top_k)
            VALUES (?, ?, ?)
            """,
            (session_id, query_text, top_k),
        )
        rag_query_id = int(query_cursor.lastrowid)

        for hit_rank, chunk in enumerate(matched_chunks, start=1):
            db_chunk_id = chunk.get("db_chunk_id")
            if not db_chunk_id:
                continue

            connection.execute(
                """
                INSERT INTO rag_hits (rag_query_id, document_chunk_id, hit_rank, score)
                VALUES (?, ?, ?, ?)
                """,
                (rag_query_id, db_chunk_id, hit_rank, float(chunk.get("score", 0))),
            )

        connection.execute(
            "UPDATE chat_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (session_id,),
        )
        connection.commit()
        return rag_query_id
