"""
数据库持久化模块（Repository Layer）。

职责：
1. 管理聊天会话(chat_sessions)的数据读写
2. 管理聊天消息(chat_messages)的数据读写，并支持保存单条消息的展示元数据
3. 管理上传文档(documents)及文本块(document_chunks)的数据写入
4. 管理 RAG 查询记录(rag_queries)及命中记录(rag_hits)的数据写入
5. 提供历史会话恢复能力，并支持最近会话列表与指定会话详情读取
6. 提供会话删除能力
7. 提供会话标题读取能力，便于 Service 层只在新会话时生成智能标题
8. 将数据库记录转换为前端可直接使用的数据结构

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
from datetime import datetime
from typing import Any

# 导入数据库连接工具，获取SQLite连接
from backend.db.connection import get_connection
from backend.utils.workflow_formatter import WORKFLOW_STEP_TITLE_MAP, format_workflow_blocks


def _current_timestamp() -> str:
    """
    生成数据库使用的本地时间字符串。

    :return: 当前本地时间，格式为 YYYY-MM-DD HH:MM:SS
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_message_metadata(metadata_json: str | None) -> dict[str, Any]:
    """
    解析聊天消息的元数据 JSON。

    函数说明：
    1. 如果 metadata_json 为空，则返回空字典。
    2. 如果 JSON 解析失败，则返回空字典，避免历史数据异常影响页面恢复。
    3. 只接受字典结构，其他 JSON 类型会被丢弃。

    :param metadata_json: chat_messages.metadata_json 字段内容
    :return: 解析后的消息元数据字典
    """
    # 没有元数据时直接返回空字典
    if not metadata_json:
        return {}

    try:
        # 将数据库里的 JSON 字符串解析为 Python 对象
        metadata = json.loads(metadata_json)
    except (TypeError, json.JSONDecodeError):
        # 元数据损坏时不影响主消息展示
        return {}

    # 只允许字典结构继续向前端传递
    if not isinstance(metadata, dict):
        return {}

    # 返回解析后的元数据
    return metadata


