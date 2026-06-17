"""
前端主页面模块（app.py）。

职责：
1. 负责初始化 Streamlit 页面，并组织整个企业知识库问答 Agent 的前端交互流程
2. 管理多模式会话状态，包括不同模式下的 session_id、历史消息、最近会话列表、数据库历史恢复与旧 JSON 历史兜底
3. 统一处理用户输入，包括纯文本输入、文件上传输入以及启用 RAG 时的文档索引逻辑
4. 调用后端聊天接口、工作流接口、RAG 接口和会话管理接口，并解析流式 SSE 响应
5. 渲染历史消息、当前结果、每条回答对应的 RAG 引用来源、结果复制、Markdown 导出和 workflow 分步复制等前端展示能力
6. 在左侧边栏展示项目名称、模式选择、对话设置和最近历史会话，并按当前模式展示可用的 RAG 设置
7. 支持新建会话、点击历史会话恢复对应消息，并通过单条删除按钮清理指定会话及其数据库关联数据

说明：
- 当前模块属于前端入口层，负责把页面状态管理、用户输入处理、后端请求发送和结果展示串起来
- 页面基于 Streamlit 构建，后端依赖 FastAPI 提供聊天流式接口、工作流接口、RAG 接口和 SQLite 会话持久化接口
- 当前实现支持“多模式 + 会话隔离 + 文件上传分析 + 向量 RAG + SQLite 历史持久化 + 旧 JSON 历史兜底”的完整交互链路
"""

# 导入时间模块，使得后面流式输出时用 time.sleep(0.01) 让文本增长更自然
import sys
import time
from datetime import datetime
from pathlib import Path
# 导入 uuid4()，生成新的唯一会话 ID，每次重置当前前端会话时都会生成新的 session_id
from uuid import uuid4


# 计算项目根目录路径
PROJECT_ROOT = Path(__file__).resolve().parents[1]
# 如果项目根目录还不在 Python 模块搜索路径中，则插入到最前面，
# 这样当前文件运行时才能正常导入 backend / frontend 下的模块
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# 导入 Streamlit 主模块，后面所有页面组件都通过 st.xxx() 调用
import streamlit as st

from backend.utils.workflow_formatter import format_workflow_blocks

# 导入“前端请求封装层”里的函数
from frontend.api_client import (
    clear_chat_session, # 删除后端数据库中的聊天会话
    index_uploaded_document, # 通知后端给文档建立索引
    iter_sse_events, # 逐条解析后端返回的 SSE 事件流
    get_rag_preview, # 获取本次 query 命中的 RAG 片段摘要
    get_rag_status, # 获取当前会话的 RAG 状态
    list_recent_chat_sessions, # 获取最近更新的历史会话列表
    load_chat_history, # 从后端数据库恢复聊天历史
    load_chat_session, # 根据 session_id 获取指定历史会话详情
    post_stream_request # 统一发送流式请求到后端
)

# 导入文件处理模块中的函数
from frontend.file_parser import (
    build_non_rag_input_text, # 不启用 RAG 时，把“文件全文 + 用户要求”拼成后端输入
    build_text_fingerprint, # 给文档文本生成指纹，用于判断是否需要重新索引
    build_user_display_text, # 构造前端聊天区展示文本，避免直接展示完整文件内容
    extract_text_from_uploaded_file # 从 txt / md / pdf 中提取文本
)

# 导入展示层函数
from frontend.renderers import (
    render_rag_preview, # 渲染 RAG 命中片段预览
    render_result_actions, # 渲染复制 / 导出按钮
    render_workflow_step_copy_actions # 渲染 workflow 分步复制按钮
)

# 导入状态管理函数
from frontend.state_manager import (
    build_history_for_api, # 把前端历史消息转换成后端可接受的 history
    ensure_mode_sessions, # 确保所有模式都有对应的会话状态
    load_mode_sessions # 从旧 JSON 文件恢复历史，作为数据库不可用时的兼容兜底
)


# -----------------------------
# 页面基础配置
# -----------------------------
st.set_page_config(
    page_title="企业知识库问答 Agent", # 浏览器标签页标题
    page_icon="🤖", # 页面图标
    layout="wide", # 宽屏布局
    initial_sidebar_state="expanded", # 默认展开侧边栏
    menu_items={} # 隐藏默认菜单项
)

# 注入少量页面样式，用于整理侧边栏项目名和主区当前模式展示，整体保持工具型界面的克制感
st.markdown(
    """
    <style>
    .block-container {
        padding-top: 3.25rem;
    }
    section[data-testid="stSidebar"] [data-testid="stSidebarHeader"] {
        display: none;
    }
    section[data-testid="stSidebar"] [data-testid="stSidebarContent"] {
        padding-top: 0 !important;
    }
    section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
        gap: 10px;
    }
    section[data-testid="stSidebar"] hr {
        margin: 0 0 30px 0;
    }
    .sidebar-brand {
        margin: 1rem 0;
        color: rgb(38, 39, 48);
        font-size: 1.28rem;
        font-weight: 760;
        line-height: 1.35;
    }
    .mode-header {
        margin: 0 0 0.9rem 0;
        padding: 0 0 0.72rem 0;
        border-bottom: 1px solid rgba(49, 51, 63, 0.12);
    }
    .mode-kicker {
        margin-bottom: 0.22rem;
        color: rgba(49, 51, 63, 0.62);
        font-size: 0.78rem;
        font-weight: 600;
    }
    .mode-title {
        color: rgb(38, 39, 48);
        font-size: 1.28rem;
        font-weight: 700;
        line-height: 1.25;
    }
    .mode-description {
        margin-top: 0.24rem;
        color: rgba(49, 51, 63, 0.68);
        font-size: 0.9rem;
        line-height: 1.45;
    }
    .mode-category {
        margin-top: 0.45rem;
        color: rgba(49, 51, 63, 0.52);
        font-size: 0.78rem;
        font-weight: 600;
    }
    .sidebar-static-mode {
        height: 68px;
        display: flex;
        flex-direction: column;
        justify-content: center;
        box-sizing: border-box;
    }
    .sidebar-static-mode-label {
        margin: 0 0 0.5rem 0;
        color: rgba(49, 51, 63, 0.68);
        font-size: 0.9rem;
        line-height: 1.35;
    }
    .sidebar-static-mode-value {
        min-height: 24px;
        display: flex;
        align-items: center;
        color: rgb(38, 39, 48);
        font-size: 0.95rem;
        font-weight: 650;
        line-height: 1.3;
    }
    section[data-testid="stSidebar"] [data-testid="stSelectbox"] {
        margin-bottom: -1rem;
    }
    section[data-testid="stSidebar"] [data-testid="stExpander"] {
        margin-top: 8px;
    }
    .sidebar-history-title {
        margin: 12px 0 10px 0;
        color: rgba(49, 51, 63, 0.82);
        font-size: 0.92rem;
        font-weight: 700;
        line-height: 1.35;
    }
    .sidebar-history-empty {
        margin: 0 0 8px 0;
        color: rgba(49, 51, 63, 0.56);
        font-size: 0.86rem;
        line-height: 1.45;
    }
    section[data-testid="stSidebar"] .st-key-sidebar_history_list[data-testid="stVerticalBlock"],
    section[data-testid="stSidebar"] .st-key-sidebar_history_list [data-testid="stVerticalBlock"] {
        /* 只压缩会话历史列表内部间距，避免影响整个侧边栏的功能区和设置区 */
        gap: 0;
    }
    .sidebar-history-row-gap {
        height: 5px;
    }
    section[data-testid="stSidebar"] .st-key-sidebar_history_list [data-testid="stElementContainer"]:has(.sidebar-history-row-gap),
    section[data-testid="stSidebar"] .st-key-sidebar_history_list [data-testid="stMarkdown"]:has(.sidebar-history-row-gap),
    section[data-testid="stSidebar"] .st-key-sidebar_history_list [data-testid="stMarkdownContainer"]:has(.sidebar-history-row-gap) {
        /* Streamlit 会给 markdown 外层包一层高度为 0 的容器，这里让 spacer 真正参与历史行布局 */
        min-height: 5px;
        height: 5px;
    }
    </style>
    """,
    unsafe_allow_html=True
)

