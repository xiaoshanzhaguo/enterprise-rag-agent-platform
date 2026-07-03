"""
数据库表结构定义模块。

职责：
1. 定义项目所有数据表的建表 SQL
2. 定义项目所有索引的创建 SQL
3. 作为数据库初始化模块的数据来源

说明：
- 当前项目基于 SQLite
- CREATE_TABLE_SQL 用于创建数据表
- CREATE_INDEX_SQL 用于创建索引
- init_database() 会依次执行本文件中的所有 SQL

数据模型覆盖：
1. 会话管理
2. 消息历史和消息展示元数据
3. 文档管理和企业知识库业务标签
4. RAG 检索和 Agent 路由记录
5. n8n 执行记录预留
6. Prompt 评测
"""
CREATE_TABLE_SQL = [
    # 保存聊天会话
    """
    CREATE TABLE IF NOT EXISTS chat_sessions (
        id TEXT PRIMARY KEY,
        mode TEXT NOT NULL,
        title TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )
    """,
    # 保存聊天记录
    """
    CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        raw_content TEXT,
        metadata_json TEXT,
        message_order INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
    )
    """,
    # 保存文档信息
    """
    CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        file_name TEXT,
        content_hash TEXT,
        source_type TEXT NOT NULL DEFAULT 'upload',
        knowledge_base_type TEXT NOT NULL DEFAULT 'general',
        department TEXT,
        process_type TEXT,
        process_status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
    )
    """,
    # 保存切块后的内容
    """
    CREATE TABLE IF NOT EXISTS document_chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        document_id INTEGER NOT NULL,
        file_name TEXT,
        chunk_index INTEGER NOT NULL,
        chunk_text TEXT NOT NULL,
        text_length INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
    )
    """,
    # 记录用户检索
    """
    CREATE TABLE IF NOT EXISTS rag_queries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        query_text TEXT NOT NULL,
        top_k INTEGER NOT NULL DEFAULT 3,
        retrieval_mode TEXT NOT NULL DEFAULT 'unknown',
        agent_route_result TEXT,
        agent_route_reason TEXT,
        agent_rewritten_query TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
    )
    """,
    # 记录哪些 chunk 命中了
    """
    CREATE TABLE IF NOT EXISTS rag_hits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rag_query_id INTEGER NOT NULL,
        document_chunk_id INTEGER NOT NULL,
        hit_rank INTEGER NOT NULL,
        score REAL NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (rag_query_id) REFERENCES rag_queries(id) ON DELETE CASCADE,
        FOREIGN KEY (document_chunk_id) REFERENCES document_chunks(id) ON DELETE CASCADE
    )
    """,
    # 保存未来 n8n 工作流执行记录。当前项目还没有真正调用 n8n，
    # 先把表结构预留出来，后续接入自动化流程时不用再重做数据库设计。
    """
    CREATE TABLE IF NOT EXISTS n8n_execution_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        message_id INTEGER,
        workflow_name TEXT NOT NULL,
        workflow_url TEXT,
        trigger_source TEXT NOT NULL DEFAULT 'agent',
        execution_id TEXT,
        status TEXT NOT NULL DEFAULT 'planned',
        input_json TEXT,
        output_json TEXT,
        error_message TEXT,
        started_at TEXT,
        finished_at TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE SET NULL,
        FOREIGN KEY (message_id) REFERENCES chat_messages(id) ON DELETE SET NULL
    )
    """,
    # 未来做 Prompt 测试
    """
    CREATE TABLE IF NOT EXISTS eval_cases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        task_type TEXT NOT NULL,
        input_text TEXT NOT NULL,
        expected_output TEXT,
        metadata_json TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )
    """,
    # 保存测试结果
    """
    CREATE TABLE IF NOT EXISTS eval_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        eval_case_id INTEGER NOT NULL,
        model_name TEXT,
        output_text TEXT NOT NULL,
        score REAL,
        metrics_json TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (eval_case_id) REFERENCES eval_cases(id) ON DELETE CASCADE
    )
    """,
]


# 索引
CREATE_INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_chat_messages_session_id ON chat_messages(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_documents_session_id ON documents(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_documents_department ON documents(department)",
    "CREATE INDEX IF NOT EXISTS idx_documents_process_type ON documents(process_type)",
    "CREATE INDEX IF NOT EXISTS idx_document_chunks_document_id ON document_chunks(document_id)",
    "CREATE INDEX IF NOT EXISTS idx_rag_queries_session_id ON rag_queries(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_rag_queries_agent_route_result ON rag_queries(agent_route_result)",
    "CREATE INDEX IF NOT EXISTS idx_rag_hits_query_id ON rag_hits(rag_query_id)",
    "CREATE INDEX IF NOT EXISTS idx_n8n_execution_records_session_id ON n8n_execution_records(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_n8n_execution_records_status ON n8n_execution_records(status)",
    "CREATE INDEX IF NOT EXISTS idx_eval_results_case_id ON eval_results(eval_case_id)",
]
