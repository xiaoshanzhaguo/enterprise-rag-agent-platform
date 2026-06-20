"""
企业知识库问答 Agent 服务模块。

职责：
1. 通过模型意图分类和 query rewrite，判断用户问题是否需要查询企业知识库。
2. 需要知识库时使用改写后的检索 query 执行 RAG 检索，并根据证据是否充分决定是否生成引用答案。
3. 不需要知识库时走普通对话流程，避免所有问题都盲目 RAG。
4. 将 Agent 决策、检索证据、生成回答三个阶段转换为 SSE 步骤事件返回前端。
5. 保存用户消息、Agent 最终结果和当前回答对应的引用元数据。

说明：
- 当前模块是轻量 Agent / Workflow 改造，不引入 LangChain 或复杂多 Agent。
- Agent 判断阶段使用模型做意图分类和检索 query 改写，不再维护关键词判断规则。
- Agent 流程固定为：判断是否需要知识库 -> 检索证据 -> 生成回答。
- 如果知识库证据不足，Agent 不会强行回答，而是明确说明缺少依据。
- 如果问题不依赖企业知识库，Agent 会跳过检索，直接按普通对话回答。
"""

# 未来版本兼容特性, 让类型注解延迟解析
from __future__ import annotations

# 导入 JSON 工具，用于解析 Agent 决策结果和保存分步骤 Agent 结果
import json
# 导入类型标注工具，让函数输入输出更清晰
from typing import Any

# FastAPI 流式响应对象，用于返回 SSE 事件流
from fastapi.responses import StreamingResponse

# 项目配置对象，用于读取模型名称和引用预览长度
from backend.config import settings
# 数据库持久化函数，用于保存聊天会话、读取标题、保存消息和 RAG 查询日志
from backend.db.repository import ensure_chat_session, get_chat_session_title, save_chat_message, save_rag_query_with_hits
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
# 请求模型和流式事件模型
from backend.schema.chat_schema import ChatRequest, StreamEvent
# 生成侧边栏历史会话标题
from backend.services.session_title import generate_session_title
# SSE 格式化工具
from backend.utils.stream_helper import to_sse


# Agent 第一步：判断是否需要知识库
STEP_JUDGE_KNOWLEDGE = "judge_knowledge"
# Agent 第二步：检索证据
STEP_RETRIEVE_EVIDENCE = "retrieve_evidence"
# Agent 第三步：生成回答
STEP_GENERATE_ANSWER = "generate_answer"

# Agent 前端展示时使用的步骤标题，用于识别并过滤历史里的分步骤结果
AGENT_STEP_TITLES = (
    "判断是否需要知识库",
    "检索证据",
    "生成回答",
)

# Agent 分步骤 JSON 里的字段名，用于识别并过滤历史里的结构化结果
AGENT_STEP_KEYS = (
    STEP_JUDGE_KNOWLEDGE,
    STEP_RETRIEVE_EVIDENCE,
    STEP_GENERATE_ANSWER,
)

def _extract_json_object(text: str) -> dict[str, Any] | None:
    """
    从模型输出中提取 JSON 对象。

    函数说明：
    1. 优先直接按 JSON 解析模型输出。
    2. 如果模型额外包了 Markdown 或说明文字，则截取第一个 {...} 再解析。
    3. 解析失败时返回 None，让外层使用保守兜底。

    :param text: 模型返回的原始文本
    :return: 解析得到的 JSON 字典；解析失败时返回 None
    """
    # 去掉首尾空白，避免换行影响 JSON 解析
    normalized_text = text.strip()
    # 空输出无法解析
    if not normalized_text:
        return None

    try:
        # 优先尝试直接解析完整输出
        parsed_result = json.loads(normalized_text)
    except json.JSONDecodeError:
        # 如果模型输出里夹杂说明文字，则尝试截取第一个 JSON 对象范围
        start_index = normalized_text.find("{")
        end_index = normalized_text.rfind("}")
        # 没有找到完整 JSON 对象时返回 None
        if start_index < 0 or end_index <= start_index:
            return None

        try:
            # 解析截取出来的 JSON 对象
            parsed_result = json.loads(normalized_text[start_index:end_index + 1])
        except json.JSONDecodeError:
            # 截取后仍然无法解析，交给外层保守兜底
            return None

    # 只有字典结构才符合当前决策协议
    if not isinstance(parsed_result, dict):
        return None

    # 返回解析出的字典
    return parsed_result


