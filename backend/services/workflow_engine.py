"""
工作流服务模块（Workflow Service）。

职责：
1. 实现多步骤 AI 工作流处理流程
2. 将复杂任务拆分为多个独立步骤执行
3. 支持每个步骤单独流式输出
4. 支持带引用来源的 RAG 检索增强
5. 统一转换为 SSE 事件流返回前端
6. 保存用户输入和工作流最终结果

说明：
- 当前模块属于 Service 层
- 基于 chat_service 扩展而来
- 一个工作流包含多个步骤
- 每个步骤都会单独调用一次模型

工作流流程：用户输入 -> summary -> analysis -> suggestion -> 汇总结果 -> 返回前端
"""

# 导入 JSON 工具，用于将工作流结果字典转换为 JSON 字符串
import json
# 导入正则表达式模块，用于识别、匹配模型输出中的步骤标签
import re

# 导入 FastAPI 流式响应对象，用于返回 SSE 事件流
from fastapi.responses import StreamingResponse

# 导入项目配置对象，用于读取模型名称等运行配置
from backend.config import settings
# 导入数据库持久化函数，用于确保会话存在并保存聊天信息
from backend.db.repository import ensure_chat_session, save_chat_message
# 导入系统提示词构造函数，根据当前模式生成 system prompt
from backend.prompt.prompt_builder import build_system_prompt
# 导入请求模型和流式事件模型，用于约束请求结构和 SSE 事件结构
from backend.schema.chat_schema import ChatRequest, StreamEvent
# 导入 assistant 消息展示元数据构造函数，用于保存当前回答对应的引用模块
from backend.services.message_metadata import build_assistant_message_metadata
# 导入 SSE 格式化工具，将 StreamEvent 转换为 text/event-stream 格式
from backend.utils.stream_helper import to_sse
# 导入 RAG 上下文构造函数，用于为当前请求生成检索增强参考内容
from backend.rag.service import build_rag_context


# 给模型增加约束
STEP_OUTPUT_RULES = """
    输出要求：
    1. 只输出当前步骤的正文内容。
    2. 不要输出“当前步骤：...”“步骤：...”“summary / analysis / suggestion”等步骤标签。
    3. 不要输出 Markdown 一级/二级/三级标题，前端会统一渲染步骤标题。
""".strip()


# 在解析 workflow 输出时，把“步骤标签行”识别出来，方便做清洗、分段或格式化
STEP_LABEL_PATTERN = re.compile(
    # 识别一整行是不是“步骤标题行”，并且兼容 Markdown 前缀、可选加粗、中文英文步骤名、中文英文冒号、大小写差异
    r"^\s*(?:[#>*\-\s]*)?(?:\*\*)?(?:当前)?(?:步骤|环节|任务)\s*[:：]\s*"
    r"(?:总结|内容总结|问题分析|分析|优化建议|建议|summary|analysis|suggestion)"
    r"(?:\*\*)?\s*$",
    re.IGNORECASE,
)


def clean_workflow_step_text(text: str) -> str:
    """
    清理模型输出中的步骤标题，避免和前端统一标题重复。

    功能：
    1. 删除开头空行
    2. 删除模型多输出的步骤标签
    3. 删除标题后的空行
    4. 返回干净正文

    :param text: 模型生成的步骤内容
    :return: 清洗后的正文内容
    """
    # 把这段文本按行拆成列表，方便后面一行一行处理
    lines = text.strip().splitlines()

    # 只要列表还不空，并且第一行是空行，就一直删掉第一行。即：先把开头多余的空行删掉
    while lines and not lines[0].strip():
        lines.pop(0)

    # 如果第一行是模型多输出的“步骤标题”，那就删掉它
    if lines and STEP_LABEL_PATTERN.match(lines[0]):
        lines.pop(0)

    # 防止删掉步骤标签后，正文前面还残留一个空行
    while lines and not lines[0].strip():
        lines.pop(0)

    # 返回一段“去掉步骤标签、去掉开头空行”的干净正文文本
    return "\n".join(lines).strip()


