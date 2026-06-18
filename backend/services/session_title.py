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
- 本地兜底逻辑不维护业务关键词表，只从用户首条输入中提炼通用短标题。
"""

# 导入项目配置对象，用于读取当前大模型名称
from backend.config import settings


# 本地标题兜底时需要移除的常见开头表达，避免侧边栏标题像完整口语请求
TITLE_LEADING_PHRASES = (
    "请帮我",
    "帮我",
    "请问",
    "麻烦",
    "能不能",
    "可以",
    "写一封",
    "写一份",
    "写一个",
    "生成一份",
    "生成一个",
)

# 本地标题兜底时需要移除的轻量语气词和标点
TITLE_NOISE_TOKENS = (
    "一下",
    "一下子",
    "吗",
    "呢",
    "呀",
    "啊",
    "？",
    "?",
    "。",
    "，",
    ",",
    "！",
    "!",
    "：",
    ":",
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


def _strip_fallback_title_noise(text: str) -> str:
    """
    清理本地兜底标题中的口语噪声。

    函数说明：
    1. 移除常见请求开头，让标题更像主题而不是完整句子。
    2. 移除轻量语气词和标点，让侧边栏展示更紧凑。
    3. 不使用业务关键词表，避免标题兜底逻辑过度定制。

    :param text: 候选标题文本
    :return: 清理后的候选标题
    """
    # 先去掉首尾空白，避免后续 startswith 判断受空格影响
    title = str(text or "").strip()
    # 连续移除常见开头表达，例如“帮我写一封”会先去掉“帮我”，再去掉“写一封”
    # 用一个开关控制循环：只要本轮成功删掉了开头短语，就再检查一轮
    has_removed_phrase = True
    while has_removed_phrase:
        # 每轮先假设没有移除任何内容
        # 如果这一轮没有删掉任何短语，循环就会自然结束
        has_removed_phrase = False
        # 遍历常见开头表达
        for phrase in TITLE_LEADING_PHRASES:
            if title.startswith(phrase):
                title = title[len(phrase):].strip()
                # 本轮删掉了一个短语，说明标题开头可能还有其他噪声，需要继续下一轮
                has_removed_phrase = True
                break
    # 移除标题里常见的轻量语气词和标点
    for token in TITLE_NOISE_TOKENS:
        title = title.replace(token, "")
    # 压缩多余空白
    return " ".join(title.split())


def build_fallback_session_title(user_text: str, mode: str) -> str:
    """
    构造本地兜底会话标题。

    函数说明：
    1. 优先使用用户输入的第一行作为标题来源。
    2. 文件上传场景下跳过附件标记，尽量保留真实问题。
    3. 通过通用文本清理生成短标题，不维护企业制度关键词表。
    4. 如果用户输入为空，则使用当前模式名兜底。

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
    # 去掉常见提问口头语和标点，让兜底标题更像主题而不是整句问题
    candidate_title = _strip_fallback_title_noise(candidate_title)
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
