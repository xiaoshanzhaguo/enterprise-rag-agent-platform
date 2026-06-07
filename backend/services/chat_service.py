"""
聊天服务模块（Chat Service）。

职责：
1. 处理普通聊天、内容分析、结构优化等非工作流类 AI 请求
2. 构造模型上下文（System Prompt + 历史消息 + 当前输入）
3. 支持 RAG 检索增强，将检索结果注入模型上下文
4. 调用大模型接口并接收流式输出
5. 将模型输出转换为统一 SSE 事件流返回前端
6. 持久化保存用户消息和模型回复

说明：
- 当前模块属于 Service 层
- 不负责路由注册
- 不直接操作前端界面
- 不负责数据库建表
- 主要负责 AI 对话业务流程编排

调用链路：FastAPI Router -> chat_with_ai() -> 构造 Prompt -> 构造 RAG 上下文 -> 调用 LLM -> 生成 SSE 事件流 -> StreamingResponse
"""

# FastAPI 流式响应对象，用于返回 SSE 事件流
from fastapi.responses import StreamingResponse

# 项目配置对象
from backend.config import settings
# 确保会话存在、保存聊天记录
from backend.db.repository import ensure_chat_session, save_chat_message
# 构造 RAG 参考内容
from backend.rag.service import build_rag_context
# 生成系统提示词
from backend.prompt.prompt_builder import build_system_prompt
# 请求模型、流式事件模型
from backend.schema.chat_schema import ChatRequest, StreamEvent
# 将 StreamEvent(...) 变成 data: {...}，符合 SSE 标准
from backend.utils.stream_helper import to_sse


def chat_with_ai(request: ChatRequest, client) -> StreamingResponse:
    """
    处理 AI 聊天请求并返回 SSE 流式响应。

    功能：
    1. 保存用户消息
    2. 构造系统提示词
    3. 构造 RAG 检索上下文
    4. 组装模型上下文消息
    5. 调用大模型流式接口
    6. 持续向前端推送 SSE 事件
    7. 保存模型回复

    :param request: 当前聊天请求对象
    :param client: OpenAI/DeepSeek 客户端实例
    :return: StreamingResponse, 返回标准 SSE 事件流
    """

    def generate():
        """
        生成 SSE 流式事件。

        流程：用户输入 -> 调用模型 -> 持续接收 delta -> 转成 StreamEvent -> yield 返回前端
        """
        # 累计模型完整回复内容
        full_text = ""

        try:
            # 优先使用前端传入的展示文本；如果没有传 display_text，则默认使用实际输入文本
            display_text = request.user_options.get("display_text", request.input_text)
            ensure_chat_session(
                session_id=request.session_id,
                mode=request.persona,
                title=display_text[:80] # 最多取前80个字符作为标题
            )
            save_chat_message(
                session_id=request.session_id,
                role="user",
                content=display_text,
                raw_content=request.input_text,
                mode=request.persona
            )

            # 根据模式生成 Prompt
            system_prompt = build_system_prompt(request.persona)

            # 通知前端：当前任务已开始。发送 SSE 事件
            # 后端不是等全部回答生成完再返回，而是每生成一小段，就包装成 SSE 格式，立刻交给前端
            # 生成器把一段 SSE 格式字符串交给 StreamingResponse，由 StreamingResponse 持续发送给前端
            yield to_sse(
                # StreamEvent，先创建一个结构化事件对象
                StreamEvent(
                    event_type="workflow_start",
                    session_id=request.session_id,
                    task_type=request.task_type,
                    content="聊天任务已开始"
                )
            )

            # 根据当前请求决定是否启用 RAG
            rag_context = ""
            if request.use_rag:
                rag_context = build_rag_context(
                    session_id=request.session_id,
                    query=request.input_text,
                    top_k=request.rag_top_k
                )

            # 组装发送给模型的消息列表
            messages = [
                {"role": "system", "content": system_prompt}
            ]

            # 如果启用了 RAG，则把检索到的参考内容作为额外 system 上下文加入
            if rag_context:
                messages.append({
                    "role": "system",
                    "content": (
                        "以下是与当前任务相关的参考内容，请优先基于这些内容回答。"
                        "如果参考内容不足，再结合一般知识进行补充，但不要虚构文档中没有的信息。\n\n"
                        f"{rag_context}"
                    )
                })

            # 拼接历史对话上下文
            if request.history:
                # 把历史消息列表里的每个 MessageItem 对象，转成模型 API 能识别的普通字典，然后追加到 messages 里
                messages.extend([msg.model_dump() for msg in request.history])

            # 加入当前用户输入
            messages.append(
                {"role": "user", "content": request.input_text}
            )

            # 调用模型接口，开启流式输出
            response = client.chat.completions.create(
                model=settings.llm_model,
                messages=messages,
                stream=True
            )

            # 持续读取模型返回的增量内容
            for chunk in response:
                # 获取本次新增内容
                delta = chunk.choices[0].delta.content

                if not delta:
                    continue

                # 累计完整文本
                full_text += delta

                # 向前端发送增量事件
                yield to_sse(
                    StreamEvent(
                        event_type="delta",
                        session_id=request.session_id,
                        task_type=request.task_type,
                        content=delta
                    )
                )

            # 保存 AI 回复
            save_chat_message(
                session_id=request.session_id,
                role="assistant",
                content=full_text,
                raw_content=full_text,
                mode=request.persona
            )

            # 模型输出完成后，发送最终事件
            yield to_sse(
                StreamEvent(
                    event_type="final",
                    session_id=request.session_id,
                    task_type=request.task_type,
                    content=full_text,
                    is_final=True
                )
            )

        except Exception as e:
            # 发生异常时，也通过事件流返回结构化错误信息
            yield to_sse(
                StreamEvent(
                    event_type="error",
                    session_id=request.session_id,
                    task_type=request.task_type,
                    error_message=str(e),
                    is_final=True
                )
            )

    # StreamingResponse是 FastAPI 提供的流式响应对象
    # 接收一个可迭代对象或生成器，然后把里面 yield 出来的内容持续发送给前端
    return StreamingResponse(
        generate(),
        media_type="text/event-stream", # 告诉前端，这不是普通字符串，而是 SSE 事件流
    )
