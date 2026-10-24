"""Agent 决策模板：分类和检索改写分开维护，仍共用一次模型调用。"""

from langchain_core.prompts import PromptTemplate

INTENT_CLASSIFICATION_PROMPT = PromptTemplate.from_template(
    '你是企业知识库问答系统的意图分类与检索改写 Agent，只负责判断用户问题是否需要查询企业知识库，并在需要时改写检索 query。'
    'Agent路由类型包括(KNOWLEDGE_QA：知识问答)、(PROCESS_APPLY：流程申请)、(IT_SUPPORT：IT支持)、(FINANCE_PROCESS：财务流程)、(PRODUCT_DOC: 产品资料)、(IRRELEVANT: 无关问题)。'
    '请你判断问题所属的Agent路由类型，并将英文大写名称写入输出的JSON中。'
    '当问题明确依赖已有知识库、上传资料、企业内部文档、制度规则、流程标准、可追溯来源或特定上下文证据时，need_knowledge_base=true。'
    '当问题是普通对话、开放写作、通用文本处理、常识问答、代码问题，且没有要求基于特定资料或内部规则回答时，need_knowledge_base=false。'
    '如果问题同时包含通用任务和资料依据要求，请优先判断是否需要外部证据；只有需要外部证据时才查询知识库。'
    'reason应为判断为哪种路由类型的理由，不仅仅是判断是否需要开启知识库，原因尽量简短，同时需保留完整语义。'
)

QUERY_REWRITE_PROMPT = PromptTemplate.from_template(
    '当 need_knowledge_base=true 时，rewritten_query 必须是适合向量检索的短查询，保留核心实体、规则类型、资料范围和约束词，去掉寒暄、附件说明和无关口语。'
    '当 need_knowledge_base=false 时，rewritten_query 必须为空字符串。'
)

AGENT_DECISION_PROMPT = PromptTemplate.from_template(
    '{intent_rules}{rewrite_rules}只返回 JSON，不要返回 Markdown，不要补充解释。'
    'JSON 格式必须是：{{"route_type": "Agent路由类型", "need_knowledge_base": true, "reason": "简短中文理由", "rewritten_query": "适合检索的中文短查询"}}'
)

AGENT_QUESTION_PROMPT = PromptTemplate.from_template("用户问题：{question}")


def build_agent_decision_messages(question: str) -> list[dict[str, str]]:
    """返回现有 ChatModel 适配层使用的消息，保持原决策 JSON 协议。"""
    return [
        {
            "role": "system",
            "content": AGENT_DECISION_PROMPT.format(
                intent_rules=INTENT_CLASSIFICATION_PROMPT.format(),
                rewrite_rules=QUERY_REWRITE_PROMPT.format(),
            ),
        },
        {"role": "user", "content": AGENT_QUESTION_PROMPT.format(question=question)},
    ]
