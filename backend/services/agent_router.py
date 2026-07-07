"""
轻量 Agent Router 模块。

职责：
1. 根据用户问题判断应该进入哪类业务路由。
2. 先用规则实现最小可用版本。
3. 后续可以替换为 LLM 分类、LangChain Router 或 LangGraph 节点。
"""

# 导入 JSON 工具，用于解析 Agent 决策结果和保存分步骤 Agent 结果
import json
from typing import Any
# dataclass作用：帮你快速定义一个只用来存数据的类。简单理解：专门用来装结构化数据的小盒子。
from dataclasses import dataclass
# 导入Enum枚举类。作用：限制某个字段只能取几个固定值。
from enum import Enum
# 项目配置对象，用于读取模型名称和引用预览长度
from backend.config import settings
# 数据库文档状态查询函数，用于保存历史消息中的引用模块状态
from backend.rag.store import get_document_status

class AgentRouteType(str, Enum):
    """
    Agent 路由类型。

    补充：
    - 继承了str、Enum，其含义：这是一个枚举；并且每个枚举值本质上也是字符串。
    - 为什么要继承 str？因为这样它更容易和 JSON、数据库、日志兼容。
    """
    KNOWLEDGE_QA = "knowledge_qa"       # 知识问答
    PROCESS_APPLY = "process_apply"     # 流程申请
    IT_SUPPORT = "it_support"           # IT支持
    FINANCE_PROCESS = "finance_process" # 财务流程
    PRODUCT_DOC = "product_doc"         # 产品资料
    IRRELEVANT = "irrelevant"           # 无关问题


@dataclass
class AgentRouteDecision:
    """
    Agent Router 的判断结果。

    补充：
    - @dataclass。这个装饰器表示：下面这个类是一个数据类。它会自动帮你生成：__init__、__repr__、__eq__，无需手写构造函数。
    """
    route_type: AgentRouteType # 路由类型
    reason: str                # 判断理由
    should_use_rag: bool       # 是否应该使用 RAG 检索
    rewritten_query: str = ""  # 改写后的检索query


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
                        "Agent路由类型包括(KNOWLEDGE_QA：知识问答)、(PROCESS_APPLY：流程申请)、(IT_SUPPORT：IT支持)、(FINANCE_PROCESS：财务流程)、(PRODUCT_DOC: 产品资料)、(IRRELEVANT: 无关问题)。"
                        "请你判断问题所属的Agent路由类型，并将英文大写名称写入输出的JSON中。"
                        "当问题明确依赖已有知识库、上传资料、企业内部文档、制度规则、流程标准、可追溯来源或特定上下文证据时，need_knowledge_base=true。"
                        "当问题是普通对话、开放写作、通用文本处理、常识问答、代码问题，且没有要求基于特定资料或内部规则回答时，need_knowledge_base=false。"
                        "如果问题同时包含通用任务和资料依据要求，请优先判断是否需要外部证据；只有需要外部证据时才查询知识库。"
                        "reason应为判断为哪种路由类型的理由，不仅仅是判断是否需要开启知识库，原因尽量简短，同时需保留完整语义。"
                        "当 need_knowledge_base=true 时，rewritten_query 必须是适合向量检索的短查询，保留核心实体、规则类型、资料范围和约束词，去掉寒暄、附件说明和无关口语。"
                        "当 need_knowledge_base=false 时，rewritten_query 必须为空字符串。"
                        "只返回 JSON，不要返回 Markdown，不要补充解释。"
                        "JSON 格式必须是：{\"route_type\": \"Agent路由类型\", \"need_knowledge_base\": true, \"reason\": \"简短中文理由\", \"rewritten_query\": \"适合检索的中文短查询\"}"
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

    # 读取route_type
    route_type = str(decision_json.get("route_type") or "").strip()
    if not route_type:
        route_type = AgentRouteType.KNOWLEDGE_QA

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
    return AgentRouteDecision(
        route_type=parse_route_type(route_type),
        reason=f"Agent 判断: {reason}",
        should_use_rag=need_knowledge_base,
        rewritten_query=rewritten_query,
    )


