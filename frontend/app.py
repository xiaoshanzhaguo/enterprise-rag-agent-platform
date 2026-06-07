"""
前端主页面模块（app.py）。

职责：
1. 负责初始化 Streamlit 页面，并组织整个 AI 内容分析与创作助手的前端交互流程
2. 管理多模式会话状态，包括不同模式下的 session_id、历史消息、数据库历史恢复与旧 JSON 历史兜底
3. 统一处理用户输入，包括纯文本输入、文件上传输入以及启用 RAG 时的文档索引逻辑
4. 调用后端聊天接口、工作流接口、RAG 接口和会话管理接口，并解析流式 SSE 响应
5. 渲染历史消息、当前结果、RAG 预览、结果复制、Markdown 导出和 workflow 分步复制等前端展示能力
6. 支持新建当前模式聊天、清空当前模式聊天，并同步清理后端数据库会话和内存 RAG 索引

说明：
- 当前模块属于前端入口层，负责把页面状态管理、用户输入处理、后端请求发送和结果展示串起来
- 页面基于 Streamlit 构建，后端依赖 FastAPI 提供聊天流式接口、工作流接口、RAG 接口和 SQLite 会话持久化接口
- 当前实现支持“多模式 + 会话隔离 + 文件上传分析 + 第一阶段 RAG + SQLite 历史持久化 + 旧 JSON 历史兜底”的完整交互链路
"""

# 导入时间模块，使得后面流式输出时用 time.sleep(0.01) 让文本增长更自然
import sys
import time
from pathlib import Path
# 导入 uuid4()，生成新的唯一会话 ID，每次新建聊天或清空当前模式聊天时都会生成新的 session_id
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
    clear_indexed_document, # 通知后端删除某个会话的文档索引
    create_chat_session, # 在后端数据库中创建空聊天会话
    index_uploaded_document, # 通知后端给文档建立索引
    iter_sse_events, # 逐条解析后端返回的 SSE 事件流
    get_rag_preview, # 获取本次 query 命中的 RAG 片段摘要
    get_rag_status, # 获取当前会话的 RAG 状态
    load_chat_history, # 从后端数据库恢复聊天历史
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
    page_title="AI 内容分析与创作助手", # 浏览器标签页标题
    page_icon="🤖", # 页面图标
    layout="wide", # 宽屏布局
    initial_sidebar_state="expanded", # 默认展开侧边栏
    menu_items={} # 隐藏默认菜单项
)

# 页面顶部渲染大标题
st.title("AI 内容分析与创作助手")


# -----------------------------
# 模式映射
# 前端展示名称 -> 后端 task_type
# persona 先沿用展示名称，便于后端按人设/风格扩展
# -----------------------------
MODE_TO_TASK_TYPE = {
    "内容分析": "summary",
    "结构优化": "rewrite",
    "风格改写": "rewrite",
    "多版本生成": "chat",
    "工作流优化": "workflow"
}

# 每个模式的简短说明文案。当前模式切换后，给用户一个简短提示，帮助理解这个模式是干什么的
MODE_DESCRIPTIONS = {
    "内容分析": "提炼主题、关键信息和结论",
    "结构优化": "整理表达层次和逻辑结构",
    "风格改写": "保持原意，调整表达语气",
    "多版本生成": "生成不同场景可直接使用的版本",
    "工作流优化": "分步骤总结、分析并提出建议"
}

# 当前前端支持的所有模式列表
AVAILABLE_MODES = list(MODE_TO_TASK_TYPE.keys())

# 在侧边栏中让用户选择当前模式
mode = st.sidebar.selectbox("选择功能", AVAILABLE_MODES)

# 显示当前模式说明
st.caption(f"当前模式：{MODE_DESCRIPTIONS[mode]}")

# -----------------------------
# 第一阶段启用 RAG 的模式
# 先只支持：内容分析、工作流优化
# -----------------------------
RAG_ENABLED_MODES = {
    "内容分析",
    "工作流优化"
}

# 当用户只上传文件、未输入 query 时的默认提示词
DEFAULT_FILE_MODE_PROMPTS = {
    "内容分析": "请基于上传文档完成内容分析，提炼主题、关键信息和结论。",
    "工作流优化": "请基于上传文档进行工作流优化，分步骤总结、分析并提出建议。"
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
    "工作流优化"
}


# -----------------------------
# Session State 初始化
# 如果不存在，或被清空为 {}，则重新初始化
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
# 初始化前端索引状态缓存
# 用于记录当前模式下，当前 session 的文档是否已经索引过，避免每次发请求都重新索引
# -----------------------------
if "rag_index_state" not in st.session_state:
    st.session_state.rag_index_state = {}

# 当前模式对应的会话状态：当前模式完整会话对象、session_id、消息列表
current_session = st.session_state.mode_sessions[mode]
current_session_id = current_session["session_id"]
current_messages = current_session["messages"]