def _normalize_rewritten_query(question: str, rewritten_query: Any) -> str:
    """
    归一化模型改写后的检索 query。

    函数说明：
    1. 去掉模型可能额外输出的空白、引号和换行。
    2. 如果模型没有给出可用 query，则回退到用户原始问题。
    3. 限制 query 长度，避免把大段文本直接送入检索链路。

    :param question: 用户原始问题
    :param rewritten_query: 模型输出的改写 query
    :return: 可用于 RAG 检索的 query
    """
    # 用户原始问题作为最终兜底，保证检索链路始终有 query 可用
    fallback_query = question.strip()
    # 非字符串结构无法作为检索 query，直接回退
    if not isinstance(rewritten_query, str):
        return fallback_query

    # 清理首尾空白、换行和常见包裹引号
    normalized_query = rewritten_query.strip().strip('"').strip("'").strip()
    # 空字符串没有检索价值，回退到原始问题
    if not normalized_query:
        return fallback_query

    # 过长 query 会稀释检索重点，因此只保留前 120 个字符
    return normalized_query[:120]


def _format_retrieval_mode_for_display(retrieval_mode: str) -> str:
    """
    格式化前端展示用的检索方式。

    函数说明：
    1. 保留数据库中的原始 retrieval_mode，不影响 rag_queries 统计。
    2. 当项目配置为 vector，但最终命中来自 keyword 时，明确说明这是向量无可靠命中后的关键词兜底。
    3. 避免用户看到 keyword 时误以为系统没有启用向量检索。

    :param retrieval_mode: RAG 服务层返回的实际命中方式
    :return: 更适合前端展示的检索方式文案
    """
    # 当前配置为向量模式，但结果来自关键词，说明发生了 fallback
    if settings.rag_retrieval_mode == "vector" and retrieval_mode == "keyword":
        return "keyword（vector 未达到阈值后回退）"

    # 其他情况直接展示原始检索方式
    return retrieval_mode


