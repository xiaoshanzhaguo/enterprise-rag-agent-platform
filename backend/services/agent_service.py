"""
企业知识库问答 Agent 服务模块。

职责：
1. 判断用户问题是否需要查询企业知识库。
2. 需要知识库时执行 RAG 检索，并根据证据是否充分决定是否生成引用答案。
3. 不需要知识库时走普通对话流程，避免所有问题都盲目 RAG。
4. 将 Agent 决策、检索证据、生成回答三个阶段转换为 SSE 步骤事件返回前端。
5. 保存用户消息、Agent 最终结果和当前回答对应的引用元数据。

说明：
- 当前模块是轻量 Agent / Workflow 改造，不引入 LangChain 或复杂多 Agent。
- Agent 流程固定为：判断是否需要知识库 -> 检索证据 -> 生成回答。
- 如果知识库证据不足，Agent 不会强行回答，而是明确说明缺少依据。
- 如果问题不依赖企业知识库，Agent 会跳过检索，直接按普通对话回答。
"""

# 未来版本兼容特性, 让类型注解延迟解析
from __future__ import annotations

# 导入 JSON 工具，用于保存分步骤 Agent 结果
import json
# 导入类型标注工具，让函数输入输出更清晰
from typing import Any

# FastAPI 流式响应对象，用于返回 SSE 事件流
from fastapi.responses import StreamingResponse

# 项目配置对象，用于读取模型名称和引用预览长度
from backend.config import settings
# 数据库持久化函数，用于保存聊天会话、消息和 RAG 查询日志
from backend.db.repository import ensure_chat_session, save_chat_message, save_rag_query_with_hits
# 数据库文档状态查询函数，用于保存历史消息中的引用模块状态
from backend.rag.store import get_document_status
# RAG 服务函数，用于检索、组装引用上下文和生成来源标识
from backend.rag.service import (
    NO_HIT_RETRIEVAL_MODE,
    NO_RAG_EVIDENCE_MESSAGE,
    build_rag_context_from_chunks,
    build_source_label,
    retrieve_rag_chunks_with_mode,
)
# 系统提示词构造函数，用于复用普通对话 persona
from backend.prompt.prompt_builder import build_system_prompt
# 请求模型和流式事件模型
from backend.schema.chat_schema import ChatRequest, StreamEvent
# SSE 格式化工具
from backend.utils.stream_helper import to_sse


# Agent 第一步：判断是否需要知识库
STEP_JUDGE_KNOWLEDGE = "judge_knowledge"
# Agent 第二步：检索证据
STEP_RETRIEVE_EVIDENCE = "retrieve_evidence"
# Agent 第三步：生成回答
STEP_GENERATE_ANSWER = "generate_answer"

# 明确属于企业知识库、制度、文档、规则类问题的关键词
KNOWLEDGE_REQUIRED_KEYWORDS = {
    "公司",
    "员工",
    "手册",
    "制度",
    "政策",
    "规定",
    "流程",
    "审批",
    "申请",
    "报销",
    "发票",
    "试用期",
    "年假",
    "晚餐",
    "远程办公",
    "加班",
    "考勤",
    "请假",
    "合同",
    "采购",
    "账号",
    "权限",
    "客户",
    "工单",
    "知识库",
    "文档",
    "根据",
    "依据",
}

# 明确属于寒暄、身份询问或普通闲聊的关键词
GENERAL_CHAT_KEYWORDS = {
    "你好",
    "您好",
    "你是谁",
    "谢谢",
    "早上好",
    "下午好",
    "晚上好",
}