# -----------------------------
# RAG 控件区
# 说明：
# - 控件要放在历史消息前面，否则 Streamlit 重跑后会被历史输出挤到页面下方
# - 即使当前没附加文件，也先给默认值，保证后续 payload 安全
# - use_rag: 默认不启用
# - rag_top_k: 默认检索3个片段
# -----------------------------
use_rag = False
rag_top_k = 3


# 只有当前模式支持 RAG 时，才展示 RAG 控件
if mode in RAG_ENABLED_MODES:
    # 渲染一个复选框，默认勾选，key 按模式区分，避免不同模式冲突
    use_rag = st.checkbox(
        "启用文档检索增强（RAG）",
        value=True,
        key=f"use_rag_{mode}"
    )

    # 只有真的勾选了 RAG，才继续展示下面的检索数量滑块
    if use_rag:
        # 渲染一个滑块，让用户选择检索片段数量
        rag_top_k = st.slider(
            "检索片段数量",
            min_value=1,
            max_value=5,
            value=3,
            key=f"rag_top_k_{mode}"
        )

        # 给用户一个小提示，解释 RAG 的处理逻辑
        st.caption("在附加文档时，系统会先检索相关片段，再交给模型处理。")

        # 取出当前模式对应的索引记录
        current_index_state = st.session_state.rag_index_state.get(mode)
        # 如果当前模式当前会话已经索引过一份文档，就显示提示。这有助于用户知道现在检索增强依赖的是哪份文件
        if current_index_state and current_index_state.get("session_id") == current_session_id:
            file_name = current_index_state.get("file_name", "未命名文件")
            st.caption(f"当前会话已索引文档：{file_name}")


# -----------------------------
# 展示当前模式的历史消息
# assistant 消息支持 Markdown，便于工作流分段展示
# 并为 assistant 消息补充：
# - 复制当前结果
# - 导出 Markdown
# -----------------------------
for idx, message in enumerate(current_messages):
    # 每条消息都按它的 role 渲染成聊天气泡，并把内容以 Markdown 方式展示
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

        # 只有 assistant 消息才需要渲染结果操作按钮。因为用户消息通常不需要导出，assistant 消息才是生成结果
        if message["role"] == "assistant":
            # 为历史 assistant 消息渲染整体结果操作区：复制整段结果 + 导出 Markdown
            render_result_actions(
                result_text=message["content"],
                mode_name=mode,
                widget_key_suffix=f"history_{idx}"
            )

            # 如果是 workflow 历史消息，并且保留了分步结构，则支持分步复制
            if mode == "工作流优化" and message.get("workflow_blocks"):
                render_workflow_step_copy_actions(
                    workflow_blocks=message["workflow_blocks"],
                    widget_key_suffix=f"history_steps_{idx}"
                )


# -----------------------------
# 会话控制按钮
# -----------------------------
if st.sidebar.button("新建当前模式聊天"):
    # 取出旧会话 ID，用于同步清理旧会话相关资源
    old_session_id = st.session_state.mode_sessions[mode]["session_id"]

    # 清理旧会话在内存 RAG store 中的临时文档索引
    clear_indexed_document(old_session_id)

    # 删除旧会话在 SQLite 中的聊天记录、文档记录和 RAG 关联记录
    clear_chat_session(old_session_id)

    # 生成新的会话 ID，并提前通知后端创建空会话记录
    new_session_id = str(uuid4())
    create_chat_session(new_session_id, mode)

    # 重置当前模式会话：重新生成 session_id，并清空消息
    st.session_state.mode_sessions[mode] = {
        "session_id": new_session_id,
        "messages": []
    }

    # 同步清理当前模式的前端索引状态缓存
    st.session_state.rag_index_state.pop(mode, None)

    # 强制页面重新执行，让新会话立即生效
    st.rerun()

if st.sidebar.button("清空当前模式聊天"):
    # 清理当前模式旧 session 对应的内存索引和数据库会话数据，只影响当前模式，不影响其他模式的历史
    old_session_id = st.session_state.mode_sessions[mode]["session_id"]
    clear_indexed_document(old_session_id)
    clear_chat_session(old_session_id)

    # 创建一个新的空 session，作为当前模式后续对话的会话容器
    new_session_id = str(uuid4())
    create_chat_session(new_session_id, mode)

    # 只重置当前模式会话
    st.session_state.mode_sessions[mode] = {
        "session_id": new_session_id,
        "messages": []
    }

    # 只清空当前模式的前端索引状态缓存
    st.session_state.rag_index_state.pop(mode, None)

    st.rerun()

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

