"""
RAG 向量库模块。

职责：
1. 使用本地 ChromaDB 作为向量库。
2. 将文档 chunk 的 embedding、正文和 metadata 持久化到 data/chroma/。
3. 根据 query embedding 从向量库中按相似度取回当前 session 的 chunk。
4. 支持按 session 删除向量，保持清空会话时 SQLite 与 ChromaDB 状态一致。

说明：
- 当前模块只负责向量库读写，不负责 SQLite 文档持久化。
- SQLite 仍然是文档、chunk、会话和检索日志的主数据源。
- ChromaDB 只保存向量检索需要的 embedding、正文和 metadata。
"""

from __future__ import annotations

# 导入 Path，用于解析和创建 ChromaDB 持久化目录
from pathlib import Path
from typing import Any

# 导入统一配置对象，读取 VECTOR_STORE_DIR 等配置
from backend.config import settings
# 导入 embedding 生成函数
from backend.rag.embedding_client import generate_embedding, generate_embeddings


# 项目根目录，用于将 ./data/chroma 这类相对路径解析为项目内绝对路径
PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Chroma collection 名称。当前项目先使用一个 collection，通过 metadata.session_id 区分会话
COLLECTION_NAME = "rag_chunks"


def resolve_vector_store_dir() -> Path:
    """
    解析 ChromaDB 持久化目录。

    函数说明：
    1. 读取 settings.vector_store_dir。
    2. 如果是相对路径，则基于项目根目录解析。
    3. 创建目录，确保 ChromaDB 可以写入本地文件。

    :return: ChromaDB 持久化目录绝对路径
    """
    # 将配置中的目录字符串转换为 Path
    store_dir = Path(settings.vector_store_dir)

    # 如果配置的是相对路径，则基于项目根目录补全
    if not store_dir.is_absolute():
        store_dir = PROJECT_ROOT / store_dir

    # 创建 ChromaDB 持久化目录
    store_dir.mkdir(parents=True, exist_ok=True)

    # 返回绝对路径
    return store_dir.resolve()


def get_chroma_collection():
    """
    获取 ChromaDB collection。

    函数说明：
    1. 延迟导入 chromadb，避免没有安装依赖时影响普通 keyword RAG。
    2. 使用 PersistentClient，把向量库写入本地 data/chroma/。
    3. 获取或创建固定名称的 collection。

    :return: ChromaDB collection 对象
    """
    try:
        # 延迟导入 chromadb，便于 keyword 模式下不强制加载向量库依赖
        import chromadb
    except ImportError as exc:
        raise ImportError("当前环境未安装 chromadb，请先执行 pip install chromadb。") from exc

    # 获取向量库持久化目录
    store_dir = resolve_vector_store_dir()
    # 创建 ChromaDB 持久化客户端
    client = chromadb.PersistentClient(path=str(store_dir))
    # 获取或创建 collection
    return client.get_or_create_collection(name=COLLECTION_NAME)


def build_vector_id(session_id: str, db_chunk_id: int | str) -> str:
    """
    构造向量库中的唯一 ID。

    函数说明：
    1. session_id 用于隔离不同会话。
    2. db_chunk_id 对应 SQLite document_chunks.id。
    3. 两者组合可以稳定定位同一个 chunk 的向量记录。

    :param session_id: 当前会话 ID
    :param db_chunk_id: SQLite 文本块主键 ID
    :return: ChromaDB 向量记录 ID
    """
    return f"{session_id}:{db_chunk_id}"


def delete_session_vectors(session_id: str | None) -> None:
    """
    删除某个 session 对应的所有向量记录。

    函数说明：
    1. 如果 session_id 为空，直接返回。
    2. 根据 metadata.session_id 删除 ChromaDB 中的旧向量。
    3. 用于清空会话时保持向量库状态一致。

    :param session_id: 当前会话 ID
    :return: None
    """
    # 没有 session_id 时没有可删除的向量
    if not session_id:
        return

    # 获取 Chroma collection
    collection = get_chroma_collection()

    # 按 session_id 删除旧向量
    collection.delete(where={"session_id": session_id})


