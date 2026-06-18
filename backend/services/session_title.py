"""
会话标题生成服务模块。

职责：
1. 根据用户首条输入生成适合侧边栏展示的短标题。
2. 优先使用大模型生成更自然的中文主题。
3. 模型不可用或输出异常时，回退到本地主题提炼，保证主流程稳定。

说明：
- 当前模块只负责标题生成，不负责数据库读写。
- Service 层会在新会话第一次保存消息前调用本模块。
- 标题只用于历史会话列表展示，不参与模型上下文和 RAG 检索。
"""

# 导入项目配置对象，用于读取当前大模型名称
from backend.config import settings


TITLE_KEYWORD_FALLBACKS = (
    ("试用期", "试用期政策咨询"),
    ("年假", "年假政策咨询"),
    ("带薪年假", "年假政策咨询"),
    ("远程办公", "远程办公申请"),
    ("报销", "报销政策咨询"),
    ("健身卡", "健身卡报销咨询"),
    ("晚餐", "晚餐福利咨询"),
    ("请假", "请假流程咨询"),
    ("加班", "加班制度咨询"),
    ("权限", "权限申请咨询"),
    ("合同", "合同规则咨询"),
    ("采购", "采购流程咨询"),
)


def _clean_title_text(text: str, max_length: int = 16) -> str:
    """
    清理并截断会话标题。

    函数说明：
    1. 去掉模型可能返回的引号、标题前缀和多余空白。
    2. 只保留第一行，避免侧边栏按钮过长。
    3. 限制标题长度，让历史会话列表更紧凑。

    :param text: 原始标题文本
    :param max_length: 标题最大字符数
    :return: 清理后的标题
    """
    # 去掉首尾空白和常见包裹符号
    title = str(text or "").strip().strip('"').strip("'").strip("“”")
    # 如果模型返回了多行，只取第一行
    title = title.splitlines()[0].strip() if title else ""
    # 去掉常见的标题前缀
    for prefix in ("标题：", "主题：", "会话标题："):
        if title.startswith(prefix):
            title = title[len(prefix):].strip()
    # 合并中间多余空白
    title = " ".join(title.split())
    # 截断到侧边栏适合展示的长度
    return title[:max_length]


def build_fallback_session_title(user_text: str, mode: str) -> str:
    """
    构造本地兜底会话标题。

    函数说明：
    1. 优先使用用户输入的第一行作为标题来源。
    2. 文件上传场景下跳过附件标记，尽量保留真实问题。
    3. 如果用户输入为空，则使用当前模式名兜底。

    :param user_text: 用户首条展示文本
    :param mode: 当前功能模式
    :return: 兜底会话标题
    """
    # 拆分用户输入行，并过滤空行和附件行
    candidate_lines = [
        line.strip()
        for line in str(user_text or "").splitlines()
        if line.strip() and not line.strip().startswith("【附件】")
    ]
    # 优先使用第一条有效用户文本
    candidate_title = candidate_lines[0] if candidate_lines else mode
    # 如果命中常见企业知识库关键词，则生成更像主题的兜底标题
    for keyword, fallback_title in TITLE_KEYWORD_FALLBACKS:
        if keyword in candidate_title:
            return fallback_title

    # 去掉常见提问口头语，让兜底标题更像主题而不是整句问题
    for token in ("请问", "帮我", "一下", "呀", "呢", "吗", "？", "?", "。", "，", ","):
        candidate_title = candidate_title.replace(token, "")
    # 压缩多余空白
    candidate_title = " ".join(candidate_title.split())
    # 清理并截断兜底标题
    return _clean_title_text(candidate_title) or "未命名会话"


def generate_session_title(user_text: str, mode: str, client) -> str:
    """
    使用大模型生成会话标题。

    函数说明：
    1. 根据当前模式和用户首条输入生成 12 个字以内的中文短标题。
    2. 标题只描述本轮会话主题，不输出标点、引号、解释或 Markdown。
    3. 如果模型调用失败或返回为空，则回退到本地标题。

    :param user_text: 用户首条展示文本
    :param mode: 当前功能模式
    :param client: OpenAI 兼容客户端
    :return: 可用于 chat_sessions.title 的标题
    """
    # 本地兜底标题，保证模型不可用时仍然有可展示标题
    fallback_title = build_fallback_session_title(user_text=user_text, mode=mode)
    # 没有客户端时直接使用兜底标题
    if client is None:
        return fallback_title

    try:
        # 调用大模型生成短标题；非流式即可，避免复杂化主流程
        response = client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是企业知识库问答系统的会话标题生成器。"
                        "请根据用户首条消息生成一个 12 个字以内的中文短标题。"
                        "只输出标题本身，不要解释，不要引号，不要 Markdown，不要句号。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"当前模式：{mode}\n"
                        f"用户首条消息：{user_text}"
                    ),
                },
            ],
            temperature=0,
        )
    except Exception:
        # 标题生成失败不能影响正常对话，直接使用兜底标题
        return fallback_title

    try:
        # 读取模型返回的标题正文
        generated_title = response.choices[0].message.content or ""
    except (AttributeError, IndexError):
        # 响应结构异常时使用兜底标题
        return fallback_title

    # 清理模型标题
    cleaned_title = _clean_title_text(generated_title, max_length=16)
    # 如果清理后为空，使用兜底标题
    return cleaned_title or fallback_title