def run_workflow_stream(request: ChatRequest, client) -> StreamingResponse:
    """
    执行多步骤工作流并返回 SSE 流式响应。

    功能：
    1. 保存用户输入
    2. 构造工作流步骤
    3. 按顺序执行各步骤
    4. 每个步骤单独调用模型
    5. 支持 RAG 检索增强
    6. 实时推送步骤执行结果
    7. 保存最终工作流结果

    :param request: 当前工作流请求对象
    :param client: OpenAI/DeepSeek 客户端
    :return: StreamingResponse, 返回标准 SSE 工作事件流
    """

    def generate():
        """
        生成工作流事件流。

        流程：
        1. 构造 system prompt
        2. 定义多步骤工作流
        3. 逐步调用模型并流式输出
        4. 每个步骤结束后发送 step_complete事件
        5. 所有步骤结束后发送 final 事件
        6. 若发生异常，则发送 error 事件
        """
        final_result = {}

        try:
            # 优先使用前端传入的展示文本；如果没有传 display_text，则默认使用实际输入文本
            display_text = request.user_options.get("display_text", request.input_text)
            ensure_chat_session(
                session_id=request.session_id,
                mode=request.mode,
                title=display_text[:80]
            )
            save_chat_message(
                session_id=request.session_id,
                role="user",
                content=display_text,
                raw_content=request.input_text,
                mode=request.mode
            )

            # 根据模式生成 Prompt
            system_prompt = build_system_prompt(request.mode)

            # 通知前端：整个工作流开始
            yield to_sse(
                StreamEvent(
                    event_type="workflow_start",
                    session_id=request.session_id,
                    task_type=request.task_type,
                    content="工作流已开始"
                )
            )

            # 定义工作流步骤
            steps = [
                {
                    "name": "summary",
                    "prompt": f"""
                        请仅总结以下内容体现出的项目目前状态。

                        {STEP_OUTPUT_RULES}
                        4. 只写当前已经完成、已经具备、正在面向的状态。
                        5. 不分析问题，不列出不足，不给优化建议。

                        内容如下：
                        {request.input_text}
                    """.strip()
                },
                {
                    "name": "analysis",
                    "prompt": f"""
                        请仅分析以下内容中已经明确出现的问题或不足。
        
                        要求：
                        1. 只分析输入中已明确出现的问题，不补充新问题。
                        2. 不给建议，不给方案，不补充新背景。
                        3. 输出简洁、结构化、逐条列出。
                        4. 不要输出“当前步骤：问题分析”等步骤标签或标题。
                        5. 不要重复总结项目当前状态。
        
                        内容如下：
                        {request.input_text}
                    """.strip()
                },
                {
                    "name": "suggestion",
                    "prompt": f"""
                        请仅基于以下内容中已经明确出现的问题，给出 3 条最值得优先执行的优化建议。
        
                        要求：
                        1. 只给建议，不重复总结和问题分析。
                        2. 每条建议必须直接对应输入中已出现的问题。
                        3. 不补充新技术、新框架、新工具、新平台、新指标。
                        4. 不要给通用行业方案，只给和输入内容直接相关的建议。
                        5. 输出简洁、按重要性排序。
                        6. 不要输出“当前步骤：优化建议”等步骤标签或标题。
                        7. 不要点名引入输入中没有出现的新技术、协议或类型系统；建议应落在整理现有逻辑、统一字段、补充必要校验和文档上。
        
                        内容如下：
                        {request.input_text}
                    """.strip()
                }
            ]

            # 依次执行每个工作流步骤
            for step in steps:
                step_name = step["name"]
                step_text = ""

                # 通知前端：当前步骤开始执行
                yield to_sse(
                    StreamEvent(
                        event_type="step_start",
                        session_id=request.session_id,
                        task_type=request.task_type,
                        step_name=step_name,
                        content=f"{step_name} 步骤开始"
                    )
                )

                # 当前步骤是否启用 RAG
                rag_context = ""
                if request.use_rag:
                    # 使用“步骤名称 + 用户输入”作为检索 query
                    # 让 summary / analysis / suggestion 各自更贴近当前步骤要求
                    step_query = f"{step_name} {request.input_text}"
                    rag_context = build_rag_context(
                        session_id=request.session_id,
                        query=step_query,
                        top_k=request.rag_top_k
                    )

                # 组装本步骤的消息上下文
                messages = [{"role": "system", "content": system_prompt}]

                # 如果启用了 RAG，则加入和当前步骤相关的参考内容
                if request.use_rag and rag_context:
                    messages.append({
                        "role": "system",
                        "content": (
                            "以下是与当前步骤相关的知识库检索结果。"
                            "请严格遵守其中的回答要求和引用格式，不要编造知识库中没有的依据。\n\n"
                            f"{rag_context}"
                        )
                    })

                # 如果存在历史消息，则拼接到上下文中
                if request.history:
                    messages.extend([msg.model_dump() for msg in request.history])

                # 加入当前步骤提示词
                messages.append({"role": "user", "content": step["prompt"]})

                # 调用模型接口，开启当前步骤的流式输出
                response = client.chat.completions.create(
                    model=settings.llm_model,
                    messages=messages,
                    stream=True
                )

                # 持续读取当前步骤的增量输出
                for chunk in response:
                    delta = chunk.choices[0].delta.content

                    if not delta:
                        continue

                    step_text += delta

                    # 向前端发送当前步骤的增量内容
                    yield to_sse(
                        StreamEvent(
                            event_type="delta",
                            session_id=request.session_id,
                            task_type=request.task_type,
                            step_name=step_name,
                            content=delta
                        )
                    )

                step_text = clean_workflow_step_text(step_text)

                # 保存当前步骤的完整结果
                final_result[step_name] = step_text

                # 通知前端：当前步骤已经完成
                yield to_sse(
                    StreamEvent(
                        event_type="step_complete",
                        session_id=request.session_id,
                        task_type=request.task_type,
                        step_name=step_name,
                        content=step_text,
                    )
                )

            # 将工作流结果字典转换为 JSON 字符串，并保留中文字符原样显示
            final_content = json.dumps(final_result, ensure_ascii=False)

            # 保存整个工作流结果
            save_chat_message(
                session_id=request.session_id,
                role="assistant",
                content=final_content,
                raw_content=final_content,
                mode=request.mode,
                metadata=build_assistant_message_metadata(request.user_options)
            )

            # 所有步骤执行完成后，发送最终事件
            yield to_sse(
                StreamEvent(
                    event_type="final",
                    session_id=request.session_id,
                    task_type=request.task_type,
                    content=final_content,
                    is_final=True
                )
            )

        except Exception as e:
            # 若执行过程中发生异常，则返回结构化错误事件
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
