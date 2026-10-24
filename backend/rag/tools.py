"""将现有 RAG 检索服务封装成可由 Agent 调用的 LangChain Tool。"""

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.rag.service import (
    build_rag_context_from_chunks,
    retrieve_rag_chunks_with_mode,
)


class KnowledgeSearchInput(BaseModel):
    """调用方只提供 query，检索范围由服务端绑定。"""

    model_config = ConfigDict(extra="forbid")
    query: str = Field(description="需要在企业知识库中查询的问题或关键词", min_length=1)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("查询内容不能为空。")
        return value


def create_knowledge_search_tool(
    *,
    session_id: str | None,
    knowledge_base_id: str | None = None,
    knowledge_base_type_filter: str | None = None,
    top_k: int = 3,
) -> StructuredTool:
    """为当前请求构造工具，保留既有检索、过滤和无命中语义。

    ID 和分类应来自已校验的请求上下文，不由模型选择；工具本身不替代权限校验。
    工具不写检索日志，仍由 Agent 服务统一记录最终生效的检索结果。
    """
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise ValueError("top_k 必须为正整数。")

    def search_knowledge_base(query: str) -> dict[str, Any]:
        chunks, retrieval_mode = retrieve_rag_chunks_with_mode(
            session_id=session_id,
            query=query,
            knowledge_base_id=knowledge_base_id,
            knowledge_base_type_filter=knowledge_base_type_filter,
            top_k=top_k,
        )
        return {
            "query": query,
            "chunks": chunks,
            "retrieval_mode": retrieval_mode,
            "context": build_rag_context_from_chunks(chunks),
        }

    return StructuredTool.from_function(
        func=search_knowledge_base,
        name="search_knowledge_base",
        description=(
            "查询当前授权范围内的企业知识库，获取制度、流程、IT 支持或产品资料的依据。"
            "输入查询文本；返回文档片段、分数、引用上下文和实际检索方式。"
            "没有命中表示缺少依据，不应据此编造答案。"
        ),
        args_schema=KnowledgeSearchInput,
    )