def _has_indexed_rag_document(session_id: str | None, knowledge_base_id: str | None = None) -> bool:
    """
    判断当前会话是否已经有可检索的知识库文档。

    函数说明：
    1. 通过数据库文档状态判断，而不是依赖前端开关或内存状态。
    2. 只有已有文档和 chunk 时，Agent 判断失败才适合按原问题兜底检索。
    3. 状态查询失败时返回 False，避免数据库异常被误判成可检索。

    :param session_id: 当前会话 ID
    :param knowledge_base_id: 企业知识库 ID；传入后优先检查公共知识库是否有文档
    :return: True 表示当前会话已有可检索文档
    """
    # 没有 session_id 时，后端无法定位当前会话，也就无法查询这个会话是否上传过文档。
    if not session_id:
        return False

    try:
        # 从数据库读取当前会话的文档状态。
        # 这里不用前端传来的状态，是为了防止页面刷新、后端重启或前端状态丢失后判断不准。
        document_status = get_document_status(session_id, knowledge_base_id=knowledge_base_id)
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
    knowledge_base_id: str | None = None,
) -> AgentRouteDecision:
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
    :param knowledge_base_id: 企业知识库 ID，用于判断公共知识库是否已有可检索文档
    :return: AgentRouteDecision，包含路由类型、判断原因、是否开启 RAG、改写后的query
    """
    # 去掉首尾空白，只用于判断是否为空问题
    normalized_question = question.strip()

    # 如果前端没有开启 RAG，则尊重用户设置，走普通对话。
    # 即使当前 session 有文档，也不应该绕过用户开关强行检索。
    if not use_rag:
        return AgentRouteDecision(
            route_type=AgentRouteType.KNOWLEDGE_QA,
            reason="当前未开启 RAG，走普通对话",
            should_use_rag=False,
            rewritten_query=normalized_question,
        )

    # 空问题没有检索价值，直接跳过知识库检索。
    # 这里先拦截空输入，避免后面把空字符串写入 rag_queries 或传给检索器。
    if not normalized_question:
        return AgentRouteDecision(
            route_type=AgentRouteType.KNOWLEDGE_QA,
            reason="当前问题为空，跳过知识库检索。",
            should_use_rag=False,
            rewritten_query=normalized_question,
        )

    # Agent 判断失败但已有知识库文档时，用原问题兜底检索，保证演示链路仍然走 RAG。
    if _has_indexed_rag_document(session_id, knowledge_base_id=knowledge_base_id):
        # 这里返回 True，表示后续流程仍然进入“检索证据”步骤。
        # 第三个返回值使用原始问题，等价于“没有 query rewrite 时，直接用用户问题检索”。
        return AgentRouteDecision(
            route_type=AgentRouteType.KNOWLEDGE_QA,
            reason="Agent 意图分类暂不可用，已按原始问题检索当前会话知识库。",
            should_use_rag=True,
            rewritten_query=normalized_question,
        )

    # 没有模型判断结果且没有可检索文档时，不再用关键词猜测用户意图。
    # 这样可以避免用户只是闲聊或写作时，被后端硬塞进一个没有文档的 RAG 流程。
    return AgentRouteDecision(
        route_type=AgentRouteType.KNOWLEDGE_QA,
        reason="Agent 意图分类暂不可用，且当前会话没有可检索文档，本轮按普通对话处理。",
        should_use_rag=False,
        rewritten_query=normalized_question,
    )


def parse_route_type(value: str | None) -> AgentRouteType:
    """
    将模型返回的 route_type 转成 AgentRouteType。

    :param value: 当前路由类型
    :return: AgentRouteType，Agent路由类型
    """
    if value is None:
        return AgentRouteType.KNOWLEDGE_QA

    normalized_value = value.strip()

    for route_type in AgentRouteType:
        if normalized_value == route_type.name:
            return route_type
        if normalized_value.lower() == route_type.value.lower():
            return route_type

    return AgentRouteType.KNOWLEDGE_QA


def route_question_by_rules(question: str) -> AgentRouteDecision:
    """
    LLM 不可用时使用的规则兜底 Router。

    :param question: 用户问题
    :return: AgentRouteDecision，包含路由类型、判断原因、是否开启 RAG、改写后的query
    """
    normalized_question = question.lower()

    if not normalized_question:
        return AgentRouteDecision(
            route_type=AgentRouteType.IRRELEVANT,
            reason="问题为空，属于无关问题",
            should_use_rag=False,
            rewritten_query=""
        )

    if any(keyword in normalized_question for keyword in ["vpn", "gitlab", "账号", "权限", "电脑", "网络", "登录失败"]):
        return AgentRouteDecision(
            route_type=AgentRouteType.IT_SUPPORT,
            reason="问题包含IT关键词，属于IT支持",
            should_use_rag=True,
            rewritten_query=normalized_question
        )

    if any(keyword in normalized_question for keyword in ["报销", "发票", "差旅", "付款", "费用", "审批金额"]):
        return AgentRouteDecision(
            route_type=AgentRouteType.FINANCE_PROCESS,
            reason="问题包含财务关键词，属于财务流程",
            should_use_rag=True,
            rewritten_query=normalized_question
        )

    if any(keyword in normalized_question for keyword in ["请假", "入职", "转正", "远程办公", "权限申请"]):
        return AgentRouteDecision(
            route_type=AgentRouteType.PROCESS_APPLY,
            reason="问题包含流程申请关键词，属于流程申请",
            should_use_rag=True,
            rewritten_query=normalized_question
        )

    if any(keyword in normalized_question for keyword in ["产品 FAQ", "版本发布", "客户反馈", "功能说明"]):
        return AgentRouteDecision(
            route_type=AgentRouteType.PRODUCT_DOC,
            reason="问题包含产品资料关键词，属于产品资料",
            should_use_rag=True,
            rewritten_query=normalized_question
        )

    return AgentRouteDecision(
            route_type=AgentRouteType.KNOWLEDGE_QA,
            reason="问题未命中特定业务流程关键词，默认按知识问答处理",
            should_use_rag=True,
            rewritten_query=normalized_question
    )



def route_question(
    question: str,
    use_rag: bool,
    client=None,
    session_id: str | None = None,
    knowledge_base_id: str | None = None,
) -> AgentRouteDecision:
    """
    根据用户问题判断 Agent 路由类型。

    函数说明：
    1. 如果前端没有开启 RAG，直接判定为不需要知识库。
    2. 优先使用大模型做路由类型分类、意图分类和 query rewrite。
    3. 如果模型决策失败，则使用保守兜底，保证流程稳定。

    :param question: 用户当前输入的问题
    :param use_rag: 前端是否开启 RAG
    :param client: OpenAI 兼容客户端，用于执行轻量 Agent 意图分类和 query rewrite
    :param session_id: 当前会话 ID，用于模型判断失败时检查是否已有可检索文档
    :param knowledge_base_id: 企业知识库 ID，用于模型判断失败时检查公共知识库是否已有文档
    :return: AgentRouteDecision，包含路由类型、判断原因、是否开启 RAG、改写后的query
    """
    normalized_question = question.strip()

    # 如果前端没有开启 RAG，则尊重用户设置，走普通对话。
    # 这个判断放在最前面，可以避免无意义调用 LLM 做意图分类。
    if not use_rag:
        return AgentRouteDecision(
            route_type=AgentRouteType.KNOWLEDGE_QA,
            reason="当前未开启 RAG，按普通对话处理。",
            should_use_rag=False,
            rewritten_query="",
        )

    # 处理空问题，返回 IRRELEVANT
    if not normalized_question:
        return AgentRouteDecision(
            route_type=AgentRouteType.IRRELEVANT,
            reason="当前问题为空，跳过知识库检索，属于无关问题",
            should_use_rag=False,
            rewritten_query="",
        )

    # 优先让模型做一次轻量路由类型分类、意图分类和 query rewrite，增强 Agent 自主决策能力
    llm_decision = _decide_need_knowledge_base_by_llm(
        question=normalized_question,
        client=client,
    )
    
    # 模型决策成功时直接使用模型结论
    if llm_decision is not None:
        return llm_decision

    # LLM 不可用时的规则兜底 Router
    rule_decision = route_question_by_rules(normalized_question)
    if rule_decision.route_type != AgentRouteType.IRRELEVANT:
        return rule_decision

    # 模型决策失败时进入兜底逻辑。
    # 兜底函数会根据 session_id 再查一次数据库：有文档就用原问题检索，没有文档就按普通对话处理。
    return _build_intent_decision_fallback(
        question=normalized_question,
        use_rag=use_rag,
        session_id=session_id,
        knowledge_base_id=knowledge_base_id,
    )