# 只有当用户真的提交了输入，才进入后续处理
if chat_submission:
    # -----------------------------
    # 第一步：统一解析 chat_input 返回值
    # accept_file=True 时，chat_input 返回 dict-like 对象，包含 text 和 files
    # 非上传模式下，返回的是普通字符串
    # -----------------------------
    if mode in UPLOAD_ENABLED_MODES:
        user_text = (chat_submission.text or "").strip()
        uploaded_files = chat_submission["files"]
    else:
        user_text = str(chat_submission).strip()
        uploaded_files = []

    # 当前阶段只处理单文件，因此只取第一个文件
    uploaded_file = uploaded_files[0] if uploaded_files else None

    # -----------------------------
    # 第二步：如果附加了文件，先提取文本
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
            # 调用后端 /index_document 接口，让后端为当前会话建立文档索引
            success, message = index_uploaded_document(
                session_id=current_session_id,
                file_name=uploaded_file_name,
                document_text=uploaded_file_text
            )

            # 如果索引失败，显示错误并停止
            if not success:
                st.error(message)
                st.stop()

            # 索引成功则显示提示
            st.success(message)

            # 记录当前模式当前会话已索引过这份文档
            st.session_state.rag_index_state[mode] = {
                "session_id": current_session_id,
                "file_name": uploaded_file_name,
                "text_fingerprint": text_fingerprint
            }

    # 如果启用了 RAG，则获取并展示本次 query 命中的片段预览
    if use_rag and mode in RAG_ENABLED_MODES:
        # 调用后端 /rag_preview 接口，获取本次 query 命中的片段摘要
        rag_preview_chunks = get_rag_preview(
            session_id=current_session_id,
            query=submit_raw_text,
            top_k=rag_top_k
        )
        # 调用后端 /rag_status/{session_id}，获取当前会话索引状态
        rag_status_info = get_rag_status(current_session_id)
        # 将检索片段和状态信息渲染到前端
        render_rag_preview(rag_preview_chunks, rag_status_info)

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
    # 第六步: 根据模式决定调用哪个接口
    # -----------------------------
    # 将当前模式名映射成后端任务类型
    task_type = MODE_TO_TASK_TYPE[mode]
    # 判断当前是否属于 workflow 模式
    is_workflow = task_type == "workflow"

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
        "user_options": {
            # display_text 是前端聊天区展示文本；
            # 上传文件场景下，它只展示用户问题和附件名，避免把完整文档全文保存成可见聊天记录。
            # 后端会优先用 display_text 保存 message.content，同时用 input_text 保存 raw_content。
            "display_text": submit_display_text
        },
        "use_rag": use_rag,
        "rag_top_k": rag_top_k
    }

    # 发送流式请求
    response = post_stream_request(payload, is_workflow)

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

            # workflow 模式下的分步骤文本累计区
            workflow_blocks: dict[str, str] = {}

            # 标记是否收到第一条有效事件，用来清理“思考中”
            first_event_received = False

            # 逐条读取并解析后端返回的 SSE 事件
            for event in iter_sse_events(response):
                event_type = event.get("event_type")
                step_name = event.get("step_name")
                content = event.get("content", "")
                error_message = event.get("error_message")

                # 第一次真正收到事件时，清除“思考中...”占位提示
                if not first_event_received:
                    placeholder.empty()
                    first_event_received = True

                # 工作流开始 / 步骤开始事件
                if event_type in {"workflow_start", "step_start"}:
                    # 如果当前模式确实是 workflow，并且事件带了步骤名，就把当前已积累的 workflow 内容重新渲染一次，这样可以让前端在步骤切换时保持结构化展示
                    if is_workflow and step_name:
                        current_markdown = format_workflow_blocks(workflow_blocks)
                        if current_markdown:
                            placeholder.markdown(current_markdown)

                # 增量事件：按模式分别处理
                elif event_type == "delta":
                    if is_workflow:
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
                    if is_workflow:
                        # workflow 模式下，最终渲染一遍已积累好的步骤结果
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
            # - workflow -> 最终结果来自格式化后的 workflow_blocks
            # - 普通聊天 -> 最终结果就是 full_response
            if is_workflow:
                final_display_text = format_workflow_blocks(workflow_blocks)
            else:
                final_display_text = full_response

            # 当前轮结果生成后，渲染操作区并写入历史
            if final_display_text.strip():
                render_result_actions(
                    result_text=final_display_text,
                    mode_name=mode,
                    widget_key_suffix="latest_result"
                )

                if is_workflow and workflow_blocks:
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

                # workflow 模式下，把分步结构一起保存，便于历史消息继续支持“分步复制”
                if is_workflow and workflow_blocks:
                    # 为什么用.copy()？因为 workflow_blocks 是一个字典，可变。.copy() 是复制一份，避免后面原字典变化时，把历史消息里的结果也带着改掉。
                    assistant_message["workflow_blocks"] = workflow_blocks.copy()

                # 将 assistant 消息追加到当前模式的历史消息列表里
                current_messages.append(assistant_message)