def _decide_need_knowledge_base_by_llm(question: str, client) -> tuple[bool, str, str] | None:
    """
    使用大模型进行意图分类和检索 query 改写。

    函数说明：
    1. 让模型只做轻量路由决策和检索 query 改写，不生成最终答案。
    2. 要求模型返回固定 JSON，包含 need_knowledge_base、reason 和 rewritten_query。
    3. 如果模型输出不合规或调用失败，则返回 None，让外层保守兜底。

    :param question: 用户当前输入的问题
    :param client: OpenAI 兼容客户端
    :return: 三元组，依次表示是否需要知识库、判断理由、检索 query；失败时返回 None
    """
    # 如果没有可用客户端，则无法进行模型决策
    if client is None:
        return None

    try:
        # 调用模型做一次非流式轻量决策，避免前端还没判断就先检索
        response = client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是企业知识库问答系统的意图分类与检索改写 Agent，只负责判断用户问题是否需要查询企业知识库，并在需要时改写检索 query。"
                        "当问题明确依赖已有知识库、上传资料、企业内部文档、制度规则、流程标准、可追溯来源或特定上下文证据时，need_knowledge_base=true。"
                        "当问题是普通对话、开放写作、通用文本处理、常识问答、代码问题，且没有要求基于特定资料或内部规则回答时，need_knowledge_base=false。"
                        "如果问题同时包含通用任务和资料依据要求，请优先判断是否需要外部证据；只有需要外部证据时才查询知识库。"
                        "当 need_knowledge_base=true 时，rewritten_query 必须是适合向量检索的短查询，保留核心实体、规则类型、资料范围和约束词，去掉寒暄、附件说明和无关口语。"
                        "当 need_knowledge_base=false 时，rewritten_query 必须为空字符串。"
                        "只返回 JSON，不要返回 Markdown，不要补充解释。"
                        "JSON 格式必须是：{\"need_knowledge_base\": true, \"reason\": \"简短中文理由\", \"rewritten_query\": \"适合检索的中文短查询\"}"
                    ),
                },
                {
                    "role": "user",
                    "content": f"用户问题：{question}",
                },
            ],
            temperature=0, # 控制模型的随机性，0 为最稳定，尽量让意图判断结果可复现
        )
    except Exception:
        # 模型决策失败不能中断 Agent 主流程，后续交给保守兜底
        return None

    try:
        # 读取模型返回文本
        decision_text = response.choices[0].message.content or ""
    except (AttributeError, IndexError):
        # 如果模型响应结构异常，则交给保守兜底
        return None
    # 从模型输出中解析 JSON
    decision_json = _extract_json_object(decision_text)
    # 如果解析失败，则交给保守兜底
    if not decision_json:
        return None

    # 读取是否需要知识库字段
    need_knowledge_base = decision_json.get("need_knowledge_base")
    # 字段必须是布尔值，避免字符串 true/false 导致误判
    if not isinstance(need_knowledge_base, bool):
        return None

    # 读取模型给出的简短理由
    reason = str(decision_json.get("reason") or "").strip()
    # 如果模型没有给理由，则补充默认理由
    if not reason:
        reason = "模型判断当前问题需要按路由结果处理。"

    # 需要知识库时使用模型改写后的 query；不需要知识库时不保留检索 query
    rewritten_query = (
        _normalize_rewritten_query(question, decision_json.get("rewritten_query"))
        if need_knowledge_base
        else ""
    )

    # 返回模型决策结果和检索 query
    return need_knowledge_base, f"Agent 判断：{reason}", rewritten_query


def _has_indexed_rag_document(session_id: str | None) -> bool:
    """
    判断当前会话是否已经有可检索的知识库文档。

    函数说明：
    1. 通过数据库文档状态判断，而不是依赖前端开关或内存状态。
    2. 只有已有文档和 chunk 时，Agent 判断失败才适合按原问题兜底检索。
    3. 状态查询失败时返回 False，避免数据库异常被误判成可检索。

    :param session_id: 当前会话 ID
    :return: True 表示当前会话已有可检索文档
    """
    # 没有 session_id 时，后端无法定位当前会话，也就无法查询这个会话是否上传过文档。
    if not session_id:
        return False

    try:
        # 从数据库读取当前会话的文档状态。
        # 这里不用前端传来的状态，是为了防止页面刷新、后端重启或前端状态丢失后判断不准。
        document_status = get_document_status(session_id)
    except Exception:
        # 文档状态查询失败时，不冒险进入 RAG 检索。
        # 这样可以避免数据库异常时继续执行后续检索，导致更难理解的错误。
        return False

    # has_document 表示这个 session 有文档记录。
    # chunk_count > 0 表示文档已经切块完成，确实可以被检索。
    # 两个条件都满足，才认为“当前会话已有可检索知识库”。
    return bool(
        document_status.get("has_document")
        and int(document_status.get("chunk_count") or 0) > 0
    )