# 左侧边栏顶部只展示项目名称，让侧边栏的应用身份更简洁清晰
st.sidebar.markdown(
    '<div class="sidebar-brand">企业知识库问答 Agent</div>',
    unsafe_allow_html=True
)
st.sidebar.markdown(
    '<div style="border-top: 1px solid rgba(49, 51, 63, 0.18); margin: 0 0 10px 0;"></div>',
    unsafe_allow_html=True
)


# -----------------------------
# 模式映射
# 前端展示名称 -> 后端 task_type
# persona 先沿用展示名称，便于后端按人设/风格扩展
# -----------------------------
MODE_TO_TASK_TYPE = {
    "企业知识库问答": "agent",
    "内容分析": "summary",
    "结构优化": "rewrite",
    "风格改写": "rewrite",
    "多版本生成": "chat",
    "工作流优化": "workflow"
}

# 每个模式的简短说明文案。当前模式切换后，给用户一个简短提示，帮助理解这个模式是干什么的
MODE_DESCRIPTIONS = {
    "企业知识库问答": "上传企业文档后，系统会判断问题是否需要知识库、检索证据，并生成带来源引用的回答。",
    "内容分析": "附加文本处理能力，用于提炼主题、关键信息和结论。",
    "结构优化": "附加文本处理能力，用于整理表达层次和逻辑结构。",
    "风格改写": "附加文本处理能力，用于保持原意并调整表达语气。",
    "多版本生成": "附加文本处理能力，用于生成不同场景可直接使用的表达版本。",
    "工作流优化": "附加文本处理能力，用于分步骤总结、分析并提出建议。"
}

# 核心功能列表：默认优先展示企业知识库问答，让用户一进入项目就看到主定位
CORE_MODES = ["企业知识库问答"]

# 附加功能列表：保留原有内容处理能力，但不再作为项目主定位
AUXILIARY_MODES = [
    "内容分析",
    "结构优化",
    "风格改写",
    "多版本生成",
    "工作流优化",
]

# 当前前端支持的所有模式列表，核心功能排在最前面，用于历史恢复和会话状态初始化
AVAILABLE_MODES = CORE_MODES + AUXILIARY_MODES


def sync_mode_from_group() -> None:
    """
    根据功能类型同步当前模式。

    函数说明：
    1. 切到核心功能时，当前模式固定为企业知识库问答。
    2. 切到附加功能时，当前模式使用最近一次选择的附加模式。
    3. 这样既能突出主入口，也避免下拉框里重复显示“附加功能”前缀。

    :return: None
    """
    # 读取当前功能类型；没有时默认核心功能
    mode_group = st.session_state.get("mode_group", "核心功能")
    # 如果是核心功能，则固定进入企业知识库问答
    if mode_group == "核心功能":
        st.session_state.selected_mode = CORE_MODES[0]
        return

    # 如果是附加功能，则使用最近一次选择的附加模式；没有时默认第一个附加模式
    st.session_state.selected_mode = st.session_state.get("auxiliary_mode", AUXILIARY_MODES[0])


def format_history_session_time(timestamp_text: str | None) -> str | None:
    """
    将数据库时间格式化为会话历史显示名称。

    函数说明：
    1. 接收数据库中的 created_at 或 updated_at 字符串。
    2. 优先按 YYYY-MM-DD HH:MM:SS 解析。
    3. 兼容 ISO 格式时间字符串。
    4. 格式化为 YYYY-MM-DD_HH-MM-SS，作为更简洁的会话历史名称。

    :param timestamp_text: 数据库返回的时间字符串
    :return: 格式化后的时间名称；解析失败时返回 None
    """
    # 如果没有时间字符串，直接返回 None，让外层继续使用其他兜底字段
    if not timestamp_text:
        return None

    # 去掉首尾空白，避免数据库值或接口值带空格影响解析
    normalized_timestamp = str(timestamp_text).strip()
    # 如果清理后为空字符串，直接返回 None
    if not normalized_timestamp:
        return None

    # 当前数据库默认保存的是本地时间字符串，例如 2026-06-14 19:30:00
    parse_patterns = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ]

    # 逐个尝试已知时间格式
    for pattern in parse_patterns:
        try:
            # 解析时间字符串。将字符串转换成 datatime 对象
            parsed_time = datetime.strptime(normalized_timestamp, pattern)
        except ValueError:
            # 当前格式不匹配时继续尝试下一个格式
            continue

        # 返回用户希望的会话历史名称格式。将 datetime对象 转换成字符串
        return parsed_time.strftime("%Y-%m-%d_%H-%M-%S")

    try:
        # 兼容带毫秒或时区的 ISO 字符串
        parsed_time = datetime.fromisoformat(normalized_timestamp)
    except ValueError:
        # 所有解析方式都失败时返回 None
        return None

    # 返回用户希望的会话历史名称格式
    return parsed_time.strftime("%Y-%m-%d_%H-%M-%S")


def build_history_session_label(session: dict) -> str:
    """
    构造侧边栏历史会话展示文案。

    函数说明：
    1. 优先使用 updated_at 作为会话历史名称。
    2. 如果 updated_at 不存在，则使用 created_at 兜底。
    3. 时间统一格式化为 YYYY-MM-DD_HH-MM-SS，让左侧会话历史更简洁。
    4. 如果时间字段异常，再使用原始 title 兜底。

    :param session: 后端返回的会话摘要字典
    :return: 适合侧边栏展示的会话文案
    """
    # 优先使用 updated_at，表示这个会话最近一次发生变化的时间
    formatted_updated_time = format_history_session_time(session.get("updated_at"))
    # 如果 updated_at 可以解析，则直接作为会话历史名称
    if formatted_updated_time:
        return formatted_updated_time

    # updated_at 不可用时，使用 created_at 作为兜底
    formatted_created_time = format_history_session_time(session.get("created_at"))
    # 如果 created_at 可以解析，则直接作为会话历史名称
    if formatted_created_time:
        return formatted_created_time

    # 如果时间字段都异常，则保留原始标题兜底，避免按钮为空
    fallback_title = " ".join(str(session.get("title") or "未命名会话").split())
    # 返回兜底标题
    return fallback_title or "未命名会话"


