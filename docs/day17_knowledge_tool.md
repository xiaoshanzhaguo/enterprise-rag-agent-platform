# 第 17 天：知识库检索 Tool

`backend/rag/tools.py` 提供 LangChain `StructuredTool`，工具名称为 `search_knowledge_base`。
复用现有 RAG 服务，向量模式仍走 ChromaDB，并保留关键词兜底、分类过滤、分数、来源和无命中处理，无需增加依赖或重建索引。

```python
from backend.rag.tools import create_knowledge_search_tool

tool = create_knowledge_search_tool(
    session_id="当前会话 ID",
    knowledge_base_id="已校验的知识库 ID",
    knowledge_base_type_filter="hr",
    top_k=3,
)
result = tool.invoke({"query": "年假如何申请？"})
# result: query、chunks、retrieval_mode、context
```

调用方只传入 query。会话、知识库、分类和 top_k 在服务端构造工具时绑定，不能通过工具参数覆盖。绑定范围不替代 API 层的权限校验。缺少知识库和会话范围时返回 no_hit；空白查询或额外参数会被校验拒绝。

`agent_service._retrieve_agent_rag_chunks` 已通过工具执行主查询及原问题重试。Agent 保持现有固定编排，由 Router 决定是否查询知识库，尚未改为由模型使用 bind_tools 自主循环选工具。工具返回原始 chunks，引用、SSE 和最终检索日志仍由原服务处理，避免重复写日志。检索异常继续向上传递，不伪装成无命中。

离线检查：

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

测试覆盖工具参数和范围、元数据、向量命中、关键词兜底、关闭兜底后的无命中，以及 Agent 原问题重试。测试替换底层向量检索，不下载 embedding 模型、不调用在线模型、不修改实际知识库。