def _build_intent_decision_fallback(
    question: str,
    use_rag: bool,
    session_id: str | None = None,
) -> tuple[bool, str, str]:
    """
    构造意图分类失败时的保守兜底结果。

    函数说明：
    1. 只处理 RAG 关闭、空问题和模型判断失败这类系统状态。
    2. 不再通过关键词判断用户意图，避免规则系统覆盖 Agent 判断。
    3. 如果 RAG 已开启且当前会话已有文档，则使用原问题执行检索，避免演示时因意图分类失败跳过知识库。
    4. 没有可检索文档时，仍然保守跳过知识库检索，避免盲目 RAG。

    :param question: 用户当前输入的问题
    :param use_rag: 前端是否开启 RAG
    :param session_id: 当前会话 ID，用于判断是否已有可检索文档
    :return: 三元组，依次表示是否需要知识库、判断理由、检索 query
    """
    # 去掉首尾空白，只用于判断是否为空问题
    normalized_question = question.strip()

    # 如果前端没有开启 RAG，则尊重用户设置，走普通对话。
    # 即使当前 session 有文档，也不应该绕过用户开关强行检索。
    if not use_rag:
        return False, "当前未开启 RAG，按普通对话处理。", ""

    # 空问题没有检索价值，直接跳过知识库检索。
    # 这里先拦截空输入，避免后面把空字符串写入 rag_queries 或传给检索器。
    if not normalized_question:
        return False, "当前问题为空，跳过知识库检索。", ""

    # Agent 判断失败但已有知识库文档时，用原问题兜底检索，保证演示链路仍然走 RAG。
    if _has_indexed_rag_document(session_id):
        # 这里返回 True，表示后续流程仍然进入“检索证据”步骤。
        # 第三个返回值使用原始问题，等价于“没有 query rewrite 时，直接用用户问题检索”。
        return (
            True,
            "Agent 意图分类暂不可用，已按原始问题检索当前会话知识库。",
            normalized_question,
        )

    # 没有模型判断结果且没有可检索文档时，不再用关键词猜测用户意图。
    # 这样可以避免用户只是闲聊或写作时，被后端硬塞进一个没有文档的 RAG 流程。
    return False, "Agent 意图分类暂不可用，且当前会话没有可检索文档，本轮按普通对话处理。", ""


def decide_need_knowledge_base(
    question: str,
    use_rag: bool,
    client=None,
    session_id: str | None = None,
) -> tuple[bool, str, str]:
    """
    判断当前问题是否需要查询企业知识库，并生成检索 query。

    函数说明：
    1. 如果前端没有开启 RAG，直接判定为不需要知识库。
    2. 优先使用大模型做意图分类和 query rewrite。
    3. 如果模型决策失败，则使用保守兜底，保证流程稳定。

    :param question: 用户当前输入的问题
    :param use_rag: 前端是否开启 RAG
    :param client: OpenAI 兼容客户端，用于执行轻量 Agent 意图分类和 query rewrite
    :param session_id: 当前会话 ID，用于模型判断失败时检查是否已有可检索文档
    :return: 三元组，依次表示是否需要知识库、判断理由、检索 query
    """
    # 去掉首尾空白，只用于基础空值判断和传给模型
    normalized_question = question.strip()

    # 如果前端没有开启 RAG，则尊重用户设置，走普通对话。
    # 这个判断放在最前面，可以避免无意义调用 LLM 做意图分类。
    if not use_rag:
        return False, "当前未开启 RAG，按普通对话处理。", ""

    # 空问题没有检索价值，直接走普通对话兜底。
    # 这样后面的 LLM 判断、query rewrite 和 RAG 检索都不会处理空字符串。
    if not normalized_question:
        return False, "当前问题为空，跳过知识库检索。", ""

    # 优先让模型做一次轻量意图分类和 query rewrite，增强 Agent 自主决策能力
    llm_decision = _decide_need_knowledge_base_by_llm(
        question=normalized_question,
        client=client,
    )
    # 模型决策成功时直接使用模型结论
    if llm_decision is not None:
        return llm_decision

    # 模型决策失败时进入兜底逻辑。
    # 兜底函数会根据 session_id 再查一次数据库：有文档就用原问题检索，没有文档就按普通对话处理。
    return _build_intent_decision_fallback(
        question=normalized_question,
        use_rag=use_rag,
        session_id=session_id,
    )