def decide_need_knowledge_base(question: str, use_rag: bool) -> tuple[bool, str]:
    """
    判断当前问题是否需要查询企业知识库。

    函数说明：
    1. 如果前端没有开启 RAG，直接判定为不需要知识库。
    2. 如果问题是短寒暄或普通身份询问，跳过知识库检索。
    3. 如果问题包含制度、流程、文档、企业规则等关键词，则需要知识库。
    4. 其他模糊问题默认不强制检索，避免把所有对话都包装成 RAG。

    :param question: 用户当前输入的问题
    :param use_rag: 前端是否开启 RAG
    :return: 二元组，第一项表示是否需要知识库，第二项是判断理由
    """
    # 去掉首尾空白，避免空格影响关键词判断
    normalized_question = question.strip()

    # 如果前端没有开启 RAG，则尊重用户设置，走普通对话
    if not use_rag:
        return False, "当前未开启 RAG，按普通对话处理。"

    # 空问题没有检索价值，直接走普通对话兜底
    if not normalized_question:
        return False, "当前问题为空，跳过知识库检索。"

    # 短寒暄或身份询问通常不需要企业知识库
    if len(normalized_question) <= 12 and any(keyword in normalized_question for keyword in GENERAL_CHAT_KEYWORDS):
        return False, "当前问题属于寒暄或普通对话，不需要查询企业知识库。"

    # 只要命中企业知识库相关关键词，就认为需要检索文档证据
    if any(keyword in normalized_question for keyword in KNOWLEDGE_REQUIRED_KEYWORDS):
        return True, "当前问题涉及企业制度、流程、文档或规则，需要查询知识库。"

    # 没有明确知识库信号时，不强行检索
    return False, "当前问题没有明显企业知识库信号，按普通对话处理。"


