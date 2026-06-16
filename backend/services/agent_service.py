"""
企业知识库问答 Agent 服务模块。

职责：
1. 通过模型路由决策和规则兜底，判断用户问题是否需要查询企业知识库。
2. 需要知识库时执行 RAG 检索，并根据证据是否充分决定是否生成引用答案。
3. 不需要知识库时走普通对话流程，避免所有问题都盲目 RAG。
4. 将 Agent 决策、检索证据、生成回答三个阶段转换为 SSE 步骤事件返回前端。
5. 保存用户消息、Agent 最终结果和当前回答对应的引用元数据。

说明：
- 当前模块是轻量 Agent / Workflow 改造，不引入 LangChain 或复杂多 Agent。
- Agent 判断阶段优先使用模型做路由决策，模型不可用时回退到规则判断。
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

# 明确属于通用写作、改写或文本处理的关键词
GENERAL_TEXT_TASK_KEYWORDS = {
    "写",
    "写一封",
    "生成",
    "模板",
    "改写",
    "润色",
    "翻译",
    "总结",
    "扩写",
    "缩写",
}

# 出现这些词时，通常表示用户需要企业制度、流程或文档依据
KNOWLEDGE_POLICY_ANCHOR_KEYWORDS = {
    "公司",
    "制度",
    "政策",
    "规定",
    "流程",
    "审批",
    "员工手册",
    "手册",
    "依据",
    "根据",
    "规则",
    "标准",
}


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """
    从模型输出中提取 JSON 对象。

    函数说明：
    1. 优先直接按 JSON 解析模型输出。
    2. 如果模型额外包了 Markdown 或说明文字，则截取第一个 {...} 再解析。
    3. 解析失败时返回 None，让外层回退到规则判断。

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
            # 截取后仍然无法解析，交给外层规则兜底
            return None

    # 只有字典结构才符合当前决策协议
    if not isinstance(parsed_result, dict):
        return None

    # 返回解析出的字典
    return parsed_result


def _is_general_text_task_without_policy_anchor(question: str) -> bool:
    """
    判断当前问题是否是无需知识库的通用文本处理任务。

    函数说明：
    1. 识别写邮件、写文案、改写、翻译、总结等通用文本任务。
    2. 如果同时出现公司制度、流程、员工手册等依据词，则不按通用任务处理。
    3. 用于避免“帮我写一封请假邮件”被误判成必须查询企业知识库。

    :param question: 用户当前输入的问题
    :return: 如果是无需知识库的通用文本任务则返回 True，否则返回 False
    """
    # 去掉首尾空白，统一后续关键词判断
    normalized_question = question.strip()
    # 空问题不是有效文本任务
    if not normalized_question:
        return False

    # 判断是否包含写作、改写、翻译、总结等通用任务动作
    has_general_text_action = any(keyword in normalized_question for keyword in GENERAL_TEXT_TASK_KEYWORDS)
    # 判断是否包含公司制度、流程、员工手册等知识库依据信号
    has_policy_anchor = any(keyword in normalized_question for keyword in KNOWLEDGE_POLICY_ANCHOR_KEYWORDS)

    # 有通用文本处理动作，且没有明确制度依据时，按普通对话处理
    return has_general_text_action and not has_policy_anchor


