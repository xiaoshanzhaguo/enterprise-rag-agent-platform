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
from backend.rag.knowledge_bases import (
    DEFAULT_KNOWLEDGE_BASE,
    DEFAULT_KNOWLEDGE_BASE_ID,
    SESSION_SCOPED_KNOWLEDGE_BASE_ID,
    build_knowledge_base_storage_session_id,
    list_default_knowledge_bases,
    normalize_knowledge_base_id,
)
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

        # 恢复 Agent 路由结果。这个字段不一定直接展示，但能帮助后续排查“为什么检索/为什么跳过检索”。
        agent_route = metadata.get("agent_route")
        if isinstance(agent_route, dict):
            message["agent_route"] = agent_route

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


def ensure_knowledge_base(
    knowledge_base_id: str | None = None,
    name: str | None = None,
    description: str | None = None,
    owner_role: str = "kb_admin",
) -> str:
    """
    确保企业知识库主记录存在。

    函数说明：
    1. 当前第一版默认只有 enterprise_default 一个企业知识库。
    2. 上传文档和查询知识库列表前都会调用它，避免空库启动时没有知识库记录。
    3. 返回归一化后的 knowledge_base_id，调用方后续统一使用这个 ID。

    :param knowledge_base_id: 知识库 ID；为空时使用默认企业知识库
    :param name: 知识库展示名称
    :param description: 知识库说明
    :param owner_role: 维护该知识库的角色
    :return: 归一化后的知识库 ID
    """
    normalized_id = normalize_knowledge_base_id(knowledge_base_id)
    default_config = DEFAULT_KNOWLEDGE_BASE if normalized_id == DEFAULT_KNOWLEDGE_BASE_ID else {}
    now = _current_timestamp()

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO knowledge_bases (id, name, description, owner_role, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                owner_role = excluded.owner_role,
                updated_at = excluded.updated_at
            """,
            (
                normalized_id,
                name or default_config.get("name") or normalized_id,
                description or default_config.get("description"),
                owner_role or default_config.get("owner_role") or "kb_admin",
                now,
                now,
            ),
        )
        connection.commit()

    return normalized_id


def list_knowledge_bases() -> list[dict[str, Any]]:
    """
    返回可查询的企业知识库列表。

    函数说明：
    1. 先确保默认企业知识库存在。
    2. 再从 SQLite 读取知识库主表。
    3. 如果数据库为空，至少返回内置默认知识库，保证前端可演示。

    :return: 知识库字典列表
    """
    ensure_knowledge_base(DEFAULT_KNOWLEDGE_BASE_ID)

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                id AS knowledge_base_id,
                name,
                description,
                owner_role,
                created_at,
                updated_at
            FROM knowledge_bases
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()

    if not rows:
        return list_default_knowledge_bases()

    return [
        {
            "knowledge_base_id": row["knowledge_base_id"],
            "name": row["name"],
            "description": row["description"],
            "owner_role": row["owner_role"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


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
    knowledge_base_id: str | None = None,
    knowledge_base_type: str = "general",
    department: str | None = None,
    process_type: str | None = None,
    process_status: str = "active",
) -> list[dict[str, Any]]:
    """
    保存上传文档及其切分后的文本块。

    函数说明：
    1. 如果 session_id 或 chunks 为空，直接返回空列表。
    2. 确保当前聊天会话和企业知识库记录存在。
    3. 生成文档内容哈希值，便于后续识别文档内容。
    4. 如果同一知识库下已经存在同名同内容文档，则复用已有 chunk。
    5. 如果不存在重复文档，则将文档基础信息和业务标签追加写入 documents 表。
    6. 将每个文本块写入 document_chunks 表。
    7. 返回带数据库主键和展示信息的 chunk 元数据。

    :param session_id: 当前会话ID
    :param file_name: 文件名
    :param chunks: 切分后的文本块列表
    :param mode: 当前会话模式
    :param source_type: 文档来源类型
    :param knowledge_base_id: 文档所属企业知识库 ID；为空时使用默认企业知识库
    :param knowledge_base_type: 知识库类型，例如 hr、finance、it、product 或 general
    :param department: 文档所属部门，例如 HR、财务、IT、产品
    :param process_type: 流程类型，例如 leave、reimbursement、vpn、gitlab_access
    :param process_status: 流程状态，例如 active、draft、archived
    :return: 文本块元数据列表
    """
    # 如果没有会话 ID 或没有切分结果，就没有可保存的数据
    if not session_id or not chunks:
        return []

    # 确保当前员工聊天会话存在。这个 session 代表“谁触发了上传动作”。
    ensure_chat_session(session_id=session_id, mode=mode)
    # 只有显式传入 knowledge_base_id 时，才把文档写入企业公共知识库。
    # 没传时保持旧逻辑：文档仍属于当前 session，兼容内容分析等旧入口。
    normalized_knowledge_base_id = ensure_knowledge_base(knowledge_base_id) if knowledge_base_id else None
    if normalized_knowledge_base_id:
        # documents 表历史上有 session_id 外键。公共知识库文档统一挂到内部 session，
        # 这样普通用户删除聊天 session 时，不会误删公共知识库文档。
        storage_session_id = build_knowledge_base_storage_session_id(normalized_knowledge_base_id)
        ensure_chat_session(
            session_id=storage_session_id,
            mode="knowledge_base",
            title=f"知识库文档存储：{normalized_knowledge_base_id}",
        )
    else:
        storage_session_id = session_id
    # documents.knowledge_base_id 目前是 NOT NULL。旧 session 文档没有独立知识库范围时，
    # 写入默认值只做兼容占位；真正检索范围仍然由 session_id 决定。
    document_knowledge_base_id = normalized_knowledge_base_id or SESSION_SCOPED_KNOWLEDGE_BASE_ID
    # 生成当前本地时间，统一用于文档和 chunk 的 created_at
    now = _current_timestamp()
    # 生成文档指纹
    content_hash = hashlib.sha256("\n\n".join(chunks).encode("utf-8")).hexdigest()

    # 打开数据库连接，保存文档和文本块
    with get_connection() as connection:
        # 查询是否已经保存过同名同内容文档，避免刷新后重复上传造成重复索引。
        # 公共知识库文档按 knowledge_base_id 去重；旧入口文档仍按 session_id 去重。
        duplicate_where_clause = "knowledge_base_id = ?" if normalized_knowledge_base_id else "session_id = ?"
        duplicate_where_value = normalized_knowledge_base_id or session_id
        existing_document = connection.execute(
            f"""
            SELECT id
            FROM documents
            WHERE {duplicate_where_clause}
              AND content_hash = ?
              AND (
                  file_name = ?
                  OR (file_name IS NULL AND ? IS NULL)
              )
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (duplicate_where_value, content_hash, file_name, file_name),
        ).fetchone()

        # 如果已经存在同名同内容文档，则直接复用已有 chunk 元数据
        if existing_document:
            # 即使文档内容重复，也允许用户通过本轮上传修正文档分类。
            # 这样前端选择 HR / 财务 / IT / 产品 后，不会因为复用旧 chunk 而保留过期分类。
            connection.execute(
                """
                UPDATE documents
                SET
                    knowledge_base_id = ?,
                    knowledge_base_type = ?,
                    department = ?,
                    process_type = ?,
                    process_status = ?,
                    source_type = ?
                WHERE id = ?
                """,
                (
                    document_knowledge_base_id,
                    knowledge_base_type or "general",
                    department,
                    process_type,
                    process_status or "active",
                    source_type,
                    existing_document["id"],
                ),
            )

            # 读取已有文本块，返回给向量库 upsert，确保 ChromaDB 缺失时也能补写
            existing_chunks = connection.execute(
                """
                SELECT
                    document_chunks.id AS db_chunk_id,
                    document_chunks.document_id AS document_id,
                    document_chunks.file_name AS file_name,
                    document_chunks.chunk_index AS chunk_id,
                    document_chunks.chunk_text AS text,
                    document_chunks.text_length AS text_length,
                    document_chunks.created_at AS created_at,
                    documents.knowledge_base_id AS knowledge_base_id,
                    documents.knowledge_base_type AS knowledge_base_type,
                    documents.department AS department,
                    documents.process_type AS process_type,
                    documents.process_status AS process_status
                FROM document_chunks
                INNER JOIN documents ON documents.id = document_chunks.document_id
                WHERE document_chunks.document_id = ?
                ORDER BY document_chunks.chunk_index ASC
                """,
                (existing_document["id"],),
            ).fetchall()

            # 更新内部知识库 session 时间，表示公共知识库文档被复用或更新。
            connection.execute(
                "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
                (now, storage_session_id),
            )
            # 同时刷新触发上传的用户 session，便于历史会话列表体现最近使用过。
            connection.execute(
                "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            # 提交更新时间
            connection.commit()
            # 将已有 sqlite3.Row 转为普通字典，保持返回结构和新插入分支一致
            return [
                {
                    "chunk_id": row["chunk_id"],
                    "db_chunk_id": row["db_chunk_id"],
                    "document_id": row["document_id"],
                    "file_name": row["file_name"],
                    "text": row["text"],
                    "text_length": row["text_length"],
                    "created_at": row["created_at"],
                    "knowledge_base_id": row["knowledge_base_id"],
                    "knowledge_base_type": row["knowledge_base_type"],
                    "department": row["department"],
                    "process_type": row["process_type"],
                    "process_status": row["process_status"],
                }
                for row in existing_chunks
            ]

        # 先保存文档主记录，后续 chunk 通过 document_id 关联到这条文档；同一会话允许累计多份文档
        document_cursor = connection.execute(
            """
            INSERT INTO documents (
                session_id,
                knowledge_base_id,
                file_name,
                content_hash,
                source_type,
                knowledge_base_type,
                department,
                process_type,
                process_status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                    storage_session_id,
                    document_knowledge_base_id,
                file_name,
                content_hash,
                source_type,
                knowledge_base_type or "general",
                department,
                process_type,
                process_status or "active",
                now,
            ),
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
                    "knowledge_base_id": document_knowledge_base_id,
                    "knowledge_base_type": knowledge_base_type or "general",
                    "department": department,
                    "process_type": process_type,
                    "process_status": process_status or "active",
                }
            )

        # 更新内部知识库 session 时间，表示公共知识库文档发生变化
        connection.execute(
            "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
            (now, storage_session_id),
        )
        # 同时刷新触发上传的用户 session，便于历史会话列表体现最近使用过。
        connection.execute(
            "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
            (now, session_id),
        )
        # 提交事务，让文档、chunks 和会话更新时间一起生效
        connection.commit()
        # 返回保存后的 chunk 元数据
        return saved_chunks


def get_document_chunks(session_id: str | None, knowledge_base_id: str | None = None) -> list[dict[str, Any]]:
    """
    从数据库读取已持久化的 RAG 文本块。

    函数说明：
    1. 如果传入 knowledge_base_id，则按企业知识库查询公共文档。
    2. 如果没有 knowledge_base_id，则兼容旧逻辑，按 session_id 查询当前会话文档。
    3. 关联 document_chunks 表，读取文档切分后的所有文本块。
    4. 将数据库字段转换为 RAG 检索层需要的 chunk 字典结构。

    :param session_id: 当前会话ID
    :param knowledge_base_id: 企业知识库 ID；传入后优先按知识库范围查询
    :return: 文本块列表
    """
    normalized_knowledge_base_id = normalize_knowledge_base_id(knowledge_base_id) if knowledge_base_id else None
    # 没有知识库 ID 且没有会话 ID 时，就无法定位文档，直接返回空列表。
    if not normalized_knowledge_base_id and not session_id:
        return []

    where_clause = "documents.knowledge_base_id = ?" if normalized_knowledge_base_id else "documents.session_id = ?"
    where_value = normalized_knowledge_base_id or session_id

    # 打开数据库连接，查询结束后自动关闭
    with get_connection() as connection:
        # 关联 documents 和 document_chunks，读取指定知识库或旧 session 对应的全部 chunk
        rows = connection.execute(
            f"""
            SELECT
                document_chunks.id AS db_chunk_id,
                document_chunks.document_id AS document_id,
                COALESCE(document_chunks.file_name, documents.file_name) AS file_name,
                document_chunks.chunk_index AS chunk_id,
                document_chunks.chunk_text AS text,
                document_chunks.text_length AS text_length,
                document_chunks.created_at AS created_at,
                documents.knowledge_base_id AS knowledge_base_id,
                documents.knowledge_base_type AS knowledge_base_type,
                documents.department AS department,
                documents.process_type AS process_type,
                documents.process_status AS process_status
            FROM document_chunks
            INNER JOIN documents ON documents.id = document_chunks.document_id
            WHERE {where_clause}
            ORDER BY documents.created_at DESC, documents.id DESC, document_chunks.chunk_index ASC
            """,
            (where_value,),
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
            "knowledge_base_id": row["knowledge_base_id"],
            "knowledge_base_type": row["knowledge_base_type"],
            "department": row["department"],
            "process_type": row["process_type"],
            "process_status": row["process_status"],
        }
        for row in rows
    ]


def get_document_status(session_id: str | None, knowledge_base_id: str | None = None) -> dict[str, Any]:
    """
    从数据库读取 RAG 文档状态。

    函数说明：
    1. 如果传入 knowledge_base_id，则返回企业公共知识库的文档状态。
    2. 如果没有 knowledge_base_id，则兼容旧逻辑，返回当前会话文档状态。
    3. 统计每份文档对应的 chunk 数量。
    4. 返回前端 /rag_status 接口需要的聚合状态结构。

    :param session_id: 当前会话ID
    :param knowledge_base_id: 企业知识库 ID；传入后优先按知识库范围查询
    :return: RAG 文档状态
    """
    normalized_knowledge_base_id = normalize_knowledge_base_id(knowledge_base_id) if knowledge_base_id else None
    # 没有知识库 ID 且没有会话 ID 时，就返回一个空状态，避免接口报错。
    if not normalized_knowledge_base_id and not session_id:
        return {
            "session_id": session_id or "",
            "knowledge_base_id": normalized_knowledge_base_id,
            "has_document": False,
            "file_names": [],
            "document_count": 0,
            "chunk_count": 0,
            "documents": [],
            "expires_in_seconds": 0,
        }

    where_clause = "documents.knowledge_base_id = ?" if normalized_knowledge_base_id else "documents.session_id = ?"
    where_value = normalized_knowledge_base_id or session_id

    # 打开数据库连接，查询当前知识库或旧 session 的文档状态
    with get_connection() as connection:
        # 查询当前知识库下的全部文档，并按上传时间倒序展示
        rows = connection.execute(
            f"""
            SELECT
                documents.id AS document_id,
                documents.knowledge_base_id AS knowledge_base_id,
                documents.file_name AS file_name,
                documents.created_at AS created_at,
                documents.knowledge_base_type AS knowledge_base_type,
                documents.department AS department,
                documents.process_type AS process_type,
                documents.process_status AS process_status,
                COUNT(document_chunks.id) AS chunk_count
            FROM documents
            LEFT JOIN document_chunks ON document_chunks.document_id = documents.id
            WHERE {where_clause}
            GROUP BY
                documents.id,
                documents.knowledge_base_id,
                documents.file_name,
                documents.created_at,
                documents.knowledge_base_type,
                documents.department,
                documents.process_type,
                documents.process_status
            ORDER BY documents.created_at DESC, documents.id DESC
            """,
            (where_value,),
        ).fetchall()

        # 如果当前会话没有文档记录，就返回无文档状态
        if not rows:
            return {
                "session_id": session_id,
                "knowledge_base_id": normalized_knowledge_base_id,
                "has_document": False,
                "file_names": [],
                "document_count": 0,
                "chunk_count": 0,
                "documents": [],
                "expires_in_seconds": 0,
            }

    # 整理每份文档的状态，前端可用于展示多文档列表
    documents = [
        {
            "document_id": row["document_id"],
            "knowledge_base_id": row["knowledge_base_id"],
            "file_name": row["file_name"],
            "chunk_count": int(row["chunk_count"]),
            "created_at": row["created_at"],
            "knowledge_base_type": row["knowledge_base_type"],
            "department": row["department"],
            "process_type": row["process_type"],
            "process_status": row["process_status"],
        }
        for row in rows
    ]
    # 统计当前会话累计文档数量
    document_count = len(documents)
    # 统计当前会话累计文本块数量
    total_chunk_count = sum(document["chunk_count"] for document in documents)
    # 收集全部文件名，供前端展示多文档摘要
    file_names = [
        document["file_name"]
        for document in documents
        if document.get("file_name")
    ]

    # 返回和 RagStatusResponse 对齐的状态字段
    return {
        "session_id": session_id,
        "knowledge_base_id": normalized_knowledge_base_id,
        "has_document": total_chunk_count > 0,
        "file_names": file_names,
        "document_count": document_count,
        "chunk_count": total_chunk_count,
        "documents": documents,
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
    agent_route_result: str | None = None,
    agent_route_reason: str | None = None,
    agent_rewritten_query: str | None = None,
    knowledge_base_id: str | None = None,
    knowledge_base_type_filter: str | None = None,
    answer_text: str | None = None,
) -> int | None:
    """
    保存一次 RAG 查询记录及其命中的文档块结果。

    函数说明：
    1. 先确保当前 session_id 对应的聊天会话存在。
    2. 将用户本次 RAG 查询内容、实际检索方式、Agent 路由结果和最终回答保存到 rag_queries 表。
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
    :param agent_route_result: Agent 路由结果，例如 use_knowledge_base、skip_knowledge_base
    :param agent_route_reason: Agent 路由理由，来自意图分类或兜底策略
    :param agent_rewritten_query: Agent 改写出的检索 query，方便排查 query rewrite 是否有效
    :param knowledge_base_id: 本次检索使用的企业知识库 ID
    :param knowledge_base_type_filter: 知识库类型
    :param answer_text: 本次检索对应的最终回答。流式生成场景下可以先为空，生成结束后再回填
    :return: 保存成功时，返回本次 rag_queries 表中新插入记录的主键 ID；如果 session_id 或 query_text 为空，则返回None
    """
    if not session_id or not query_text:
        return None

    normalized_knowledge_base_id = normalize_knowledge_base_id(knowledge_base_id) if knowledge_base_id else None
    ensure_chat_session(session_id=session_id, mode=mode)
    now = _current_timestamp()

    with get_connection() as connection:
        query_cursor = connection.execute(
            """
            INSERT INTO rag_queries (
                session_id,
                query_text,
                top_k,
                retrieval_mode,
                agent_route_result,
                agent_route_reason,
                agent_rewritten_query,
                knowledge_base_id,
                knowledge_base_type_filter,
                answer_text,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                query_text,
                top_k,
                retrieval_mode or "unknown",
                agent_route_result,
                agent_route_reason,
                agent_rewritten_query,
                normalized_knowledge_base_id,
                knowledge_base_type_filter,
                answer_text,
                now,
            ),
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


def update_rag_query_answer(rag_query_id: int | None, answer_text: str) -> None:
    """
    回填某次 RAG 查询对应的最终回答。

    函数说明：
    1. save_rag_query_with_hits() 发生在检索完成后、模型生成前。
    2. 流式回答结束后再调用本函数，把最终 answer_text 写回 rag_queries。
    3. 这样 rag_queries / rag_hits 可以作为完整证据：问题、分类、命中片段、分数、最终回答都能查到。

    :param rag_query_id: rag_queries 表主键
    :param answer_text: 模型最终生成的回答文本
    :return: None
    """
    if not rag_query_id:
        return

    with get_connection() as connection:
        connection.execute(
            "UPDATE rag_queries SET answer_text = ? WHERE id = ?",
            (answer_text, rag_query_id),
        )
        connection.commit()


def update_latest_rag_query_answer(session_id: str | None, answer_text: str) -> None:
    """
    回填当前会话最近一次 RAG 查询的最终回答。

    普通 chat/workflow 链路里的 build_rag_context() 只负责构造 prompt，
    调用方拿不到 rag_query_id，因此这里按 session_id 找最近一条记录回填。
    Agent 链路已经能拿到精确 rag_query_id 时，应优先调用 update_rag_query_answer()。

    :param session_id: 当前会话 ID
    :param answer_text: 模型最终生成的回答文本
    :return: None
    """
    if not session_id:
        return

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id
            FROM rag_queries
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()

        if not row:
            return

        connection.execute(
            "UPDATE rag_queries SET answer_text = ? WHERE id = ?",
            (answer_text, int(row["id"])),
        )
        connection.commit()


def save_n8n_execution_record(
    session_id: str | None,
    workflow_name: str,
    workflow_url: str | None = None,
    trigger_source: str = "agent",
    execution_id: str | None = None,
    status: str = "planned",
    input_data: dict[str, Any] | None = None,
    output_data: dict[str, Any] | None = None,
    error_message: str | None = None,
    message_id: int | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> int | None:
    """
    保存一条 n8n 工作流执行记录。

    当前项目还没有真正调用 n8n，这个函数先作为持久化入口预留。
    等后续接入 n8n Webhook 后，Service 层只需要在调用前后写入 planned/running/success/failed 状态即可。

    :param session_id: 当前会话 ID，可为空
    :param workflow_name: n8n 工作流名称，例如 reimbursement_approval
    :param workflow_url: n8n Webhook 或工作流地址
    :param trigger_source: 触发来源，例如 agent、manual、webhook
    :param execution_id: n8n 返回的执行 ID
    :param status: 执行状态，例如 planned、running、success、failed
    :param input_data: 传给 n8n 的输入参数
    :param output_data: n8n 返回的结果
    :param error_message: 执行失败时的错误信息
    :param message_id: 关联的 assistant 消息 ID，可为空
    :param started_at: 执行开始时间
    :param finished_at: 执行结束时间
    :return: 新增记录 ID；workflow_name 为空时返回 None
    """
    # workflow_name 是后续排查执行记录的最小必要信息，缺失时不写库
    if not workflow_name:
        return None

    # 如果有 session_id，就确保会话存在，避免外键写入失败
    if session_id:
        ensure_chat_session(session_id=session_id, mode="agent")

    # 字典统一序列化为 JSON 字符串，便于 SQLite 保存半结构化参数
    input_json = json.dumps(input_data or {}, ensure_ascii=False)
    output_json = json.dumps(output_data or {}, ensure_ascii=False)
    now = _current_timestamp()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO n8n_execution_records (
                session_id,
                message_id,
                workflow_name,
                workflow_url,
                trigger_source,
                execution_id,
                status,
                input_json,
                output_json,
                error_message,
                started_at,
                finished_at,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                message_id,
                workflow_name,
                workflow_url,
                trigger_source or "agent",
                execution_id,
                status or "planned",
                input_json,
                output_json,
                error_message,
                started_at,
                finished_at,
                now,
            ),
        )

        connection.commit()
        return int(cursor.lastrowid)
