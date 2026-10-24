"""
LangChain ChatModel 调用适配模块。（适配模块可以理解为一个中间转换层：项目原来的数据格式 <-> LangChain 要求的数据格式）

职责：
1. 把项目原有的 role/content 字典转换成 LangChain Message 对象。
2. 封装 ChatModel.invoke，返回普通字符串，供 Agent Router 和标题生成使用。
3. 封装 ChatModel.stream，逐段产出字符串，供现有 SSE 生成器继续使用。
4. 隔离 LangChain 响应结构，避免 Service 层依赖 AIMessage、AIMessageChunk 等具体类型。

说明：
- 本模块只替换“模型如何调用”，不负责 FastAPI 路由和 SSE 事件格式。
- Service 层仍然负责组装 Prompt、保存消息和生成 StreamEvent。
- 以后切换其他 LangChain ChatModel 时，主要修改 client.py 即可。（这个适配模块不关心底层具体模型是谁。只要 client.py 返回的是 LangChain 的 BaseChatModel，这里就可以继续工作。）

作用：
把项目原来这种普通字典消息：{"role": "user", "content": "你好"}，转换成 LangChain 能识别的 Message 对象，
然后封装 invoke 和 stream，让业务层不用直接接触 LangChain 的复杂返回结构。
更通俗地理解：项目业务层继续使用普通的 role/content 字典，适配层负责转换成 LangChain Message，再统一封装 invoke 和 stream，最后只把普通字符串交还给业务层。

整体数据流：
项目消息字典
{"role": "user", "content": "你好"}
        ↓
convert_chat_messages()
        ↓
HumanMessage(content="你好")
        ↓
client.invoke() / client.stream()
        ↓
AIMessage / AIMessageChunk
        ↓
_extract_text_from_content()
        ↓
普通字符串
"""

# 作用：让 Python 延迟解析类型注解。
# 例如：def get_message() -> BaseMessage: ... 没有延迟解析时，Python 在定义函数时就需要解析 BaseMessage。启用以后，类型标注会以类似字符串的形式保存，等类型检查或真正需要时再处理。
# 主要好处：减少循环导入问题、支持更灵活的前向引用、提高新式类型标注的兼容性。这行必须放在文件顶部，通常只能位于模块 docstring 后面。
from __future__ import annotations

# 导入集合抽象对象。collections.abc 中的类型适合描述运行时可迭代和映射对象。从 collections.abc 导入几个类型，它们主要用于类型标注。
# Iterable 表示可迭代对象。只要能够被 for 循环遍历，就属于可迭代对象。比如 list、tuple、set、generator 都可以是 Iterable，Iterator 表示迭代器。
# Mapping 表示类似字典的“键值映射对象“。比如 {"role": "user", "content": "你好"} 就是一种 Mapping。与直接写 dict 相比：Mapping[str, Any] 表示只要求对象支持读取键值，不要求它必须是真正的普通dict，类型范围更宽。
# Sequence 表示有顺序的序列，比如：list、tuple。消息列表用 Sequence 很合适，因为聊天消息必须保持顺序：
# system
# -> user
# -> assistant
# -> user
# 顺序一旦改变，模型理解的对话上下文也会改变。
from collections.abc import Iterable, Iterator, Mapping, Sequence
# Any 用于兼容 LangChain 文本块中可能出现的字符串或结构化字典。
from typing import Any

# BaseChatModel 是 LangChain 所有聊天模型共同实现的基础接口。
# BaseChatModel 是 LangChain 里聊天模型的统一抽象。可以理解为：不管底层是 DeepSeek、OpenAI、Qwen，只要是 LangChain ChatModel，都可以看成 BaseChatModel。
from langchain_core.language_models.chat_models import BaseChatModel
# 不同 Message 类型分别对应 system、user 和 assistant 三种角色。
# 导入 LangChain 的消息类型。BaseMessage：所有消息类型的基础父类；SystemMessage：系统消息；HumanMessage：用户消息；AIMessage：AI 助手消息。
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage


# ChatMessagePayload 表示项目原有的消息字典，例如 {"role": "user", "content": "你好"}。
# 定义消息字典类型别名。
# 为什么 value 用 Any？因为从接口、数据库、历史数据里来的内容不一定完全干净，所以先允许任意类型，后面再统一转换成字符串。
ChatMessagePayload = Mapping[str, Any]


