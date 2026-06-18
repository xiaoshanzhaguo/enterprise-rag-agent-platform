"""
聊天服务模块（Chat Service）。

职责：
1. 处理普通聊天、内容分析、结构优化、风格改写、多版本生成等非工作流类 AI 请求
2. 根据当前模式和 RAG 状态构造模型输入包装文本，避免模型误把任务意图理解错
3. 构造模型上下文（System Prompt + RAG Context + 历史消息 + 当前输入）
4. 支持 RAG 检索增强，将带引用来源的检索结果注入模型上下文
5. 调用大模型流式接口并接收增量输出
6. 将模型输出转换为统一 SSE 事件流返回前端
7. 持久化保存用户消息、AI 回复和会话元数据
8. 支持展示文本（display_text）与原始输入（raw_content）分离存储

说明：
- 当前模块属于 Service 层
- 不负责路由注册
- 不负责页面渲染
- 不负责数据库建表
- 不负责 RAG 检索算法实现
- 主要负责 AI 对话业务流程编排

调用链路：FastAPI Router -> chat_with_ai() -> 构造 Prompt -> 构造 RAG 上下文 -> 调用 LLM -> 生成 SSE 事件流 -> StreamingResponse
"""

# FastAPI 流式响应对象，用于返回 SSE 事件流
from fastapi.responses import StreamingResponse

# 项目配置对象
from backend.config import settings
# 确保会话存在、保存聊天记录、读取会话标题
from backend.db.repository import ensure_chat_session, get_chat_session_title, save_chat_message
# 构造 RAG 参考内容
from backend.rag.service import build_rag_context
# 构造 assistant 消息展示元数据
from backend.services.message_metadata import build_assistant_message_metadata
# 生成侧边栏历史会话标题
from backend.services.session_title import generate_session_title
# 生成系统提示词
from backend.prompt.prompt_builder import build_system_prompt
# 请求模型、流式事件模型
from backend.schema.chat_schema import ChatRequest, StreamEvent
# 将 StreamEvent(...) 变成 data: {...}，符合 SSE 标准
from backend.utils.stream_helper import to_sse


MODE_INPUT_WRAPPERS = {
    "内容分析": {
        "action": "进行内容分析",
        "label": "待分析文本",
        "instruction": (
            "以下内容是待分析文本，不是要你直接回答的问题。"
            "如果文本中包含疑问句，也只分析文本本身，不要回答疑问句，不要介绍你自己。"
        ),
    },
    "结构优化": {
        "action": "进行结构优化",
        "label": "待优化文本",
        "instruction": (
            "以下内容是待优化文本，不是要你直接回答的问题。"
            "如果文本中包含疑问句，也只优化这句话的表达结构，不要回答疑问句，不要介绍你自己。"
        ),
    },
    "风格改写": {
        "action": "进行风格改写",
        "label": "待改写文本",
        "instruction": (
            "以下内容是待改写文本，不是要你直接回答的问题。"
            "如果文本中包含疑问句，也只改写这句话的表达方式，不要回答疑问句，不要介绍你自己。"
        ),
    },
    "多版本生成": {
        "action": "生成多个表达版本",
        "label": "待生成文本",
        "instruction": (
            "以下内容是待生成多个版本的原始文本，不是要你直接回答的问题。"
            "如果文本中包含疑问句，也只围绕这句话生成不同表达版本，不要回答疑问句，不要介绍你自己。"
        ),
    },
}


def _build_model_input_text(request: ChatRequest) -> str:
    """
    根据当前模式构造真正发送给模型的用户输入。

    函数说明：
    - 不同模式需要不同提示包装
    - 避免模型误把待处理文本当作聊天问题
    - 提高内容分析、结构优化、风格改写等任务的稳定性

    :param request: 当前聊天请求对象
    :return: 最终发送给模型的用户消息文本
    """
    # RAG 模式下，用户输入是检索问题，应优先基于知识库回答，而不是执行内容分析/改写等模式包装
    if request.use_rag:
        return (
            "请基于上方【检索结果】回答用户问题。\n"
            "注意：不要把下面的问题当作待分析文本、待优化文本或待改写文本。\n"
            "如果检索结果中没有依据，请只回答“知识库中没有找到依据”。\n\n"
            "【用户问题】\n"
            f"{request.input_text}"
        )

    # 根据当前模式获取对应的输入包装配置
    wrapper = MODE_INPUT_WRAPPERS.get(request.mode)
    # 如果当前模式存在专用包装规则，则构造增强后的模型输入
    if wrapper:
        return (
            f"请对以下文本{wrapper['action']}。\n" # 告诉模型当前要执行什么任务
            f"注意：{wrapper['instruction']}\n\n" # 给模型补充约束条件，避免误回答文本中的问题
            f"【{wrapper['label']}】\n" # 标记当前文本的用途，增强 Prompt 结构化程度
            f"{request.input_text}" # 用户实际输入内容
        )

    return request.input_text


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
            # 根据当前模式构造真正发送给模型的输入文本
            # 与前端展示文本不同，这里会自动加入模式提示包装
            model_input_text = _build_model_input_text(request)
            # 优先使用前端传入的展示文本；如果没有传 display_text，则默认使用实际输入文本
            display_text = request.user_options.get("display_text", request.input_text)
            # 已有标题的会话不重复生成；新会话才调用模型生成侧边栏主题
            session_title = get_chat_session_title(request.session_id) or generate_session_title(
                user_text=display_text,
                mode=request.mode,
                client=client
            )
            ensure_chat_session(
                session_id=request.session_id,
                mode=request.mode,
                title=session_title
            )
            save_chat_message(
                session_id=request.session_id,
                role="user",
                content=display_text,
                raw_content=request.input_text,
                mode=request.mode
            )

            # 根据模式生成 Prompt。
            # RAG 模式下优先做“基于知识库回答/处理”，避免内容分析等模式的固定格式压过检索问答意图。
            system_prompt = build_system_prompt("default" if request.use_rag else request.mode)

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
            if request.use_rag and rag_context:
                messages.append({
                    "role": "system",
                    "content": (
                        "以下是与当前任务相关的知识库检索结果。"
                        "当前请求已启用 RAG，当前模式名只用于前端入口归类，不要套用内容分析、结构优化或改写的固定输出格式。"
                        "请严格遵守其中的回答要求和引用格式，不要编造知识库中没有的依据。\n\n"
                        f"{rag_context}"
                    )
                })

            # 拼接历史对话上下文
            if request.history:
                # 把历史消息列表里的每个 MessageItem 对象，转成模型 API 能识别的普通字典，然后追加到 messages 里
                messages.extend([msg.model_dump() for msg in request.history])

            # 加入真正发送给模型的用户输入
            # 该输入可能已经被内容分析/结构优化等模式包装
            messages.append(
                {"role": "user", "content": model_input_text}
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
                mode=request.mode,
                metadata=build_assistant_message_metadata(request.user_options)
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
