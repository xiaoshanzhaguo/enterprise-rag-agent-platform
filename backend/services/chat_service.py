from fastapi.responses import StreamingResponse

from backend.config import settings
from backend.rag.service import build_rag_context
from backend.prompt.prompt_builder import build_system_prompt
from backend.schema.chat_schema import ChatRequest, StreamEvent
from backend.utils.stream_helper import to_sse

# 负责聊天逻辑
def chat_with_ai(request: ChatRequest, client) -> StreamingResponse:
    """
    聊天服务。

    将一次 AI 对话请求封装成 SSE 事件流返回给前端，
    支持开始事件、增量事件、最终事件和错误事件。
    """

    def generate():
        """
        生成流式事件。

        流程：
        1. 构造 system_prompt
        2. 组装消息上下文 (system + history + 当前输入)
        3. 调用模型流式输出
        4. 持续发送 delta 事件
        5. 最终发送 final 事件
        6. 若发生异常，则发送 error 事件
        """
        full_text = ""

        try:
            # 根据当前助手人设或风格生成系统提示词
            system_prompt = build_system_prompt(request.persona)

            # 通知前端：当前任务已开始
            yield to_sse(
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
                delta = chunk.choices[0].delta.content

                if not delta:
                    continue

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

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
    )