def _convert_message(message: ChatMessagePayload) -> BaseMessage:
    """
    将单条项目消息转换成 LangChain Message。

    :param message: 包含 role 和 content 的消息字典（即传入一条项目原始消息）
    :return: 对应角色的 LangChain BaseMessage（返回 LangChain 的消息对象）
    """
    # role 决定创建哪一种 LangChain Message；统一转小写方便兼容历史数据。
    # 未知消息默认按用户消息处理。
    # str(...) 把 role 转成字符串。这样即使 role 意外是 None、数字，也不会直接报错。
    # 整体意思：安全地取 role，并统一整理成小写字符串。
    role = str(message.get("role") or "user").strip().lower()
    # 当前项目传给模型的 content 都是文本；遇到 None 时转换为空字符串。（这样可以避免 LangChain Message 接收到 None 后出错）
    # 数字、布尔值等也会被转换成字符串。
    content = str(message.get("content") or "")

    # system 消息用于系统提示词和 RAG 证据上下文。
    # 判断这条消息是不是系统消息，系统消息一般用于：定义 AI 身份、设定回答规则、放入 RAG 上下文、提供系统级约束。
    if role == "system":
        # SystemMessage(content=content) 是 LangChain 的系统消息类型。
        return SystemMessage(content=content)
    # assistant / ai 消息表示历史模型回答。这样可以兼容不同的历史数据格式。
    if role in {"assistant", "ai"}:
        # AIMessage(content=content) 表示这是一条历史 AI 回复。
        return AIMessage(content=content)
    # user / human 消息以及未知角色都按用户消息处理，保持旧历史数据兼容。
    # 如果前面都没命中，就默认返回 HumanMessage(content=content)。
    # 也就是说：role 是 user → HumanMessage, role 是 human → HumanMessage，role 是未知值 → HumanMessage。这样做是为了兼容旧数据，避免因为 role 写错导致程序中断。
    return HumanMessage(content=content)


def convert_chat_messages(messages: Sequence[ChatMessagePayload]) -> list[BaseMessage]:
    """
    批量转换项目消息列表。
    把一组项目原始消息批量转换成 LangChain Message 列表。

    :param messages: 项目原有 role/content 消息列表
    :return: LangChain Message 列表
    """
    # 使用列表推导逐条转换，保持原消息顺序不变。
    return [_convert_message(message) for message in messages]


def _extract_text_from_content(content: Any) -> str:
    """
    从 LangChain 消息 content 中提取文本。

    LangChain 的 content 既可能是字符串，也可能是包含多个文本块的列表。

    :param content: AIMessage 或 AIMessageChunk 的 content
    :return: 可展示的纯文本
    """
    # 最常见情况是普通字符串，可以直接返回。
    if isinstance(content, str):
        return content
    # 非列表结构没有可靠文本格式，使用 str 兜底。
    if not isinstance(content, list):
        return str(content or "")

    # 收集结构化 content 中的所有文本块。
    text_parts: list[str] = []
    for block in content:
        # 有些模型直接返回字符串块。
        if isinstance(block, str):
            text_parts.append(block)
            continue
        # 标准文本块通常把正文放在 text 字段中。
        if isinstance(block, Mapping):
            block_text = block.get("text")
            if isinstance(block_text, str):
                text_parts.append(block_text)

    # 多个文本块按原顺序拼接，保持模型流式输出顺序。
    return "".join(text_parts)


def invoke_chat_model(
    client: BaseChatModel,
    messages: Sequence[ChatMessagePayload],
    *,
    temperature: float | None = None,
) -> str:
    """
    非流式调用 LangChain ChatModel 并返回文本。

    使用场景：Agent 意图分类、query rewrite、会话标题生成。

    :param client: LangChain ChatModel
    :param messages: 项目原有消息列表
    :param temperature: 可选随机性参数；路由和标题通常传 0
    :return: 模型回复文本
    """
    # 转换消息后，Service 层不需要了解 LangChain Message 类型。
    langchain_messages = convert_chat_messages(messages)
    # temperature 只在调用方明确传入时绑定，其他场景沿用模型默认值。
    runnable = client.bind(temperature=temperature) if temperature is not None else client
    # invoke 返回完整 AIMessage。
    response = runnable.invoke(langchain_messages)
    # 统一提取纯文本，屏蔽 LangChain 响应对象差异。
    return _extract_text_from_content(response.content)


# Iterator[str]: 表示这个函数会返回一个可以逐个取出字符串的迭代器。因为函数内容使用了 yield delta，所以它是生成器函数，调用后返回迭代器。
def stream_chat_model(
    client: BaseChatModel,
    messages: Sequence[ChatMessagePayload],
) -> Iterator[str]:
    """
    流式调用 LangChain ChatModel 并逐段产出文本。

    本函数只负责把 AIMessageChunk 转成字符串；外层原有 SSE 生成器继续负责
    包装 StreamEvent、调用 to_sse 和返回 StreamingResponse。

    :param client: LangChain ChatModel
    :param messages: 项目原有消息列表
    :return: 文本增量迭代器
    """
    # 先把字典消息转换为 LangChain Message，保持 Prompt 内容和顺序不变。
    langchain_messages = convert_chat_messages(messages)
    # stream 返回 AIMessageChunk 迭代器，模型每生成一段就会得到一个 chunk。
    # chunks 是一组可以逐个遍历的消息对象。
    chunks: Iterable[BaseMessage] = client.stream(langchain_messages)

    # 逐段读取 LangChain chunk，并只向外产出非空文本。
    for chunk in chunks:
        delta = _extract_text_from_content(chunk.content)
        if delta:
            yield delta