def _decide_need_knowledge_base_by_llm(question: str, client) -> tuple[bool, str] | None:
    """
    使用大模型判断当前问题是否需要查询企业知识库。

    函数说明：
    1. 让模型只做轻量路由决策，不生成最终答案。
    2. 要求模型返回固定 JSON，包含 need_knowledge_base 和 reason。
    3. 如果模型输出不合规或调用失败，则返回 None，让外层规则判断兜底。

    :param question: 用户当前输入的问题
    :param client: OpenAI 兼容客户端
    :return: 二元组，第一项表示是否需要知识库，第二项是判断理由；失败时返回 None
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
                        "你是企业知识库问答系统的路由 Agent，只负责判断用户问题是否需要查询企业知识库。"
                        "如果问题涉及公司制度、员工手册、流程、报销、请假、年假、试用期、远程办公、权限、合同、采购、内部规则或需要基于上传文档回答，则 need_knowledge_base=true。"
                        "如果问题只是让你写请假邮件、写通知、写文案、翻译、改写、总结、开放闲聊、代码问题或不依赖企业内部文档的常识问题，则 need_knowledge_base=false。"
                        "只有当用户明确要求按照公司制度、请假流程、员工手册、内部规定或已有文档依据来回答时，才把请假类问题判断为 need_knowledge_base=true。"
                        "只返回 JSON，不要返回 Markdown，不要补充解释。"
                        "JSON 格式必须是：{\"need_knowledge_base\": true, \"reason\": \"简短中文理由\"}"
                    ),
                },
                {
                    "role": "user",
                    "content": f"用户问题：{question}",
                },
            ],
            temperature=0, # 控制模型的随机性，0为最稳定。尽量不要发挥，严格按规则判断
        )
    except Exception:
        # 模型决策失败不能中断 Agent 主流程，后续交给规则判断
        return None

    try:
        # 读取模型返回文本
        decision_text = response.choices[0].message.content or ""
    except (AttributeError, IndexError):
        # 如果模型响应结构异常，则交给规则判断兜底
        return None
    # 从模型输出中解析 JSON
    decision_json = _extract_json_object(decision_text)
    # 如果解析失败，则交给规则判断兜底
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

    # 返回模型决策结果
    return need_knowledge_base, f"Agent 判断：{reason}"


def _decide_need_knowledge_base_by_rule(question: str, use_rag: bool) -> tuple[bool, str]:
    """
    使用规则兜底判断当前问题是否需要查询企业知识库。

    函数说明：
    1. 当模型决策不可用或输出异常时，保证 Agent 仍能稳定工作。
    2. 对空问题、寒暄问题和明显企业制度问题进行确定性判断。
    3. 对没有明显知识库信号的问题默认走普通对话，避免所有问题都盲目 RAG。

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

    # 通用写作任务如果没有明确要求公司制度或文档依据，则不查询知识库
    if _is_general_text_task_without_policy_anchor(normalized_question):
        return False, "当前问题属于通用文本生成任务，不需要查询企业知识库。"

    # 只要命中企业知识库相关关键词，就认为需要检索文档证据
    if any(keyword in normalized_question for keyword in KNOWLEDGE_REQUIRED_KEYWORDS):
        return True, "当前问题涉及企业制度、流程、文档或规则，需要查询知识库。"

    # 没有明确知识库信号时，不强行检索
    return False, "当前问题没有明显企业知识库信号，按普通对话处理。"


def decide_need_knowledge_base(question: str, use_rag: bool, client=None) -> tuple[bool, str]:
    """
    判断当前问题是否需要查询企业知识库。

    函数说明：
    1. 如果前端没有开启 RAG，直接判定为不需要知识库。
    2. 如果问题是短寒暄或普通身份询问，跳过知识库检索。
    3. 对其余问题优先使用大模型做路由决策，让 Agent 自己判断是否需要知识库。
    4. 如果模型决策失败，则回退到规则判断，保证流程稳定。

    :param question: 用户当前输入的问题
    :param use_rag: 前端是否开启 RAG
    :param client: OpenAI 兼容客户端，用于执行轻量 Agent 路由判断
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

    # 通用写作任务不需要先查知识库，避免“写请假邮件”被误判成公司制度问答
    if _is_general_text_task_without_policy_anchor(normalized_question):
        return False, "当前问题属于通用文本生成任务，不需要查询企业知识库。"

    # 优先让模型做一次轻量路由判断，增强 Agent 自主决策能力
    llm_decision = _decide_need_knowledge_base_by_llm(
        question=normalized_question,
        client=client,
    )
    # 模型决策成功时直接使用模型结论
    if llm_decision is not None:
        return llm_decision

    # 模型决策失败时回退到规则判断，保证 Agent 主流程不被模型路由失败打断
    return _decide_need_knowledge_base_by_rule(
        question=normalized_question,
        use_rag=use_rag,
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
        "可以基于企业文档回答问题，也可以处理请假邮件、改写、总结等轻量文本任务。"
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
                client=client,
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
                mode=request.persona,
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