def restore_history_session(session: dict) -> bool:
    """
    将指定历史会话恢复为当前前端会话。

    函数说明：
    1. 校验后端返回的会话模式是否属于当前前端支持的模式。
    2. 将该 session_id 和 messages 写入对应模式的 mode_sessions。
    3. 同步左侧功能类型与当前模式，保证页面刷新后展示正确入口。
    4. 清理该模式的前端 RAG 指纹缓存，让文档状态以后端数据库为准重新判断。

    :param session: 后端返回的会话详情字典
    :return: True 表示恢复成功；False 表示会话结构不合法
    """
    # 读取会话 ID
    session_id = str(session.get("session_id") or "")
    # 读取会话所属模式
    session_mode = str(session.get("mode") or "")
    # 读取会话消息列表
    messages = session.get("messages", [])

    # 会话 ID、模式和消息列表任一不合法，都不能恢复
    if not session_id or session_mode not in AVAILABLE_MODES or not isinstance(messages, list):
        return False

    # 把历史会话写回它所属模式的前端状态
    st.session_state.mode_sessions[session_mode] = {
        "session_id": session_id,
        "messages": messages
    }

    # 如果恢复的是核心功能，则左侧功能类型切回核心功能
    if session_mode in CORE_MODES:
        st.session_state.mode_group = "核心功能"
    else:
        # 如果恢复的是附加功能，则左侧功能类型切回附加功能
        st.session_state.mode_group = "附加功能"
        # 同步附加功能下拉框选中项
        st.session_state.auxiliary_mode = session_mode

    # 同步当前模式
    st.session_state.selected_mode = session_mode
    # 清理该模式前端内存索引状态，后续 RAG 状态重新以数据库查询结果为准
    st.session_state.rag_index_state.pop(session_mode, None)
    # 返回恢复成功
    return True


def reset_mode_to_empty_session(session_mode: str) -> None:
    """
    将指定模式重置为一个新的空前端会话。

    函数说明：
    1. 生成新的 session_id，但不立即写入数据库。
    2. 清空该模式的前端消息列表。
    3. 清理该模式的前端 RAG 指纹缓存。
    4. 用于删除当前正在使用的历史会话后重置前端状态。

    :param session_mode: 需要重置的前端模式名称
    :return: None
    """
    # 如果传入模式不在当前支持列表中，则不处理
    if session_mode not in AVAILABLE_MODES:
        return

    # 生成新的会话 ID，只更新前端状态；数据库等用户真正发送内容时再创建记录
    new_session_id = str(uuid4())

    # 重置指定模式下正在使用的会话
    st.session_state.mode_sessions[session_mode] = {
        "session_id": new_session_id,
        "messages": []
    }

    # 清空该模式的前端索引状态缓存
    st.session_state.rag_index_state.pop(session_mode, None)


def render_sidebar_session_area(active_mode: str) -> None:
    """
    渲染侧边栏会话入口区域。

    函数说明：
    1. 在项目标题下方展示“新建会话”和最近会话列表。
    2. 点击历史会话时，把待恢复会话写入 pending_history_session，下一轮页面重跑时再恢复。
    3. 删除历史会话时，同步清理数据库记录和前端缓存状态。
    4. 该区域放在功能选择之前，让用户先看到当前项目的会话上下文。

    :param active_mode: 当前前端正在使用的模式名称
    :return: None
    """
    # 如果当前模式不在支持列表中，则不渲染会话区域，避免异常状态影响页面启动
    if active_mode not in AVAILABLE_MODES:
        return

    # 读取当前模式正在使用的 session_id，用于高亮当前会话
    active_session_id = st.session_state.mode_sessions[active_mode]["session_id"]

    # 侧边栏新建会话按钮
    # 新建会话只切换到新的空 session，不删除旧会话；旧会话继续留在数据库和历史列表中
    if st.sidebar.button(
        "新建会话",
        width="stretch",
        icon="✏️"
    ):
        # 当前项目在发送消息时已自动落库，这里只需要重置前端当前模式到新的空会话
        reset_mode_to_empty_session(active_mode)
        # 刷新页面，让新的 session_id、RAG 状态和输入区立即生效
        st.rerun()

    # 侧边栏会话历史列表
    st.sidebar.markdown(
        '<div class="sidebar-history-title">会话历史</div>',
        unsafe_allow_html=True
    )

    # 从后端数据库读取最近10条非空会话，用于展示 SQLite 持久化历史能力
    recent_sessions = list_recent_chat_sessions(limit=10)

    # 如果没有历史会话，则展示轻量空状态
    if not recent_sessions:
        st.sidebar.markdown(
            '<div class="sidebar-history-empty">暂无会话历史，发送第一条消息后会自动出现。</div>',
            unsafe_allow_html=True
        )
        return

    # 过滤出 session_id 合法的会话，避免异常数据影响按钮 key
    valid_recent_sessions = [
        session
        for session in recent_sessions
        if session.get("session_id")
    ]

    # 如果过滤后没有可用会话 ID，则展示空状态
    if not valid_recent_sessions:
        st.sidebar.markdown(
            '<div class="sidebar-history-empty">暂无可展示的历史会话。</div>',
            unsafe_allow_html=True
        )
        return

    # 在侧边栏容器中渲染“加载 + 删除”两列按钮
    with st.sidebar.container(key="sidebar_history_list"):
        # 遍历最近10条会话，保持后端按更新时间倒序返回的顺序
        for index, session in enumerate(valid_recent_sessions):
            # 读取当前行的会话 ID
            history_session_id = str(session.get("session_id"))
            # 读取当前行的会话模式，用于删除当前模式缓存时定位
            history_session_mode = str(session.get("mode") or "")
            # 判断当前行是否就是页面正在展示的会话
            is_current_history_session = history_session_id == active_session_id
            # 构造侧边栏按钮展示文案
            history_label = build_history_session_label(session)

            # 一行分成两列：左侧加载会话，右侧删除会话
            load_col, delete_col = st.columns([4, 1])

            with load_col:
                # 点击左侧按钮时加载对应会话；当前会话使用 primary 高亮
                if st.button(
                    history_label,
                    width="stretch",
                    icon="📄",
                    key=f"load_history_{history_session_id}",
                    type="primary" if is_current_history_session else "secondary"
                ):
                    # 当前会话已经在页面上，不需要重复恢复
                    if is_current_history_session:
                        st.rerun()

                    # 从后端读取完整会话详情
                    selected_session = load_chat_session(history_session_id)
                    # 如果读取成功，则先写入待恢复状态，下一轮在控件渲染前真正恢复
                    if selected_session:
                        st.session_state.pending_history_session = selected_session
                        st.rerun()
                    else:
                        # 恢复失败时给出侧边栏提示，不影响当前会话继续使用
                        st.sidebar.warning("历史会话恢复失败，请稍后重试。")

            with delete_col:
                # 点击右侧按钮时删除该会话；只删除这一条历史会话，不影响其他会话
                if st.button(
                    "",
                    width="stretch",
                    icon="❌",
                    key=f"delete_history_{history_session_id}"
                ):
                    # 删除数据库中的会话及其关联消息、文档和 RAG 记录
                    clear_chat_session(history_session_id)

                    # 如果删除的是当前正在展示的会话，则把该模式重置为空会话
                    if is_current_history_session:
                        reset_mode_to_empty_session(active_mode)
                    # 如果删除的是其他模式当前缓存的会话，也同步清理该模式的前端状态
                    elif (
                        history_session_mode in st.session_state.mode_sessions
                        and st.session_state.mode_sessions[history_session_mode]["session_id"] == history_session_id
                    ):
                        reset_mode_to_empty_session(history_session_mode)

                    # 删除完成后刷新页面，更新侧边栏历史列表
                    st.rerun()

            # 如果后面还有历史会话，则插入 5px 间距，让多条会话之间更容易区分
            if index < len(valid_recent_sessions) - 1:
                st.markdown(
                    '<div class="sidebar-history-row-gap"></div>',
                    unsafe_allow_html=True
                )