def _build_agent_rag_metadata(
    request: ChatRequest,
    matched_chunks: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    构造 Agent 回答对应的 RAG 展示元数据。

    函数说明：
    1. 将后端实际命中的 chunks 转换为前端历史消息可恢复的引用片段。
    2. 保存当前会话的 RAG 文档状态，供刷新页面后展示文档名。
    3. 如果没有命中片段，则返回 None，避免写入空元数据。

    :param request: 当前聊天请求对象
    :param matched_chunks: Agent 实际用于回答的检索片段
    :return: 可写入 chat_messages.metadata_json 的元数据；没有命中时返回 None
    """
    # 没有命中片段时，不保存引用模块元数据
    if not matched_chunks:
        return None

    # 前端预览文本长度限制，至少保留 80 个字符
    preview_limit = max(settings.rag_preview_text_limit, 80)
    # 存放前端可直接渲染的引用片段
    rag_preview_chunks = []

    # 遍历命中的检索片段，转换为和 /rag_preview 一致的展示结构
    for index, chunk in enumerate(matched_chunks, start=1):
        # 读取原文内容，后续用于完整片段展示和预览截断
        text = chunk.get("text", "")
        # 追加一个标准化引用片段
        rag_preview_chunks.append(
            {
                "rank": chunk.get("rank", index),
                "file_name": chunk.get("file_name"),
                "chunk_id": chunk.get("chunk_id"),
                "score": chunk.get("score", 0),
                "retrieval_mode": chunk.get("retrieval_mode") or NO_HIT_RETRIEVAL_MODE,
                "source": build_source_label(chunk),
                "text": text,
                "text_preview": chunk.get("text_preview") or text[:preview_limit],
                "text_length": len(text),
            }
        )

    # 返回前端历史恢复需要的元数据结构
    return {
        "rag_preview_chunks": rag_preview_chunks,
        "rag_status_info": get_document_status(request.session_id),
    }


def _stream_model_answer(
    client,
    messages: list[dict[str, str]],
):
    """
    调用大模型并逐段产出文本。

    函数说明：
    1. 使用当前统一 LLM_MODEL 发起流式请求。
    2. 过滤空 delta。
    3. 通过生成器逐段产出模型新增文本，供 Agent SSE 事件复用。

    :param client: OpenAI 兼容客户端
    :param messages: 已组装好的模型上下文
    :return: 生成器，每次产出一段模型增量文本
    """
    # 调用模型接口，开启流式输出
    response = client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        stream=True,
    )

    # 遍历模型流式响应
    for chunk in response:
        # 读取当前增量文本
        delta = chunk.choices[0].delta.content
        # 空内容没有展示价值，直接跳过
        if not delta:
            continue
        # 产出当前增量文本
        yield delta


def run_agent_stream(request: ChatRequest, client) -> StreamingResponse:
    """
    执行企业知识库问答 Agent 并返回 SSE 流式响应。

    功能：
    1. 保存用户消息。
    2. 判断当前问题是否需要企业知识库。
    3. 需要知识库时执行 RAG 检索并记录 rag_queries / rag_hits。
    4. 证据充足时生成带引用答案。
    5. 证据不足时明确拒答，不基于常识强答。
    6. 不需要知识库时跳过检索，走普通对话。
    7. 将三个阶段作为前端可见步骤返回。

    :param request: 当前 Agent 请求对象
    :param client: OpenAI/DeepSeek 客户端
    :return: StreamingResponse, 返回标准 SSE 事件流
    """

    def generate():
        """
        生成 Agent SSE 事件流。

        流程：
        1. 发送 workflow_start。
        2. 输出“判断是否需要知识库”步骤。
        3. 根据判断结果选择检索或跳过检索。
        4. 输出“生成回答”步骤。
        5. 保存最终结果并发送 final 事件。
        """
        # 最终保存的分步骤结果
        final_result: dict[str, str] = {}
        # 当前回答实际使用的 RAG 命中片段
        matched_chunks: list[dict[str, Any]] = []

        try:
            # 优先使用前端传入的展示文本，避免上传文件场景把全文展示进聊天气泡
            display_text = request.user_options.get("display_text", request.input_text)
            # 确保当前会话存在，并用用户问题作为会话标题
            ensure_chat_session(
                session_id=request.session_id,
                mode=request.persona,
                title=display_text[:80],
            )
            # 保存用户消息
            save_chat_message(
                session_id=request.session_id,
                role="user",
                content=display_text,
                raw_content=request.input_text,
                mode=request.persona,
            )

            # 通知前端：Agent 流程开始
            yield to_sse(
                StreamEvent(
                    event_type="workflow_start",
                    session_id=request.session_id,
                    task_type=request.task_type,
                    content="企业知识库问答 Agent 已开始",
                )
            )

            # 通知前端：开始判断是否需要知识库
            yield to_sse(
                StreamEvent(
                    event_type="step_start",
                    session_id=request.session_id,
                    task_type=request.task_type,
                    step_name=STEP_JUDGE_KNOWLEDGE,
                    content="正在判断是否需要知识库",
                )
            )

            # 执行轻量规则判断
            need_knowledge_base, decision_reason = decide_need_knowledge_base(
                question=request.input_text,
                use_rag=request.use_rag,
            )
            # 组装判断步骤展示文本
            judge_text = (
                f"判断结果：{'需要查询知识库' if need_knowledge_base else '不需要查询知识库'}。\n\n"
                f"判断依据：{decision_reason}"
            )
            # 保存判断步骤结果
            final_result[STEP_JUDGE_KNOWLEDGE] = judge_text
            # 通知前端：判断步骤完成
            yield to_sse(
                StreamEvent(
                    event_type="step_complete",
                    session_id=request.session_id,
                    task_type=request.task_type,
                    step_name=STEP_JUDGE_KNOWLEDGE,
                    content=judge_text,
                )
            )

            # 通知前端：开始检索证据或跳过检索
            yield to_sse(
                StreamEvent(
                    event_type="step_start",
                    session_id=request.session_id,
                    task_type=request.task_type,
                    step_name=STEP_RETRIEVE_EVIDENCE,
                    content="正在检索证据",
                )
            )

            # 默认检索状态为 no_hit，后续根据实际结果更新
            retrieval_mode = NO_HIT_RETRIEVAL_MODE
            # 如果判断需要知识库，则执行 RAG 检索
            if need_knowledge_base:
                # 调用 RAG 检索链路，返回命中片段和实际检索方式
                matched_chunks, retrieval_mode = retrieve_rag_chunks_with_mode(
                    session_id=request.session_id,
                    query=request.input_text,
                    top_k=request.rag_top_k,
                )
                # 保存本次检索记录，便于第 10 天可解释面板和数据库追踪继续生效
                save_rag_query_with_hits(
                    session_id=request.session_id,
                    query_text=request.input_text,
                    top_k=request.rag_top_k,
                    matched_chunks=matched_chunks,
                    retrieval_mode=retrieval_mode,
                    mode=request.persona,
                )
                # 命中证据时展示命中数量、检索方式和最高排名来源
                if matched_chunks:
                    first_source = build_source_label(matched_chunks[0])
                    retrieve_text = (
                        f"已命中 {len(matched_chunks)} 个知识库片段。\n\n"
                        f"检索方式：{retrieval_mode}\n\n"
                        f"优先证据：{first_source}"
                    )
                else:
                    retrieve_text = (
                        f"{NO_RAG_EVIDENCE_MESSAGE}。\n\n"
                        f"检索方式：{retrieval_mode}"
                    )
            else:
                # 不需要知识库时明确告诉用户本轮跳过检索
                retrieve_text = "已跳过知识库检索，本轮将按普通对话生成回答。"

            # 保存检索步骤结果
            final_result[STEP_RETRIEVE_EVIDENCE] = retrieve_text
            # 通知前端：检索步骤完成
            yield to_sse(
                StreamEvent(
                    event_type="step_complete",
                    session_id=request.session_id,
                    task_type=request.task_type,
                    step_name=STEP_RETRIEVE_EVIDENCE,
                    content=retrieve_text,
                )
            )

            # 通知前端：开始生成回答
            yield to_sse(
                StreamEvent(
                    event_type="step_start",
                    session_id=request.session_id,
                    task_type=request.task_type,
                    step_name=STEP_GENERATE_ANSWER,
                    content="正在生成回答",
                )
            )

            # 累计最终回答正文
            answer_text = ""

            # 需要知识库但没有可靠证据时，不调用模型强答
            if need_knowledge_base and not matched_chunks:
                answer_text = f"{NO_RAG_EVIDENCE_MESSAGE}。请补充更具体的问题，或上传包含该规则的企业文档后再提问。"
                # 以 delta 形式发送，保证前端生成回答步骤能看到内容
                yield to_sse(
                    StreamEvent(
                        event_type="delta",
                        session_id=request.session_id,
                        task_type=request.task_type,
                        step_name=STEP_GENERATE_ANSWER,
                        content=answer_text,
                    )
                )
            else:
                # 构造普通系统提示词
                system_prompt = build_system_prompt("default")
                # 初始化模型上下文
                messages = [{"role": "system", "content": system_prompt}]

                # 如果命中了知识库证据，则把引用上下文作为 system 消息加入
                if matched_chunks:
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "你正在扮演企业知识库问答 Agent。"
                                "请严格基于以下知识库证据回答，并按要求给出引用。\n\n"
                                f"{build_rag_context_from_chunks(matched_chunks)}"
                            ),
                        }
                    )

                # 拼接历史上下文，保持普通对话连续性
                if request.history:
                    # 把历史消息列表里的每个 MessageItem 对象，转成模型 API 能识别的普通字典，然后追加到 messages 里
                    messages.extend([msg.model_dump() for msg in request.history])

                # 根据是否命中证据，构造当前用户输入
                if matched_chunks:
                    user_prompt = (
                        "请基于上方知识库证据回答下面的问题。"
                        "涉及事实、规则、数字或结论时，句末必须附引用。\n\n"
                        f"【用户问题】\n{request.input_text}"
                    )
                else:
                    user_prompt = request.input_text

                # 加入当前用户问题
                messages.append({"role": "user", "content": user_prompt})

                # 流式调用模型并把 delta 绑定到生成回答步骤
                for delta in _stream_model_answer(client=client, messages=messages):
                    answer_text += delta
                    yield to_sse(
                        StreamEvent(
                            event_type="delta",
                            session_id=request.session_id,
                            task_type=request.task_type,
                            step_name=STEP_GENERATE_ANSWER,
                            content=delta,
                        )
                    )

            # 保存生成回答步骤结果
            final_result[STEP_GENERATE_ANSWER] = answer_text.strip()
            # 通知前端：生成回答步骤完成
            yield to_sse(
                StreamEvent(
                    event_type="step_complete",
                    session_id=request.session_id,
                    task_type=request.task_type,
                    step_name=STEP_GENERATE_ANSWER,
                    content=final_result[STEP_GENERATE_ANSWER],
                )
            )

            # 将 Agent 分步骤结果序列化为 JSON
            final_content = json.dumps(final_result, ensure_ascii=False)
            # 保存 assistant 消息；如果本轮有引用片段，则把引用模块元数据一起保存
            save_chat_message(
                session_id=request.session_id,
                role="assistant",
                content=final_content,
                raw_content=final_content,
                mode=request.persona,
                metadata=_build_agent_rag_metadata(request, matched_chunks),
            )

            # 发送最终完成事件
            yield to_sse(
                StreamEvent(
                    event_type="final",
                    session_id=request.session_id,
                    task_type=request.task_type,
                    content=final_content,
                    is_final=True,
                )
            )

        except Exception as e:
            # 将异常包装成 SSE error 事件返回前端
            yield to_sse(
                StreamEvent(
                    event_type="error",
                    session_id=request.session_id,
                    task_type=request.task_type,
                    error_message=str(e),
                    is_final=True,
                )
            )

    # 返回标准 SSE 流式响应
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
    )
