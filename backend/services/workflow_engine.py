import json
import re

from fastapi.responses import StreamingResponse

from backend.config import settings
from backend.prompt.prompt_builder import build_system_prompt
from backend.schema.chat_schema import ChatRequest, StreamEvent
from backend.utils.stream_helper import to_sse
from backend.rag.service import build_rag_context


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
    清理模型偶尔输出的步骤标签，避免和前端统一标题重复。
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
    工作流流式服务。

    将多步骤内容分析流程封装为 SSE 事件流返回给前端，
    支持工作流开始、步骤开始、增量输出、步骤完成、最终完成和错误事件。
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
            # 根据当前助手人设或内容风格生成系统提示词
            system_prompt = build_system_prompt(request.persona)

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
                if rag_context:
                    messages.append({
                        "role": "system",
                        "content": (
                            "以下是与当前步骤相关的参考内容，请优先基于这些内容输出。"
                            "如果参考内容不足，不要编造文档中没有的信息。\n\n"
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

            # 所有步骤执行完成后，发送最终事件
            yield to_sse(
                StreamEvent(
                    event_type="final",
                    session_id=request.session_id,
                    task_type=request.task_type,
                    content=json.dumps(final_result, ensure_ascii=False),
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