# -----------------------------
# Session State 初始化
# 如果不存在，或被清空为 {}，则重新初始化
# 这里必须放在功能类型 radio 渲染之前：
# - 历史会话恢复可能需要同步 mode_group / auxiliary_mode
# - Streamlit 不允许在 radio 渲染后再修改同名 session_state
# -----------------------------
if "mode_sessions" not in st.session_state or not st.session_state.mode_sessions:
    # 页面初始化时优先从后端 SQLite 数据库恢复历史会话；
    # 如果后端不可用、请求失败或数据库暂无历史，则回退读取旧版本地 JSON 历史。
    db_mode_sessions = load_chat_history(AVAILABLE_MODES)
    st.session_state.mode_sessions = db_mode_sessions or load_mode_sessions(AVAILABLE_MODES)

# 确保所有当前支持的模式都有自己的会话容器
st.session_state.mode_sessions = ensure_mode_sessions(
    st.session_state.mode_sessions,
    AVAILABLE_MODES
)

# -----------------------------
# 初始化前端文件指纹缓存
# 用于记录当前页面运行期间上传文件的指纹，避免同一 session 重复索引同一份文件
# 当前 RAG 文档状态以数据库 /rag_status 返回结果为准
# -----------------------------
if "rag_index_state" not in st.session_state:
    st.session_state.rag_index_state = {}


# 初始化功能类型和当前模式，保证首次进入页面默认展示企业知识库问答
if "selected_mode" not in st.session_state:
    st.session_state.selected_mode = CORE_MODES[0]

if "mode_group" not in st.session_state:
    st.session_state.mode_group = "核心功能"

if "auxiliary_mode" not in st.session_state:
    st.session_state.auxiliary_mode = AUXILIARY_MODES[0]


# 如果上一轮点击了历史会话加载按钮，则在控件渲染前恢复状态
pending_history_session = st.session_state.pop("pending_history_session", None)
# 只有字典结构才尝试恢复，避免异常状态影响页面启动
if isinstance(pending_history_session, dict):
    restore_history_session(pending_history_session)


# 先根据当前 session_state 推导一个侧边栏会话区域使用的模式。
# 该区域显示在功能选择之前，因此不能依赖后面 radio 渲染之后才得到的 mode 变量。
sidebar_session_mode = st.session_state.selected_mode

# 会话入口放在项目标题下方，让用户先看到当前项目的会话上下文
render_sidebar_session_area(sidebar_session_mode)

st.sidebar.markdown(
    '<div style="border-top: 1px solid rgba(49, 51, 63, 0.18); margin: 10px 0 10px 0;"></div>',
    unsafe_allow_html=True
)


# 先选择功能类型，避免在一个下拉框里重复显示“附加功能”前缀
st.sidebar.radio(
    "功能类型",
    ["核心功能", "附加功能"],
    key="mode_group",
    horizontal=True,
    on_change=sync_mode_from_group
)

# 核心功能使用静态展示，附加功能使用下拉框；静态区域固定高度，保证底部分割线位置稳定
if st.session_state.mode_group == "核心功能":
    # 核心功能当前只有企业知识库问答，不做成下拉框，避免用户误以为还有其他核心选项
    mode = CORE_MODES[0]
    st.session_state.selected_mode = mode
    # 用固定高度的静态块对齐附加功能下拉框区域，让下方横线切换时不跳动
    st.sidebar.markdown(
        """
        <div class="sidebar-static-mode">
            <div class="sidebar-static-mode-label">当前核心功能</div>
            <div class="sidebar-static-mode-value">企业知识库问答</div>
        </div>
        """,
        unsafe_allow_html=True
    )
else:
    # 附加功能使用独立下拉框，选项里不再重复出现“附加功能”前缀
    mode = st.sidebar.selectbox(
        "选择附加功能",
        AUXILIARY_MODES,
        key="auxiliary_mode"
    )
    st.session_state.selected_mode = mode

# 用横线把功能选择区域和对话设置区域分开，让侧边栏层级更清楚
st.sidebar.markdown(
    '<div style="border-top: 1px solid rgba(49, 51, 63, 0.18); margin: 10px 0 8px 0;"></div>',
    unsafe_allow_html=True
)

# 根据当前模式判断它属于核心功能还是附加功能
mode_category = "核心功能" if mode in CORE_MODES else "附加功能"

# 在主内容区顶部展示当前模式和定位。企业知识库问答作为主场景，附加模式作为辅助文本处理能力。
st.markdown(
    f"""
    <div class="mode-header">
        <div class="mode-kicker">当前入口</div>
        <div class="mode-title">{mode}</div>
        <div class="mode-category">{mode_category}</div>
        <div class="mode-description">{MODE_DESCRIPTIONS[mode]}</div>
    </div>
    """,
    unsafe_allow_html=True
)

# -----------------------------
# 启用 RAG 设置的模式
# 内容分析 / 工作流优化仍保留原有 RAG 能力；企业知识库问答由 Agent 路由决策是否真正检索
# -----------------------------
RAG_ENABLED_MODES = {
    "内容分析",
    "工作流优化",
    "企业知识库问答"
}

# 企业知识库问答模式名称。单独抽成常量，避免后续判断里重复写字符串
AGENT_MODE_NAME = "企业知识库问答"

# RAG 无命中时后端返回的固定提示。前端用它判断是否需要隐藏复制 / 导出等结果操作按钮
NO_RAG_EVIDENCE_MESSAGE = "知识库中没有找到依据。"


def build_result_copy_text(
    result_text: str,
    workflow_blocks: dict[str, str] | None = None,
    preferred_step_name: str | None = None,
) -> str:
    """
    构造“复制当前结果”按钮实际复制的文本。

    函数说明：
    1. 普通结果默认复制完整结果文本。
    2. Agent 这类分步骤结果可以指定 preferred_step_name，只复制最终回答步骤。
    3. 如果指定步骤不存在或内容为空，则回退复制完整展示文本。

    :param result_text: 当前页面展示的完整结果文本
    :param workflow_blocks: workflow / Agent 分步骤结果字典
    :param preferred_step_name: 优先复制的步骤名，例如 generate_answer
    :return: 复制按钮实际写入剪贴板的文本
    """
    # 如果调用方指定了优先复制的步骤，并且传入了分步骤数据，则先尝试取这一步的内容
    if preferred_step_name and isinstance(workflow_blocks, dict):
        # 读取指定步骤内容，并去掉首尾空白
        preferred_text = workflow_blocks.get(preferred_step_name, "").strip()
        # 指定步骤存在有效内容时，直接作为复制文本
        if preferred_text:
            return preferred_text

    # 没有指定步骤或步骤内容为空时，回退到完整结果文本
    return result_text.strip()


