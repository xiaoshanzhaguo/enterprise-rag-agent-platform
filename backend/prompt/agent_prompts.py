"""Agent 决策模板：分类和检索改写分开维护，仍共用一次模型调用。"""

from langchain_core.prompts import PromptTemplate

INTENT_CLASSIFICATION_PROMPT = PromptTemplate.from_template(
    '只有用户明确要求办理、发起或提交申请时才归为 PROCESS_APPLY，包括权限、账号、请假、报销、付款等实际申请。'
    '咨询申请条件、审批人、时限、材料或金额标准属于制度问答，不是发起申请；仅出现“申请”二字不能判为 PROCESS_APPLY。'
    '财务制度咨询归为 FINANCE_PROCESS，IT规则咨询和故障排查归为 IT_SUPPORT，HR制度咨询归为 KNOWLEDGE_QA，产品资料咨询归为 PRODUCT_DOC。'
    '按问题的核心业务对象和制度用途分类，不要求出现部门名称。版本上线准备、需求冻结或锁定、分批开放与灰度放量、回滚条件、客户问题分级及处理进度规范，均属于 PRODUCT_DOC。'
    '产品发布规范即使涉及登录、账号或权限，也仍按发布治理归为 PRODUCT_DOC；用户当前账号登录失败、VPN连接错误等实际技术故障排查归为 IT_SUPPORT。'
    '你是企业知识库问答系统的意图分类与检索改写 Agent，只负责判断用户问题是否需要查询企业知识库，并在需要时改写检索 query。'
    'Agent路由类型包括(KNOWLEDGE_QA：知识问答)、(PROCESS_APPLY：流程申请)、(IT_SUPPORT：IT支持)、(FINANCE_PROCESS：财务流程)、(PRODUCT_DOC: 产品资料)、(IRRELEVANT: 无关问题)。'
    '请你判断问题所属的Agent路由类型，并将英文大写名称写入输出的JSON中。'
    '当问题明确依赖已有知识库、上传资料、企业内部文档、制度规则、流程标准、可追溯来源或特定上下文证据时，need_knowledge_base=true。'
    '询问特定公司的奖金、税号、服务器地址或其他内部事实也需要知识库，即使资料可能没有答案，也不能据此判为 IRRELEVANT 或跳过检索。'
    '例如“报销需要多久内提交”是 FINANCE_PROCESS；“我是财务部王芳，请帮我提交报销申请”是 PROCESS_APPLY。'
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