def upsert_document_chunks(session_id: str, chunks: list[dict[str, Any]]) -> None:
    """
    将文档 chunk 写入 ChromaDB 向量库。

    函数说明：
    1. 接收 SQLite 保存后返回的 chunk 元数据。
    2. 为每个 chunk 正文生成 embedding。
    3. 将 embedding、正文和 metadata 写入 ChromaDB。
    4. metadata 保存 session_id、document_id、db_chunk_id、file_name、chunk_id。

    :param session_id: 当前会话 ID
    :param chunks: SQLite 保存后的 chunk 元数据列表
    :return: None
    """
    # 如果没有会话 ID 或没有 chunk，就无需写入向量库
    if not session_id or not chunks:
        return

    # 提取 chunk 正文
    documents = [chunk.get("text", "") for chunk in chunks]
    # 批量生成 embedding
    embeddings = generate_embeddings(documents)

    # 如果 embedding 数量和 chunk 数量不一致，说明 embedding 服务返回异常
    if len(embeddings) != len(chunks):
        raise ValueError("生成 embedding 数量与 chunk 数量不一致。")

    # 构造 ChromaDB 记录 ID
    ids = [build_vector_id(session_id, chunk["db_chunk_id"]) for chunk in chunks]

    # 构造 metadata，字段要保持简单类型，便于 ChromaDB 过滤
    metadatas = [
        {
            "session_id": session_id,
            "document_id": int(chunk.get("document_id", 0)),
            "db_chunk_id": int(chunk.get("db_chunk_id", 0)),
            "file_name": chunk.get("file_name") or "未命名文件",
            "chunk_id": int(chunk.get("chunk_id", 0)),
        }
        for chunk in chunks
    ]

    # 获取 Chroma collection
    collection = get_chroma_collection()

    # 写入或更新向量记录
    collection.upsert(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas,
    )


def retrieve_similar_chunks(session_id: str | None, query: str, top_k: int = 3) -> list[dict[str, Any]]:
    """
    使用 ChromaDB 根据 query 相似度检索 chunk。

    函数说明：
    1. 为 query 生成 embedding。
    2. 在 ChromaDB 中按 session_id 过滤当前会话的向量。
    3. 返回和现有 RAG 服务层兼容的 chunk 字典结构。

    :param session_id: 当前会话 ID
    :param query: 当前用户问题
    :param top_k: 最多返回几个 chunk
    :return: 命中的 chunk 列表
    """
    # 如果缺少 session_id 或 query，则无法检索
    if not session_id or not query.strip():
        return []

    # 为 query 生成 embedding
    query_embedding = generate_embedding(query)

    # 如果 embedding 为空，直接返回空列表
    if not query_embedding:
        return []

    # 获取 Chroma collection
    collection = get_chroma_collection()

    # 按 session_id 过滤，只检索当前会话的文档 chunk
    result = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k, # 返回的结果条数
        where={"session_id": session_id},
        include=["documents", "metadatas", "distances"], # 要返回的字段
    )

    # 取出 ChromaDB 返回的第一组查询结果
    documents = result.get("documents", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]

    # 前端预览文本长度限制，至少保留 80 个字符
    preview_limit = max(settings.rag_preview_text_limit, 80)

    # 整理成现有 RAG service 能直接消费的 chunk 字典结构
    matched_chunks = []
    for text, metadata, distance in zip(documents, metadatas, distances):
        # Chroma 距离越小越相似，这里转成越大越好的相似度分数，便于前端展示
        score = round(1 / (1 + float(distance)), 4)

        # 如果相似度低于阈值，则说明只是“最接近”而不是“有依据”，不返回给 RAG 回答和前端引用展示
        if score < settings.rag_vector_score_threshold:
            continue

        # 将 metadata 和正文合并成统一 chunk 结构
        matched_chunks.append(
            {
                # 当前检索结果排名（1 表示最相关）
                # 方便前端展示引用顺序和调试检索效果
                "rank": len(matched_chunks) + 1,
                "db_chunk_id": metadata.get("db_chunk_id"),
                "document_id": metadata.get("document_id"),
                "file_name": metadata.get("file_name"),
                "chunk_id": metadata.get("chunk_id"),
                "text": text or "",
                "text_preview": (text or "")[:preview_limit],
                "text_length": len(text or ""),
                "score": score,
            }
        )

    return matched_chunks