def is_no_rag_evidence_result(result_text: str) -> bool:
    """
    判断当前 assistant 结果是否只是 RAG 无依据提示。

    函数说明：
    1. RAG 无依据提示不是可交付内容。
    2. 如果继续展示“复制当前结果 / 导出 Markdown”，用户容易误以为这是一个正式结果。
    3. 因此命中该提示时隐藏结果操作按钮。

    :param result_text: assistant 最终展示文本
    :return: True 表示当前结果只是知识库无依据提示
    """
    # 去掉首尾空白，避免换行影响判断
    normalized_text = result_text.strip()
    # 普通模式会直接返回固定无依据提示
    if normalized_text == NO_RAG_EVIDENCE_MESSAGE:
        return True

    # workflow / Agent 模式会把无依据提示放进分步骤 Markdown 中，需要逐行过滤标题和无依据提示
    meaningful_lines = []
    # 分步骤固定展示标题关键词。用关键词判断，比完整匹配 emoji 标题更稳
    workflow_title_keywords = {
        "内容总结",
        "问题分析",
        "优化建议",
        "判断是否需要知识库",
        "检索证据",
        "生成回答",
    }

    # 逐行检查是否还存在真正的回答内容
    for line in normalized_text.splitlines():
        # 去掉当前行首尾空白
        stripped_line = line.strip()
        # 空行不算有效内容
        if not stripped_line:
            continue
        # 分步骤标题行不算有效内容
        if stripped_line.startswith("###") and any(keyword in stripped_line for keyword in workflow_title_keywords):
            continue
        # 无依据提示本身不算有效内容；Agent 可能会在后面追加“请补充...”类引导，也仍然不是可导出的正式结果
        if stripped_line.startswith(NO_RAG_EVIDENCE_MESSAGE):
            continue
        # Agent 的判断过程说明不是最终答案内容，判断无依据结果时需要忽略
        if stripped_line.startswith(("判断结果：", "判断依据：", "检索方式：")):
            continue

        meaningful_lines.append(stripped_line)

    return not meaningful_lines


# 当用户只上传文件、未输入 query 时的默认提示词
DEFAULT_FILE_MODE_PROMPTS = {
    "内容分析": "请基于上传文档完成内容分析，提炼主题、关键信息和结论。",
    "工作流优化": "请基于上传文档进行工作流优化，分步骤总结、分析并提出建议。",
    "企业知识库问答": "请基于上传文档回答企业知识库问题。"
}

# chat_input 允许附加的文件类型
CHAT_INPUT_FILE_TYPES = ["txt", "md", "pdf"]


# -----------------------------
# 支持文件上传分析的模式
# 多版本生成暂不启用文件上传
# -----------------------------
UPLOAD_ENABLED_MODES = {
    "内容分析",
    "结构优化",
    "风格改写",
    "工作流优化",
    "企业知识库问答"
}


# 当前模式对应的会话状态：当前模式完整会话对象、session_id、消息列表
current_session = st.session_state.mode_sessions[mode]
current_session_id = current_session["session_id"]
current_messages = current_session["messages"]


# -----------------------------
# RAG 状态准备
# 说明：
# - 这里先只准备状态，不渲染控件
# - RAG 控件会放到左侧边栏的“对话设置”区域里
# - use_rag: 当前 session 有数据库文档时默认启用；企业知识库问答模式默认允许 RAG；其他模式无文档时默认关闭
# - rag_top_k: 默认检索3个片段
# - rag_status_info: 后端 /rag_status 返回的当前 session 文档状态，后面用于展示文件名和判断是否有可检索文档
# - has_persisted_rag_document: 当前 session 是否已经在数据库中保存过 RAG 文档和 chunk
# - rag_checkbox_key: 当前模式 + 当前 session 的 RAG 开关组件 key，用于让不同模式、不同会话的勾选状态互不影响
# - rag_default_applied_key: 标记当前 session 是否已经自动应用过一次 RAG 默认开启逻辑，避免每次页面重跑都覆盖用户手动选择
# -----------------------------
use_rag = False
rag_top_k = 3
rag_status_info = {}
has_persisted_rag_document = False
rag_checkbox_key = f"use_rag_{mode}_{current_session_id}"
rag_default_applied_key = f"{rag_checkbox_key}_default_applied"


# 只有当前模式支持 RAG 时，才查询数据库文档状态
if mode in RAG_ENABLED_MODES:
    # 先查询当前 session 是否已经有数据库持久化的 RAG 文档
    rag_status_info = get_rag_status(current_session_id)
    # 根据后端状态判断当前 session 是否有可检索文档
    has_persisted_rag_document = bool(rag_status_info.get("has_document"))

    # 如果数据库已经有文档，并且还没有给当前 session 应用过默认值，则自动开启一次 RAG
    if has_persisted_rag_document and not st.session_state.get(rag_default_applied_key):
        st.session_state[rag_checkbox_key] = True
        st.session_state[rag_default_applied_key] = True

    # 如果数据库没有文档，并且当前 session 的 RAG 勾选状态还没初始化，则按模式决定默认值：
    # - 企业知识库问答默认允许 RAG，让用户上传文件后自然进入知识库问答链路
    # - 其他模式默认关闭，避免普通内容处理入口误触发 RAG
    if not has_persisted_rag_document and rag_checkbox_key not in st.session_state:
        st.session_state[rag_checkbox_key] = mode == AGENT_MODE_NAME


# -----------------------------
# 侧边栏对话设置
# -----------------------------
with st.sidebar.expander("对话设置", expanded=True):
    # 只有支持 RAG 的模式才展示 RAG 开关
    if mode in RAG_ENABLED_MODES:
        # 渲染 RAG 开关；默认值由当前 session 是否已有数据库文档决定
        use_rag = st.checkbox(
            "启用文档检索增强（RAG）",
            value=has_persisted_rag_document,
            key=rag_checkbox_key
        )

        # 只有启用 RAG 后，才展示检索片段数量设置
        if use_rag:
            # 渲染滑块，让用户控制本次最多检索多少个文档片段
            rag_top_k = st.slider(
                "检索片段数量",
                min_value=1,
                max_value=5,
                value=3,
                key=f"rag_top_k_{mode}"
            )
    else:
        # 不支持 RAG 的模式不展示开关，避免用户误以为该模式可以检索文档
        st.info("当前模式暂不支持文档检索增强（RAG）。")