def _message_row_to_dict(row) -> dict[str, Any]:
    """
    将数据库消息记录转换为前端消息结构。

    功能：
    1. 提取 role 和 content
    2. 保留 raw_content
    3. 自动识别工作流结果
    4. 将工作流 JSON 转换为可展示格式
    5. 恢复单条 assistant 消息对应的 RAG 引用元数据

    :param row: chat_messages 表中的一条数据库记录
    :return: 前端消息字典
    """
    message = {
        "role": row["role"],
        "content": row["content"],
    }

    if row["raw_content"]:
        message["raw_content"] = row["raw_content"]

    # 从 metadata_json 中恢复前端展示元数据，例如 RAG 引用来源和命中片段
    metadata = _load_message_metadata(row["metadata_json"])

    # 只给 assistant 消息恢复引用模块，避免用户消息携带无意义展示字段
    if row["role"] == "assistant" and metadata:
        # 恢复当前回答对应的 RAG 命中片段列表
        rag_preview_chunks = metadata.get("rag_preview_chunks")
        if isinstance(rag_preview_chunks, list):
            message["rag_preview_chunks"] = [
                chunk
                for chunk in rag_preview_chunks
                if isinstance(chunk, dict)
            ]

        # 恢复当前回答对应的文档状态信息
        rag_status_info = metadata.get("rag_status_info")
        if isinstance(rag_status_info, dict):
            message["rag_status_info"] = rag_status_info

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
    now = _current_timestamp()
    # 获取数据库连接，代码执行结束后自动关闭连接
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO chat_sessions (id, mode, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                mode = CASE
                    WHEN chat_sessions.mode = 'unknown' THEN excluded.mode
                    ELSE chat_sessions.mode
                END,
                title = COALESCE(chat_sessions.title, excluded.title),
                updated_at = excluded.updated_at
            """,
            (session_id, session_mode, title, now, now),
        )
        connection.commit()


def get_chat_session_title(session_id: str | None) -> str | None:
    """
    读取指定会话当前标题。

    函数说明：
    1. 如果 session_id 为空，直接返回 None。
    2. 如果会话不存在，返回 None。
    3. 如果标题为空字符串，返回 None。
    4. Service 层可据此判断是否需要为新会话生成智能标题。

    :param session_id: 当前会话 ID
    :return: 已存在的会话标题；没有标题时返回 None
    """
    # 没有会话 ID 时无法查询标题
    if not session_id:
        return None

    # 打开数据库连接，按主键读取标题
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT title
            FROM chat_sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()

    # 会话不存在时返回 None
    if not row:
        return None

    # 清理标题空白，避免只有空格的标题被当作有效标题
    title = str(row["title"] or "").strip()
    # 有内容则返回标题，否则返回 None
    return title or None


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
            SELECT role, content, raw_content, metadata_json
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
                SELECT role, content, raw_content, metadata_json
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


def list_recent_chat_sessions(limit: int = 10) -> list[dict[str, Any]]:
    """
    查询最近更新的聊天会话列表。

    函数说明：
    1. 从 chat_sessions 表中读取最近更新的会话。
    2. 关联 chat_messages 统计每个会话的消息数量。
    3. 只返回已经产生过消息的会话，避免空会话干扰前端历史列表。
    4. title 为空时使用第一条用户消息作为兜底标题。

    :param limit: 最多返回多少条会话记录，默认返回最近10条
    :return: 最近会话摘要列表，供前端侧边栏展示
    """
    # 将 limit 转成整数，避免外部传入异常类型
    safe_limit = int(limit or 10)
    # 限制返回数量范围，避免一次性读取过多历史数据
    safe_limit = max(1, min(safe_limit, 50))

    # 打开数据库连接，读取最近会话摘要
    with get_connection() as connection:
        # 查询最近更新的非空会话，并用第一条用户消息兜底标题
        rows = connection.execute(
            """
            SELECT
                chat_sessions.id AS session_id,
                chat_sessions.mode AS mode,
                COALESCE(
                    NULLIF(TRIM(chat_sessions.title), ''),
                    (
                        SELECT chat_messages.content
                        FROM chat_messages
                        WHERE chat_messages.session_id = chat_sessions.id
                          AND chat_messages.role = 'user'
                        ORDER BY chat_messages.message_order ASC, chat_messages.id ASC
                        LIMIT 1
                    ),
                    '未命名会话'
                ) AS title,
                chat_sessions.created_at AS created_at,
                chat_sessions.updated_at AS updated_at,
                COUNT(chat_messages.id) AS message_count
            FROM chat_sessions
            LEFT JOIN chat_messages ON chat_messages.session_id = chat_sessions.id
            GROUP BY chat_sessions.id
            HAVING COUNT(chat_messages.id) > 0
            ORDER BY chat_sessions.updated_at DESC, chat_sessions.created_at DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()

    # 将 sqlite3.Row 转成前端更容易消费的普通字典
    return [
        {
            "session_id": row["session_id"],
            "mode": row["mode"],
            "title": row["title"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "message_count": int(row["message_count"]),
        }
        for row in rows
    ]


def get_chat_session_detail(session_id: str | None) -> dict[str, Any] | None:
    """
    读取指定聊天会话详情。

    函数说明：
    1. 根据 session_id 查询 chat_sessions 会话记录。
    2. 读取该会话下的所有消息，并转换为前端消息结构。
    3. 会话不存在时返回 None，由 API 层转换为 404。

    :param session_id: 需要恢复的会话ID
    :return: 会话详情字典；会话不存在时返回 None
    """
    # 如果没有传入会话ID，直接返回 None
    if not session_id:
        return None

    # 打开数据库连接，查询会话基础信息
    with get_connection() as connection:
        # 根据主键读取会话记录
        session = connection.execute(
            """
            SELECT id, mode, title, created_at, updated_at
            FROM chat_sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()

    # 如果数据库里没有这个会话，则交给 API 层返回 404
    if not session:
        return None

    # 读取并清洗当前会话的消息列表
    messages = get_session_messages(session_id)

    # 返回前端恢复会话所需的完整结构
    return {
        "session_id": session["id"],
        "mode": session["mode"],
        "title": session["title"],
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
        "messages": messages,
    }


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
    metadata: dict[str, Any] | None = None,
) -> int | None:
    """
    保存聊天消息到数据库。

    功能：
    1. 确保当前会话存在
    2. 自动计算消息顺序(message_order)
    3. 保存消息内容
    4. 保存消息展示元数据
    5. 更新会话最后更新时间

    :param session_id: 当前会话ID
    :param role: 消息角色(user/assistant/system)
    :param content: 展示给用户的消息内容
    :param raw_content: 原始消息内容，可为空
    :param mode: 当前会话模式
    :param metadata: 当前消息的展示元数据，例如 RAG 引用来源，可为空
    :return: 新插入消息的数据库主键ID；保存失败时返回None
    """
    if not session_id or not content:
        return None

    ensure_chat_session(session_id=session_id, mode=mode)
    now = _current_timestamp()
    # 将消息元数据转成 JSON 字符串保存；为空时写入 NULL
    metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None

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
            INSERT INTO chat_messages (session_id, role, content, raw_content, metadata_json, message_order, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, role, content, raw_content, metadata_json, next_order, now),
        )
        connection.execute(
            "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
            (now, session_id),
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

    函数说明：
    1. 如果 session_id 或 chunks 为空，直接返回空列表。
    2. 确保当前聊天会话存在。
    3. 生成文档内容哈希值，便于后续识别文档内容。
    4. 删除当前 session 旧的 RAG 文档，保持“一个会话当前索引一份文档”的交互语义。
    5. 将文档基础信息写入 documents 表。
    6. 将每个文本块写入 document_chunks 表。
    7. 返回带数据库主键和展示信息的 chunk 元数据。

    :param session_id: 当前会话ID
    :param file_name: 文件名
    :param chunks: 切分后的文本块列表
    :param mode: 当前会话模式
    :param source_type: 文档来源类型
    :return: 文本块元数据列表
    """
    # 如果没有会话 ID 或没有切分结果，就没有可保存的数据
    if not session_id or not chunks:
        return []

    # 确保当前会话存在，避免 documents.session_id 外键关联失败
    ensure_chat_session(session_id=session_id, mode=mode)
    # 生成当前本地时间，统一用于文档和 chunk 的 created_at
    now = _current_timestamp()
    # 生成文档指纹
    content_hash = hashlib.sha256("\n\n".join(chunks).encode("utf-8")).hexdigest()

    # 打开数据库连接，保存文档和文本块
    with get_connection() as connection:
        # 当前前端交互语义是“一个会话当前索引一份文档”，新上传文档会替换旧文档
        connection.execute("DELETE FROM documents WHERE session_id = ?", (session_id,))

        # 先保存文档主记录，后续 chunk 通过 document_id 关联到这条文档
        document_cursor = connection.execute(
            """
            INSERT INTO documents (session_id, file_name, content_hash, source_type, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, file_name, content_hash, source_type, now),
        )
        # 取出刚插入的 documents.id，作为 document_chunks.document_id
        document_id = int(document_cursor.lastrowid)

        # 收集写入数据库后的 chunk 元数据，返回给 RAG 检索层使用
        saved_chunks = []
        # 从 1 开始给 chunk 编号，便于前端展示和检索排序
        for index, chunk_text in enumerate(chunks, start=1):
            # 保存单个文本块到 document_chunks 表
            chunk_cursor = connection.execute(
                """
                INSERT INTO document_chunks (document_id, file_name, chunk_index, chunk_text, text_length, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (document_id, file_name, index, chunk_text, len(chunk_text), now),
            )
            # 将数据库 chunk 主键和检索所需字段加入返回列表
            saved_chunks.append(
                {
                    "chunk_id": index,
                    "db_chunk_id": int(chunk_cursor.lastrowid),
                    "document_id": document_id,
                    "file_name": file_name,
                    "text": chunk_text,
                    "text_length": len(chunk_text),
                    "created_at": now,
                }
            )

        # 更新会话时间，表示当前 session 的 RAG 文档发生变化
        connection.execute(
            "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
            (now, session_id),
        )
        # 提交事务，让文档、chunks 和会话更新时间一起生效
        connection.commit()
        # 返回保存后的 chunk 元数据
        return saved_chunks


def get_document_chunks(session_id: str | None) -> list[dict[str, Any]]:
    """
    从数据库读取当前会话已持久化的 RAG 文本块。

    函数说明：
    1. 如果 session_id 为空，直接返回空列表。
    2. 根据 session_id 查询 documents 表，定位当前会话的文档。
    3. 关联 document_chunks 表，读取文档切分后的所有文本块。
    4. 将数据库字段转换为 RAG 检索层需要的 chunk 字典结构。

    :param session_id: 当前会话ID
    :return: 文本块列表
    """
    # 如果没有会话 ID，就无法定位文档，直接返回空列表
    if not session_id:
        return []

    # 打开数据库连接，查询结束后自动关闭
    with get_connection() as connection:
        # 关联 documents 和 document_chunks，读取当前 session 对应的全部 chunk
        rows = connection.execute(
            """
            SELECT
                document_chunks.id AS db_chunk_id,
                document_chunks.document_id AS document_id,
                COALESCE(document_chunks.file_name, documents.file_name) AS file_name,
                document_chunks.chunk_index AS chunk_id,
                document_chunks.chunk_text AS text,
                document_chunks.text_length AS text_length,
                document_chunks.created_at AS created_at
            FROM document_chunks
            INNER JOIN documents ON documents.id = document_chunks.document_id
            WHERE documents.session_id = ?
            ORDER BY documents.created_at DESC, document_chunks.chunk_index ASC
            """,
            (session_id,),
        ).fetchall()

    # 将 sqlite3.Row 转成普通字典，统一提供给 RAG 检索层使用
    return [
        {
            "db_chunk_id": row["db_chunk_id"],
            "document_id": row["document_id"],
            "file_name": row["file_name"],
            "chunk_id": row["chunk_id"],
            "text": row["text"],
            "text_length": row["text_length"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def get_document_status(session_id: str | None) -> dict[str, Any]:
    """
    从数据库读取当前会话的 RAG 文档状态。

    函数说明：
    1. 如果 session_id 为空，返回无文档状态。
    2. 查询当前会话最近一次上传的文档。
    3. 统计该文档对应的 chunk 数量。
    4. 返回前端 /rag_status 接口需要的状态结构。

    :param session_id: 当前会话ID
    :return: RAG 文档状态
    """
    # 如果没有会话 ID，就返回一个空状态，避免接口报错
    if not session_id:
        return {
            "session_id": session_id or "",
            "has_document": False,
            "file_name": None,
            "chunk_count": 0,
            "expires_in_seconds": 0,
        }

    # 打开数据库连接，查询当前 session 的文档状态
    with get_connection() as connection:
        # 当前交互只展示最近一次上传的文档状态
        document = connection.execute(
            """
            SELECT id, file_name
            FROM documents
            WHERE session_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()

        # 如果当前会话没有文档记录，就返回无文档状态
        if not document:
            return {
                "session_id": session_id,
                "has_document": False,
                "file_name": None,
                "chunk_count": 0,
                "expires_in_seconds": 0,
            }

        # 统计当前文档下的文本块数量，用于前端展示
        chunk_count = connection.execute(
            """
            SELECT COUNT(*) AS chunk_count
            FROM document_chunks
            WHERE document_id = ?
            """,
            (document["id"],),
        ).fetchone()["chunk_count"]

    # 返回和 RagStatusResponse 对齐的状态字段
    return {
        "session_id": session_id,
        "has_document": chunk_count > 0,
        "file_name": document["file_name"],
        "chunk_count": int(chunk_count),
        "expires_in_seconds": 0,
    }


def delete_session_documents(session_id: str | None) -> None:
    """
    删除当前会话持久化的 RAG 文档和文本块。

    函数说明：
    1. 如果 session_id 为空，直接返回。
    2. 删除 documents 表中属于当前会话的文档。
    3. 依赖外键 ON DELETE CASCADE 自动删除 document_chunks。
    4. 更新 chat_sessions.updated_at，记录当前会话发生过文档清理动作。

    :param session_id: 当前会话ID
    :return: None
    """
    # 如果没有会话 ID，就没有需要清理的文档
    if not session_id:
        return

    # 打开数据库连接，删除完成后提交事务
    with get_connection() as connection:
        # 删除当前会话下的文档；document_chunks 会通过外键级联删除
        connection.execute("DELETE FROM documents WHERE session_id = ?", (session_id,))
        # 更新会话时间，表示当前会话的 RAG 文档状态发生变化
        connection.execute(
            "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
            (_current_timestamp(), session_id),
        )
        # 提交事务，让删除和更新时间正式生效
        connection.commit()


def save_rag_query_with_hits(
    session_id: str | None,
    query_text: str,
    top_k: int,
    matched_chunks: list[dict[str, Any]],
    retrieval_mode: str,
    mode: str = "unknown",
) -> int | None:
    """
    保存一次 RAG 查询记录及其命中的文档块结果。

    函数说明：
    1. 先确保当前 session_id 对应的聊天会话存在。
    2. 将用户本次 RAG 查询内容和实际检索方式保存到 rag_queries 表。
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
    :param retrieval_mode: 本次实际使用的检索方式，例如 vector、keyword 或 no_hit
    :param mode: 当前会话模式。例如：内容分析、结构优化、工作流优化等。如果为空，则使用“unknown”
    :return: 保存成功时，返回本次 rag_queries 表中新插入记录的主键 ID；如果 session_id 或 query_text 为空，则返回None
    """
    if not session_id or not query_text:
        return None

    ensure_chat_session(session_id=session_id, mode=mode)
    now = _current_timestamp()

    with get_connection() as connection:
        query_cursor = connection.execute(
            """
            INSERT INTO rag_queries (session_id, query_text, top_k, retrieval_mode, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, query_text, top_k, retrieval_mode or "unknown", now),
        )
        rag_query_id = int(query_cursor.lastrowid)

        for hit_rank, chunk in enumerate(matched_chunks, start=1):
            db_chunk_id = chunk.get("db_chunk_id")
            if not db_chunk_id:
                continue

            connection.execute(
                """
                INSERT INTO rag_hits (rag_query_id, document_chunk_id, hit_rank, score, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (rag_query_id, db_chunk_id, hit_rank, float(chunk.get("score", 0)), now),
            )

        connection.execute(
            "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
            (now, session_id),
        )
        connection.commit()
        return rag_query_id
