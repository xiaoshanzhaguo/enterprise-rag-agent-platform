"""
聊天消息展示元数据模块。

职责：
1. 从前端请求参数中提取可持久化的消息展示元数据
2. 过滤异常结构，避免无效数据写入数据库
3. 为普通聊天和工作流保存 assistant 消息时提供统一辅助函数

说明：
- 当前主要保存 RAG 引用来源和命中原文片段
- 元数据只影响前端历史展示，不参与模型上下文构造
- 适合保存“某一条回答当时对应的引用模块”这类和消息强绑定的数据
"""

from typing import Any


def build_assistant_message_metadata(user_options: dict[str, Any]) -> dict[str, Any] | None:
    """
    从请求扩展参数中构造 assistant 消息元数据。

    函数说明：
    1. 读取前端传入的 rag_preview_chunks 和 rag_status_info。
    2. 只保留结构合法的引用片段和文档状态。
    3. 如果没有可保存内容，则返回 None，避免数据库写入空 JSON。

    :param user_options: ChatRequest.user_options 扩展参数
    :return: 可写入 chat_messages.metadata_json 的元数据字典；没有数据时返回 None
    """
    # 创建空元数据容器，后续只放入合法字段
    metadata: dict[str, Any] = {}

    # 读取当前回答对应的 RAG 命中片段
    rag_preview_chunks = user_options.get("rag_preview_chunks")
    # 只有列表结构才符合前端引用模块渲染要求
    if isinstance(rag_preview_chunks, list):
        # 过滤掉非字典元素，避免脏数据影响历史消息渲染
        valid_chunks = [
            chunk
            for chunk in rag_preview_chunks
            if isinstance(chunk, dict)
        ]
        # 至少存在一个有效片段时才保存
        if valid_chunks:
            metadata["rag_preview_chunks"] = valid_chunks

    # 读取当前回答对应的 RAG 文档状态
    rag_status_info = user_options.get("rag_status_info")
    # 只有字典结构才写入元数据
    if isinstance(rag_status_info, dict) and rag_status_info:
        metadata["rag_status_info"] = rag_status_info

    # 没有任何可保存字段时返回 None，数据库字段保持 NULL
    return metadata or None