# -----------------------------
# 展示当前模式的历史消息
# assistant 消息支持 Markdown，便于工作流分段展示
# 并为 assistant 消息补充：
# - 复制当前结果
# - 导出 Markdown
# 但如果 assistant 只是“知识库中没有找到依据”，则隐藏操作按钮
# -----------------------------
for idx, message in enumerate(current_messages):
    # 每条消息都按它的 role 渲染成聊天气泡，并把内容以 Markdown 方式展示
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

        # 只有 assistant 消息才需要渲染结果操作按钮。因为用户消息通常不需要导出，assistant 消息才是生成结果
        if message["role"] == "assistant":
            # 判断当前历史结果是否只是 RAG 无依据提示；如果是，则不展示复制 / 导出按钮
            can_show_result_actions = not is_no_rag_evidence_result(message["content"])

            # 读取当前 assistant 历史消息保存的 RAG 引用片段
            message_rag_chunks = message.get("rag_preview_chunks", [])
            # 读取当前 assistant 历史消息保存的 RAG 文档状态
            message_rag_status = message.get("rag_status_info", {})

            # 如果这条历史回答保存过引用片段，则折叠展示，避免旧消息占用太多页面空间
            if can_show_result_actions and message_rag_chunks:
                render_rag_preview(
                    chunks=message_rag_chunks,
                    status=message_rag_status,
                    expanded=False
                )

            if can_show_result_actions:
                # Agent 历史消息展示完整流程，但复制按钮只复制“生成回答”这一步的正文
                result_copy_text = build_result_copy_text(
                    result_text=message["content"],
                    workflow_blocks=message.get("workflow_blocks"),
                    preferred_step_name="generate_answer" if mode == AGENT_MODE_NAME else None,
                )

                # 为历史 assistant 消息渲染整体结果操作区：复制整段结果 + 导出 Markdown
                render_result_actions(
                    result_text=result_copy_text,
                    mode_name=mode,
                    widget_key_suffix=f"history_{idx}"
                )

            # 如果是 workflow 历史消息，并且保留了分步结构，则支持分步复制；无依据结果不展示分步复制
            if can_show_result_actions and mode == "工作流优化" and message.get("workflow_blocks"):
                render_workflow_step_copy_actions(
                    workflow_blocks=message["workflow_blocks"],
                    widget_key_suffix=f"history_steps_{idx}"
                )

# -----------------------------
# 统一输入入口
# 使用 st.chat_input 同时支持:
# 1. 纯文本输入
# 2. 文本 + 文件附件
# 3. 仅上传文件
# -----------------------------
chat_submission = st.chat_input(
    "请输入内容，或直接附加文件后发送...",
    accept_file=(mode in UPLOAD_ENABLED_MODES), # 当前模式支持文件上传时，才允许附加文件
    file_type=CHAT_INPUT_FILE_TYPES if mode in UPLOAD_ENABLED_MODES else None, # 如果支持上传文件，只允许 txt / md / pdf
    key=f"chat_input_{mode}" # 不同模式使用不同 key，避免输入框状态冲突
)

submit_display_text = None   # 显示给前端聊天区看的文本
submit_raw_text = None       # 真正发给后端处理的文本
uploaded_file_name = None    # 上传文件名
uploaded_file_text = None    # 提取出来的文件全文文本
uploaded_file = None         # 当前提交附带的文件对象
user_text = ""               # 当前提交中的用户文本
uploaded_files = []          # 当前提交中的文件列表

# 先解析提交对象，提前知道本轮是否附加了文件，后面的 RAG 空状态提示需要依赖这个结果
if chat_submission:
    # accept_file=True 时，chat_input 返回 dict-like 对象，包含 text 和 files
    if mode in UPLOAD_ENABLED_MODES:
        user_text = (chat_submission.text or "").strip()
        uploaded_files = chat_submission["files"]
    else:
        user_text = str(chat_submission).strip()
        uploaded_files = []

    # 当前阶段只处理单文件，因此只取第一个文件
    uploaded_file = uploaded_files[0] if uploaded_files else None

# -----------------------------
# RAG 文档状态提示
# 放在侧边栏设置之后，保证提示读取的是最新 RAG 开关状态
# -----------------------------
if mode in RAG_ENABLED_MODES:
    # 当前数据库已保存文档时，在对话输入前展示更醒目的状态提示
    if has_persisted_rag_document:
        # 从 /rag_status 返回结果中读取当前会话已保存的文档名
        file_name = rag_status_info.get("file_name") or "未命名文件"
        # 从 /rag_status 返回结果中读取当前文档已切分出的文本块数量
        chunk_count = rag_status_info.get("chunk_count", 0)
        # chunk 数量用于告诉用户文档已经完成可检索切块；异常结构时不展示数量，避免提示误导
        chunk_count_text = f"，共 {chunk_count} 个文本块" if isinstance(chunk_count, int) and chunk_count > 0 else ""

        # 如果 RAG 已开启，用成功提示强调后续问题会基于该文档检索
        if use_rag:
            st.success(f"RAG 已开启，当前会话将基于已保存文档进行检索：{file_name}{chunk_count_text}")
        else:
            # 如果数据库有文档但用户手动关闭 RAG，则提示文档仍在，但本次不会用于检索
            st.info(f"当前会话已保存文档：{file_name}{chunk_count_text}。在左侧对话设置中开启 RAG 后，可以继续基于该文档提问。")

    # 当前没有数据库文档时，不在主界面常驻提示。
    # 原因：用户在 chat_input 中选中文件但尚未发送时，Streamlit 还不会把文件交给脚本；
    # 如果此时常驻展示“暂无可检索文档”，会和用户已经选择文件的视觉状态冲突。
    # 真正提交且缺少文档的场景，会在提交校验或 Agent 无依据回答中处理。