def _build_agent_answer_system_prompt(has_rag_evidence: bool) -> str:
    """
    构造 Agent 最终回答阶段的系统提示词。

    函数说明：
    1. 为企业知识库问答模式提供独立身份定位，避免沿用旧的内容创作助手身份。
    2. 明确约束最终回答只输出正文，不输出 JSON 或 Agent 步骤标题。
    3. 命中知识库证据时，要求模型严格基于证据并附引用。

    :param has_rag_evidence: 当前回答是否已经命中可靠知识库证据
    :return: 可直接传给大模型的 system prompt
    """
    # 所有 Agent 回答都必须遵守的基础身份与格式约束
    base_prompt = (
        "你是企业知识库问答 Agent，负责判断问题是否需要企业知识库，并给出清晰、可靠的最终回答。"
        "当前阶段只需要输出最终回答正文，不要输出 JSON，不要输出 Markdown 代码块，"
        "不要输出 judge_knowledge、retrieve_evidence、generate_answer 等字段，"
        "不要重复“判断是否需要知识库”“检索证据”“生成回答”等流程标题。"
        "历史消息只作为上下文参考，最终回答必须只回应当前用户的最新问题，不要补答历史问题。"
    )

    # 命中知识库证据时，强调引用和事实边界
    if has_rag_evidence:
        return (
            f"{base_prompt}"
            "你必须严格基于提供的知识库证据回答。"
            "涉及事实、规则、数字、制度或结论时，句末必须附引用来源。"
            "如果证据不足以支持某个结论，请明确说明知识库中没有找到依据。"
        )

    # 不需要知识库时，允许普通写作、寒暄和轻量文本处理，但仍保持企业知识库 Agent 身份
    return (
        f"{base_prompt}"
        "当前问题已判断为不需要查询知识库，请按普通对话直接回答。"
        "如果用户询问你是谁，请说明你是企业知识库问答 Agent，"
        "可以基于企业文档回答问题，也可以处理轻量文本生成、改写、总结等任务。"
    )


def _is_agent_step_output(content: str) -> bool:
    """
    判断一段历史内容是否是 Agent 分步骤展示结果。

    函数说明：
    1. 识别保存到历史里的 Agent JSON 结果。
    2. 识别前端渲染后的 Agent 步骤标题文本。
    3. 帮助最终回答阶段过滤历史污染，避免模型重复输出流程块或 JSON。

    :param content: 历史消息正文
    :return: 如果是 Agent 分步骤结果则返回 True，否则返回 False
    """
    # 去掉首尾空白，避免换行影响判断
    normalized_content = content.strip()
    # 空内容不是有效的 Agent 步骤输出
    if not normalized_content:
        return False

    # 统计命中的步骤标题数量，避免普通文本偶然出现“生成回答”也被误判
    matched_step_title_count = sum(
        1
        for step_title in AGENT_STEP_TITLES
        if step_title in normalized_content
    )
    # 同时出现多个步骤标题时，基本可以确认这是给前端展示的 Agent 流程结果
    if matched_step_title_count >= 2:
        return True

    # 尝试把内容解析成 JSON 对象，识别数据库里保存的结构化 Agent 结果
    parsed_content = _extract_json_object(normalized_content)
    # 只有字典结构才可能是 Agent 分步骤 JSON
    if not isinstance(parsed_content, dict):
        return False

    # 只要包含任一 Agent 步骤字段，就认为它不适合继续喂给模型
    return any(step_key in parsed_content for step_key in AGENT_STEP_KEYS)