# 只有当用户真的提交了输入，才进入后续处理
if chat_submission:

    # -----------------------------
    # 第一步：如果附加了文件，先提取文本
    # -----------------------------
    if uploaded_file is not None:
        uploaded_file_name = uploaded_file.name
        uploaded_file_text, uploaded_file_error = extract_text_from_uploaded_file(uploaded_file)

        if uploaded_file_error:
            st.error(uploaded_file_error)
            # 立刻停止本次 Streamlit 脚本的继续执行
            st.stop()

    # 如果用户既没输入文字，也没附加文件，则停止本次执行
    if not user_text and uploaded_file is None:
        st.stop()

    # 将当前模式名映射成后端任务类型，后续用于选择接口和判断是否属于分步骤输出
    task_type = MODE_TO_TASK_TYPE[mode]
    # workflow 和 Agent 都会返回 step_start / step_complete，需要按分步骤结构渲染
    is_stepwise = task_type in {"workflow", "agent"}

    # 企业知识库问答模式里，只要本轮上传了文件，就自动按 RAG 流程处理。
    # 这样可以避免“上传了知识库文件，但忘记勾选 RAG，最终按普通文件输入回答”的误导性结果。
    if uploaded_file_text and mode == AGENT_MODE_NAME and mode in RAG_ENABLED_MODES and not use_rag:
        use_rag = True
        st.info("检测到企业知识库问答模式已上传文件，本轮已自动启用 RAG 检索。")

    # -----------------------------
    # 第三步：构造前端展示文本和后端实际提交文本
    # -----------------------------
    if uploaded_file_text:
        # 展示给聊天区看的文本：通常是“用户输入 + 附件名”
        submit_display_text = build_user_display_text(
            user_text=user_text,
            uploaded_file_name=uploaded_file_name
        )

        # 启用 RAG 时，提交给后端的是 query，不直接塞全文
        if use_rag and mode in RAG_ENABLED_MODES:
            submit_raw_text = user_text or DEFAULT_FILE_MODE_PROMPTS[mode]
        else:
            # 不启用 RAG 时，把全文和用户要求一起拼成后端输入
            submit_raw_text = build_non_rag_input_text(
                user_text=user_text,
                uploaded_file_text=uploaded_file_text
            )
    else:
        # 没有文件时，展示文本和提交文本就是用户输入本身
        submit_display_text = user_text
        submit_raw_text = user_text

    # 如果开启了 RAG，但既没有上传新文件，数据库里也没有历史文档，则停止本次提交。
    # 这样可以避免用户以为本轮会检索知识库，实际却没有任何可检索文档。
    # 如果用户只是想普通对话，可以关闭 RAG 后再发送；如果需要知识库问答，应先上传文件。
    if (
        use_rag
        and mode in RAG_ENABLED_MODES
        and not uploaded_file_text
        and not has_persisted_rag_document
    ):
        st.warning("当前会话没有可检索文档。请先上传文件，或关闭 RAG 后直接提问。")
        st.stop()

    # -----------------------------
    # 第四步：如果当前附加了文件并启用 RAG, 则判断是否需要重新索引
    # -----------------------------
    if uploaded_file_text and use_rag and mode in RAG_ENABLED_MODES:
        # 给当前文件文本生成一个指纹，用来判断”这份文档是不是和之前同一份“
        text_fingerprint = build_text_fingerprint(uploaded_file_text)
        # 取出当前模式已有的索引状态记录
        current_index_state = st.session_state.rag_index_state.get(mode)

        # 判断是否需要重新索引。只要满足以下任意条件，就重新索引：
        # - 当前模式还没有索引记录
        # - 当前记录不属于这个会话
        # - 当前文件文本和之前索引过的不一样
        need_reindex = (
            not current_index_state
            or current_index_state.get("session_id") != current_session_id
            or current_index_state.get("text_fingerprint") != text_fingerprint
        )

        if need_reindex:
            # 文档解析、chunk 持久化和向量化可能需要一段时间，用状态提示避免页面长时间空白
            with st.status("正在建立知识库索引...", expanded=True) as index_status:
                # 提示用户当前阶段是在处理文档索引，而不是前端卡死
                st.write("正在解析文档并切分为可检索文本块。")
                # 提示用户向量模式下会调用当前配置的 embedding 服务并写入向量库
                st.write("正在调用已配置的 Embedding 服务生成向量，并写入本地向量库。")
                # 调用后端 /index_document 接口，让后端为当前会话建立文档索引
                success, message = index_uploaded_document(
                    session_id=current_session_id,
                    file_name=uploaded_file_name,
                    document_text=uploaded_file_text
                )

            # 如果索引失败，显示错误并停止
            if not success:
                # 将状态标记为失败，便于用户确认不是界面静默卡住
                index_status.update(label="文档向量索引失败", state="error")
                st.error(message)
                st.stop()

            # 将状态标记为完成，明确告诉用户索引阶段已经结束
            index_status.update(label=message, state="complete")

            # 记录当前模式当前会话已索引过这份文档
            st.session_state.rag_index_state[mode] = {
                "session_id": current_session_id,
                "file_name": uploaded_file_name,
                "text_fingerprint": text_fingerprint
            }
            # 索引成功后更新本次运行中的数据库文档状态
            has_persisted_rag_document = True
            # 索引成功后更新本次运行中的状态信息，便于后续预览展示
            rag_status_info = get_rag_status(current_session_id)

    # 默认没有 RAG 命中片段；只有启用 RAG 时才会调用后端获取
    rag_preview_chunks = []

    # 如果启用了 RAG，则先获取本次 query 命中的片段，稍后放到模型答案下方展示。
    # 企业知识库问答模式由后端 Agent 自己先判断是否需要知识库，因此这里不提前调用 /rag_preview，避免“还没判断就先检索”。
    if use_rag and mode in RAG_ENABLED_MODES and task_type != "agent":
        # 本地向量检索需要先为 query 生成 embedding，用 spinner 避免检索阶段出现空白等待
        with st.spinner("正在检索知识库并匹配引用片段..."):
            # 调用后端 /rag_preview 接口，获取本次 query 命中的片段摘要
            rag_preview_chunks = get_rag_preview(
                session_id=current_session_id,
                query=submit_raw_text,
                top_k=rag_top_k
            )
        # 调用后端 /rag_status/{session_id}，获取当前会话索引状态
        rag_status_info = rag_status_info or get_rag_status(current_session_id)

    # 组装后端扩展参数。display_text 用于数据库保存前端展示文本，RAG 元数据用于保存每条回答自己的引用模块
    user_options = {
        # display_text 是前端聊天区展示文本；
        # 上传文件场景下，它只展示用户问题和附件名，避免把完整文档全文保存成可见聊天记录。
        # 后端会优先用 display_text 保存 message.content，同时用 input_text 保存 raw_content。
        "display_text": submit_display_text
    }

    # 只有本轮确实有命中片段时，才把引用模块元数据交给后端保存
    if rag_preview_chunks:
        # 保存本轮命中的片段列表，刷新页面后这条回答仍能展示自己的引用来源
        user_options["rag_preview_chunks"] = rag_preview_chunks
        # 保存本轮文档状态，用于历史引用模块显示文档名等信息
        user_options["rag_status_info"] = rag_status_info

    # -----------------------------
    # 第五步: 展示并写入用户消息
    # -----------------------------
    with st.chat_message("user"):
        st.write(submit_display_text)

    # 将这条用户消息追加到当前模式消息列表中
    current_messages.append({
        "role": "user",
        "content": submit_display_text, # 前端显示用
        "raw_content": submit_raw_text  # 后端处理用
    })

    # -----------------------------
    # 第七步: 构造符合 ChatRequest 的请求体并发送
    # -----------------------------
    payload = {
        "session_id": current_session_id,
        "task_type": task_type,
        "input_text": submit_raw_text,
        "persona": mode,
        # current_messages[:-1] 表示不把刚刚追加的当前用户输入再算进 history。因为当前这条消息已经单独放进 input_text 里了，不应该重复出现在历史中
        "history": build_history_for_api(current_messages[:-1]),
        "user_options": user_options,
        "use_rag": use_rag,
        "rag_top_k": rag_top_k
    }

    # 发送流式请求。这里可能会等待后端建立 SSE 连接，因此给出明确提示，避免页面像是没有响应。
    with st.spinner("正在连接后端并生成回答..."):
        response = post_stream_request(payload, task_type)

    # 请求失败直接报错
    if response.status_code != 200:
        st.error(f"请求失败: {response.text}")
    else:
        # 如果请求成功，则开始在 assistant 聊天气泡里渲染流式结果
        with st.chat_message("assistant"):
            # 先创建一个可动态更新的占位区域，并显示“思考中...”。后面随着 SSE 数据到来，这块区域会被不断更新
            placeholder = st.empty()
            placeholder.markdown("思考中... 🤔")

            # 普通聊天模式的完整文本累计区
            full_response = ""

            # workflow / Agent 模式下的分步骤文本累计区
            workflow_blocks: dict[str, str] = {}

            # Agent final 事件可能携带后端实际检索到的引用模块元数据，用于本轮回答即时展示
            response_metadata = {}

            # 标记是否收到第一条有效事件，用来清理“思考中”
            first_event_received = False

            # 逐条读取并解析后端返回的 SSE 事件
            for event in iter_sse_events(response):
                event_type = event.get("event_type")
                step_name = event.get("step_name")
                content = event.get("content", "")
                event_metadata = event.get("metadata") or {}
                error_message = event.get("error_message")

                # 第一次真正收到事件时，清除“思考中...”占位提示
                if not first_event_received:
                    placeholder.empty()
                    first_event_received = True

                # 工作流开始 / 步骤开始事件
                if event_type in {"workflow_start", "step_start"}:
                    # 如果当前模式确实是分步骤输出，并且事件带了步骤名，就把当前已积累的内容重新渲染一次，这样可以让前端在步骤切换时保持结构化展示
                    if is_stepwise and step_name:
                        current_markdown = format_workflow_blocks(workflow_blocks)
                        if current_markdown:
                            placeholder.markdown(current_markdown)

                # 增量事件：按模式分别处理
                elif event_type == "delta":
                    if is_stepwise:
                        if step_name:
                            # 如果该步骤还没有内容，先初始化为空字符串。如：{}会变成{"summary": ""}
                            workflow_blocks.setdefault(step_name, "")
                            # 把本次增量文本拼接到对应步骤后面
                            workflow_blocks[step_name] += content

                        # 将当前 workflow 结果格式化成 Markdown，再加一个“▌”光标，模拟流式输出效果
                        placeholder.markdown(format_workflow_blocks(workflow_blocks) + "\n\n▌")
                    else:
                        # 将增量文本拼接到 full_response
                        full_response += content
                        # 更新页面展示
                        placeholder.markdown(full_response + "▌")
                        # 让流式输出的视觉节奏更自然一点，不至于瞬间刷完
                        time.sleep(0.01)

                # 步骤完成事件：用于工作流模式的最终分步内容落盘
                elif event_type == "step_complete":
                    # 只有确定这条事件确实属于某个步骤，才去写入对应步骤的数据。避免出现：workflow_blocks[None]
                    if step_name:
                        # 以步骤完成事件里的完整结果为准
                        workflow_blocks[step_name] = content
                        placeholder.markdown(format_workflow_blocks(workflow_blocks))

                # 最终完成事件
                elif event_type == "final":
                    # final 事件可能携带当前回答对应的引用元数据，先保存下来，后面统一渲染引用模块
                    if isinstance(event_metadata, dict) and event_metadata:
                        response_metadata = event_metadata

                    if is_stepwise:
                        # 分步骤模式下，最终渲染一遍已积累好的步骤结果
                        placeholder.markdown(format_workflow_blocks(workflow_blocks))
                    else:
                        # 如果前面没有累计到内容，但 final 给了完整文本，则用 final 兜底
                        if not full_response and content:
                            full_response = content
                        placeholder.markdown(full_response)

                # 错误事件
                elif event_type == "error":
                    st.error(error_message or "请求失败")
                    break

            # 生成最终写入聊天记录的 assistant 内容。区分模式：
            # - workflow / Agent -> 最终结果来自格式化后的 workflow_blocks
            # - 普通聊天 -> 最终结果就是 full_response
            if is_stepwise:
                final_display_text = format_workflow_blocks(workflow_blocks)
            else:
                final_display_text = full_response

            # 当前轮结果生成后，渲染操作区并写入历史
            if final_display_text.strip():
                # Agent 模式的引用块由后端实际检索结果随 final 事件返回；普通 RAG 模式仍沿用前端预览结果
                if response_metadata:
                    metadata_chunks = response_metadata.get("rag_preview_chunks")
                    metadata_status = response_metadata.get("rag_status_info")
                    if isinstance(metadata_chunks, list):
                        rag_preview_chunks = [
                            chunk
                            for chunk in metadata_chunks
                            if isinstance(chunk, dict)
                        ]
                    if isinstance(metadata_status, dict):
                        rag_status_info = metadata_status

                # 当前结果如果只是 RAG 无依据提示，则不展示复制 / 导出等结果操作
                can_show_result_actions = not is_no_rag_evidence_result(final_display_text)

                # 如果本轮启用了 RAG 且结果不是无依据提示，则展示引用来源和命中的原文片段
                if use_rag and mode in RAG_ENABLED_MODES and can_show_result_actions and rag_preview_chunks:
                    render_rag_preview(
                        chunks=rag_preview_chunks,
                        status=rag_status_info,
                        expanded=True
                    )

                if can_show_result_actions:
                    # Agent 模式页面展示完整流程，但复制按钮只复制“生成回答”这一步，便于用户直接拿走答案
                    result_copy_text = build_result_copy_text(
                        result_text=final_display_text,
                        workflow_blocks=workflow_blocks if is_stepwise else None,
                        preferred_step_name="generate_answer" if task_type == "agent" else None,
                    )

                    render_result_actions(
                        result_text=result_copy_text,
                        mode_name=mode,
                        widget_key_suffix="latest_result"
                    )

                if can_show_result_actions and task_type == "workflow" and workflow_blocks:
                    # 插入一个很小的竖向空白间距。unsafe_allow_html=True表示：允许 Streamlit 按 HTML 来渲染这段字符串
                    st.markdown("<div style='height: 0.25rem;'></div>", unsafe_allow_html=True)

                    # workflow 模式下额外支持分步复制
                    render_workflow_step_copy_actions(
                        workflow_blocks=workflow_blocks,
                        widget_key_suffix="latest_steps"
                    )

                # 组装 assistant 消息
                assistant_message = {
                    "role": "assistant",
                    "content": final_display_text
                }

                # 分步骤模式下，把分步结构一起保存，便于历史消息刷新后继续按步骤展示
                if is_stepwise and workflow_blocks:
                    # 为什么用.copy()？因为 workflow_blocks 是一个字典，可变。.copy() 是复制一份，避免后面原字典变化时，把历史消息里的结果也带着改掉。
                    assistant_message["workflow_blocks"] = workflow_blocks.copy()

                # 如果当前回答展示了 RAG 引用模块，则把引用数据挂到这条 assistant 消息上
                if use_rag and mode in RAG_ENABLED_MODES and can_show_result_actions and rag_preview_chunks:
                    # 保存本轮命中的引用片段，后续页面重跑或刷新时可以恢复到对应回答下面
                    assistant_message["rag_preview_chunks"] = rag_preview_chunks.copy()
                    # 保存本轮文档状态，作为引用模块的文档名兜底信息
                    assistant_message["rag_status_info"] = rag_status_info.copy()

                # 将 assistant 消息追加到当前模式的历史消息列表里
                current_messages.append(assistant_message)
                # 后端已经完成本轮消息落库后，刷新页面让侧边栏重新读取最新会话列表
                st.rerun()