def _build_agent_model_history(history: list[Any], max_items: int = 6) -> list[dict[str, str]]:
    """
    构造适合传给 Agent 最终回答模型的干净历史上下文。

    函数说明：
    1. 优先按完整问答轮次保留历史，避免留下已经被 Agent 步骤回答过的孤立 user 消息。
    2. 过滤掉 Agent 分步骤结果，避免模型模仿 JSON 或重复流程标题。
    3. 保留普通 user / assistant / system 消息，让常规对话仍具备基本连续性。

    :param history: ChatRequest.history 中的历史消息列表
    :param max_items: 最多保留多少条历史消息
    :return: 大模型 API 可直接接收的历史消息列表
    """
    # 创建空列表，用于保存过滤后的历史消息
    clean_history: list[dict[str, str]] = []
    # 暂存尚未确认能组成完整问答轮次的 user 消息
    pending_user_messages: list[dict[str, str]] = []

    # 多取一些历史，后续再裁剪，避免切片正好截断一组问答
    recent_history = history[-max_items * 2:]

    # 处理最近若干条历史，降低无关旧内容对当前回答的影响
    for message in recent_history:
        # 读取消息角色，兼容 Pydantic 对象和普通字典两种结构
        role = getattr(message, "role", None) if not isinstance(message, dict) else message.get("role")
        # 读取消息正文，兼容 Pydantic 对象和普通字典两种结构
        content = getattr(message, "content", "") if not isinstance(message, dict) else message.get("content", "")

        # 只保留模型 API 支持的角色
        if role not in {"user", "assistant", "system"}:
            continue

        # 确保正文是字符串，避免异常结构进入模型上下文
        if not isinstance(content, str):
            continue

        # 去掉首尾空白，避免无意义换行进入上下文
        normalized_content = content.strip()
        # 空消息没有上下文价值
        if not normalized_content:
            continue

        # 组装当前历史消息
        current_message = {
            "role": role,
            "content": normalized_content,
        }

        # user 消息先暂存，等看到后续 assistant 是否可保留再决定是否进入上下文
        if role == "user":
            pending_user_messages.append(current_message)
            continue

        # assistant 历史如果是 Agent 步骤结果，则连同前面的待配对 user 一起丢弃
        if role == "assistant" and _is_agent_step_output(normalized_content):
            pending_user_messages = []
            continue

        # 普通 assistant 表示前面的 user 已经有可用回答，可以一起保留
        if role == "assistant":
            clean_history.extend(pending_user_messages)
            pending_user_messages = []
            clean_history.append(current_message)
            continue

        # system 消息不参与问答配对，可直接保留
        clean_history.append(current_message)

    # 如果末尾还有未配对 user，说明它可能是最近一条未完成上下文，可以保留
    clean_history.extend(pending_user_messages)

    # 返回裁剪后的历史列表
    return clean_history[-max_items:]


def _normalize_agent_answer_text(answer_text: str) -> str:
    """
    归一化 Agent 最终回答正文。

    函数说明：
    1. 正常情况下直接返回模型生成的正文。
    2. 如果模型误返回包含 generate_answer 的 JSON，则只提取最终回答正文。
    3. 如果模型误返回完整 Agent 步骤文本，则尽量提取“生成回答”之后的内容。

    :param answer_text: 模型生成阶段累计得到的原始文本
    :return: 清理后的最终回答正文
    """
    # 去掉首尾空白，避免最终展示多余空行
    normalized_answer = answer_text.strip()
    # 空答案直接返回空字符串
    if not normalized_answer:
        return ""

    # 尝试解析 JSON，兼容模型误把分步骤结果整体返回的情况
    parsed_answer = _extract_json_object(normalized_answer)
    # 如果能解析出字典，并且里面有 generate_answer，则只保留最终回答正文
    if isinstance(parsed_answer, dict):
        # 读取 JSON 里的最终回答字段
        generate_answer = parsed_answer.get(STEP_GENERATE_ANSWER)
        # 只有字符串答案才可以作为最终正文
        if isinstance(generate_answer, str) and generate_answer.strip():
            return generate_answer.strip()

    # 如果模型误输出了完整步骤文本，则尝试截取“生成回答”之后的内容
    for answer_title in ("✍️ 生成回答", "生成回答"):
        # 只在包含步骤标题时才进行截取
        if answer_title in normalized_answer:
            # 使用最后一次出现的位置，避免正文里偶然提到同名词导致截取过早
            extracted_answer = normalized_answer.rsplit(answer_title, 1)[-1].strip()
            # 去掉标题后可能残留的冒号
            extracted_answer = extracted_answer.lstrip(":：").strip()
            # 截取后存在正文时返回
            if extracted_answer:
                return extracted_answer

    # 默认返回原始正文
    return normalized_answer


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


def _retrieve_agent_rag_chunks(
    session_id: str | None,
    rewritten_query: str,
    original_question: str,
    top_k: int,
) -> tuple[list[dict[str, Any]], str, str, str]:
    """
    执行 Agent RAG 检索，并在改写 query 未命中时回退原始问题。

    函数说明：
    1. 优先使用模型改写后的 query 检索，提高语义检索命中质量。
    2. 如果改写 query 没有可靠命中，则使用用户原始问题再检索一次。
    3. 返回最终命中的片段、检索方式、实际生效 query 和检索说明。

    :param session_id: 当前会话 ID
    :param rewritten_query: Agent 改写后的检索问题
    :param original_question: 用户原始问题
    :param top_k: 最多返回的检索片段数量
    :return: 四元组，依次为命中片段、检索方式、实际检索 query、补充说明
    """
    # 清理改写 query，避免空白影响检索
    normalized_rewritten_query = rewritten_query.strip()
    # 清理原始问题，作为改写失败后的兜底检索输入
    normalized_original_question = original_question.strip()
    # 优先使用改写 query；如果模型没有给出 query，则直接使用原始问题
    primary_query = normalized_rewritten_query or normalized_original_question

    # 如果两个 query 都为空，则直接返回无命中
    if not primary_query:
        return [], NO_HIT_RETRIEVAL_MODE, "", ""

    # 第一次检索使用模型改写后的 query
    matched_chunks, retrieval_mode = retrieve_rag_chunks_with_mode(
        session_id=session_id,
        query=primary_query,
        top_k=top_k,
    )
    # 如果改写 query 已经命中，直接返回结果
    if matched_chunks:
        return matched_chunks, retrieval_mode, primary_query, ""

    # 如果原始问题为空，或者和主检索 query 完全一致，就没有必要重复检索
    if not normalized_original_question or normalized_original_question == primary_query:
        return matched_chunks, retrieval_mode, primary_query, ""

    # 改写 query 没命中时，用用户原始问题再检索一次，避免 query rewrite 丢失原句关键词
    fallback_chunks, fallback_mode = retrieve_rag_chunks_with_mode(
        session_id=session_id,
        query=normalized_original_question,
        top_k=top_k,
    )
    # 如果原始问题命中，则返回 fallback 结果，并给前端展示一条说明
    if fallback_chunks:
        return (
            fallback_chunks,
            fallback_mode,
            normalized_original_question,
            "改写后的检索问题未命中，已使用原始问题重新检索。",
        )

    # 两次检索都没有命中时，fallback_chunks 此时为空列表；
    # 返回空列表给调用方，表示没有可用证据；
    # 同时保留 primary_query 作为检索日志中的主 query。
    return (
        fallback_chunks,
        fallback_mode,
        primary_query,
        "已尝试改写后的检索问题和原始问题，均未命中可靠依据。",
    )


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
            # 已有标题的会话不重复生成；新会话才调用模型生成侧边栏主题
            session_title = get_chat_session_title(request.session_id) or generate_session_title(
                user_text=display_text,
                mode=request.mode,
                client=client
            )
            # 确保当前会话存在，并用用户问题作为会话标题
            ensure_chat_session(
                session_id=request.session_id,
                mode=request.mode,
                title=session_title,
            )
            # 保存用户消息
            save_chat_message(
                session_id=request.session_id,
                role="user",
                content=display_text,
                raw_content=request.input_text,
                mode=request.mode,
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

            # 执行意图分类和 query rewrite
            need_knowledge_base, decision_reason, retrieval_query = decide_need_knowledge_base(
                question=request.input_text,
                use_rag=request.use_rag,
                client=client,
                # session_id 用于兜底场景：当 LLM 判断失败时，后端会检查当前会话是否已有文档。
                session_id=request.session_id,
            )
            # 组装判断步骤展示文本
            judge_text = (
                f"判断结果：{'需要查询知识库' if need_knowledge_base else '不需要查询知识库'}。\n\n"
                f"判断依据：{decision_reason}"
            )
            # 如果需要知识库，则展示本轮实际用于检索的 query，兼容 query rewrite 和判断失败兜底两种来源
            if need_knowledge_base:
                judge_text += f"\n\n本轮检索问题：{retrieval_query}"
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
            # 默认实际检索 query 为 Agent 改写后的 query；如果 fallback 命中，后续会替换成原始问题
            effective_retrieval_query = retrieval_query
            # 默认没有额外检索说明；只有发生 query fallback 时才展示
            retrieval_note = ""
            # 如果判断需要知识库，则执行 RAG 检索
            if need_knowledge_base:
                # 调用 Agent 检索链路，优先使用改写 query，改写无命中时回退原始问题
                matched_chunks, retrieval_mode, effective_retrieval_query, retrieval_note = _retrieve_agent_rag_chunks(
                    session_id=request.session_id,
                    rewritten_query=retrieval_query,
                    original_question=request.input_text,
                    top_k=request.rag_top_k,
                )
                # 保存本次检索记录，便于第 10 天可解释面板和数据库追踪继续生效
                save_rag_query_with_hits(
                    session_id=request.session_id,
                    query_text=effective_retrieval_query,
                    top_k=request.rag_top_k,
                    matched_chunks=matched_chunks,
                    retrieval_mode=retrieval_mode,
                    mode=request.mode,
                )
                # 格式化展示文案，避免 fallback 场景只显示 keyword 造成误解
                retrieval_mode_display = _format_retrieval_mode_for_display(retrieval_mode)
                # 命中证据时展示命中数量、检索方式和最高排名来源
                if matched_chunks:
                    first_source = build_source_label(matched_chunks[0])
                    retrieve_text = (
                        f"已命中 {len(matched_chunks)} 个知识库片段。\n\n"
                        f"检索问题：{effective_retrieval_query}\n\n"
                        f"检索方式：{retrieval_mode_display}\n\n"
                        f"优先证据：{first_source}"
                    )
                else:
                    retrieve_text = (
                        f"{NO_RAG_EVIDENCE_MESSAGE}。\n\n"
                        f"检索问题：{effective_retrieval_query}\n\n"
                        f"检索方式：{retrieval_mode_display}"
                    )
                # 如果发生了 query fallback，则把说明追加到检索步骤中，方便用户理解为什么检索问题变化
                if retrieval_note:
                    retrieve_text += f"\n\n说明：{retrieval_note}"
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
                # 构造 Agent 最终回答专用系统提示词，避免沿用旧内容助手身份
                system_prompt = _build_agent_answer_system_prompt(has_rag_evidence=bool(matched_chunks))
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
                    # 过滤掉历史里的 Agent 分步骤结果，避免模型重复输出 JSON 或流程标题
                    messages.extend(_build_agent_model_history(request.history))

                # 根据是否命中证据，构造当前用户输入
                if matched_chunks:
                    user_prompt = (
                        "请基于上方知识库证据回答下面的问题。"
                        "涉及事实、规则、数字或结论时，句末必须附引用。\n\n"
                        f"【实际检索问题】\n{effective_retrieval_query}\n\n"
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

            # 清理模型可能误返回的 JSON 或完整步骤文本，只保留最终回答正文
            answer_text = _normalize_agent_answer_text(answer_text)
            # 保存生成回答步骤结果
            final_result[STEP_GENERATE_ANSWER] = answer_text
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
            # 构造当前回答对应的引用模块元数据，既用于数据库保存，也用于本次 SSE final 事件返回前端
            assistant_metadata = _build_agent_rag_metadata(request, matched_chunks)
            # 保存 assistant 消息；如果本轮有引用片段，则把引用模块元数据一起保存
            save_chat_message(
                session_id=request.session_id,
                role="assistant",
                content=final_content,
                raw_content=final_content,
                mode=request.mode,
                metadata=assistant_metadata,
            )

            # 发送最终完成事件
            yield to_sse(
                StreamEvent(
                    event_type="final",
                    session_id=request.session_id,
                    task_type=request.task_type,
                    content=final_content,
                    metadata=assistant_metadata or {},
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
