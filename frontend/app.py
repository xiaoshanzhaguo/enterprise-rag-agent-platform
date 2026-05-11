import json
import time
import hashlib
from io import BytesIO
from uuid import uuid4

import requests
import streamlit as st
import streamlit.components.v1 as components


try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None


# -----------------------------
# 页面基础配置
# -----------------------------
st.set_page_config(
    page_title="AI 内容分析与创作助手",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={}
)

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

MODE_DESCRIPTIONS = {
    "内容分析": "提炼主题、关键信息和结论",
    "结构优化": "整理表达层次和逻辑结构",
    "风格改写": "保持原意，调整表达语气",
    "多版本生成": "生成不同场景可直接使用的版本",
    "工作流优化": "分步骤总结、分析并提出建议"
}

AVAILABLE_MODES = list(MODE_TO_TASK_TYPE.keys())
mode = st.sidebar.selectbox("选择功能", AVAILABLE_MODES)
st.caption(f"当前模式：{MODE_DESCRIPTIONS[mode]}")

# -----------------------------
# 第一阶段启用 RAG 的模式
# 先只支持：内容分析、工作流优化
# -----------------------------
RAG_ENABLED_MODES = {
    "内容分析",
    "工作流优化"
}

DEFAULT_FILE_MODE_PROMPTS = {
    "内容分析": "请基于上传文档完成内容分析，提炼主题、关键信息和结论。",
    "工作流优化": "请基于上传文档进行工作流优化，分步骤总结、分析并提出建议。"
}


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
# 工具函数：创建所有模式的会话容器
# 每个模式都维护自己的 session_id 和 messages
# -----------------------------
def create_mode_sessions(mode_names: list[str]) -> dict:
    """
    为所有模式初始化独立会话。

    返回格式：
    {
        "内容分析": {
            "session_id": "...",
            "messages": []
        },
        ...
    }
    """
    return {
        mode_name: {
            "session_id": str(uuid4()),
            "messages": []
        }
        for mode_name in mode_names
    }


# -----------------------------
# Session State 初始化
# 如果不存在，或被清空为 {}，则重新初始化
# -----------------------------
if "mode_sessions" not in st.session_state or not st.session_state.mode_sessions:
    st.session_state.mode_sessions = create_mode_sessions(AVAILABLE_MODES)

# -----------------------------
# 用于记录当前模式下，当前 session 的文档是否已经索引过，避免每次发请求都重新索引。
# -----------------------------
if "rag_index_state" not in st.session_state:
    st.session_state.rag_index_state = {}

# 当前模式对应的会话状态
current_session = st.session_state.mode_sessions[mode]
current_session_id = current_session["session_id"]
current_messages = current_session["messages"]


# -----------------------------
# 工具函数：将前端消息历史转换为后端 schema 需要的 history 格式
# 只保留最近 N 轮，避免上下文过长
# -----------------------------
MAX_HISTORY_LENGTH = 6


def build_history_for_api(messages: list[dict], max_length: int = MAX_HISTORY_LENGTH) -> list[dict]:
    """
    将前端消息列表裁剪并转换为后端可直接接收的 history 结构。

    每条消息保留：
    - role
    - content
    说明:
    - 普通文本输入直接使用 content
    - 文件上传消息优先使用 raw_content, 保证历史上下文仍热是完整文本
    """
    history = []
    recent_messages = messages[-max_length:]

    for message in recent_messages:
        role = message.get("role")
        content = message.get("raw_content", message.get("content", ""))

        if role not in {"user", "assistant", "system"}:
            continue

        history.append({
            "role": role,
            "content": content
        })

    return history


# -----------------------------
# 工具函数：格式化工作流步骤输出
# 将 step_name 转成更友好的中文标题
# -----------------------------
STEP_TITLE_MAP = {
    "summary": "🧠 内容总结",
    "analysis": "🔍 问题分析",
    "suggestion": "✨ 优化建议"
}


def format_workflow_blocks(workflow_blocks: dict[str, str]) -> str:
    """
    将工作流分步骤结果格式化为 Markdown 展示。
    """
    formatted_parts = []

    for step_name in ["summary", "analysis", "suggestion"]:
        content = workflow_blocks.get(step_name, "").strip()
        if not content:
            continue

        title = STEP_TITLE_MAP.get(step_name, step_name)
        formatted_parts.append(f"### {title}\n\n{content}\n")

    return "\n\n".join(formatted_parts)


# -----------------------------
# 工具函数：从上传文件中提取文本
# 支持 txt / md / pdf
# -----------------------------
def extract_text_from_uploaded_file(uploaded_file) -> tuple[str | None, str | None]:
    """
    从上传文件中提取文本。

    返回:
    - (text, None) 表示成功
    - (None, error_message) 表示失败
    """
    file_name = uploaded_file.name.lower()
    file_bytes = uploaded_file.getvalue()

    # 处理 txt / md
    if file_name.endswith(".txt") or file_name.endswith(".md"):
        for encoding in ("utf-8", "utf-8-sig", "gbk"):
            try:
                return file_bytes.decode(encoding), None
            except UnicodeDecodeError:
                continue
        return None, "文件编码无法识别, 请尝试使用 UTF-8 编码保存后再上传。"

    # 处理pdf
    if file_name.endswith(".pdf"):
        if PdfReader is None:
            return None, "当前环境未安装 pypdf, 请先在 requirements.txt 中添加 pypdf 并安装依赖。"

        try:
            reader = PdfReader(BytesIO(file_bytes))
            page_texts = []

            for page in reader.pages:
                text = page.extract_text() or ""
                if text.strip():
                    page_texts.append(text)

            full_text = "\n\n".join(page_texts).strip()

            if not full_text:
                return None, "PDF 中未提取到可用文本。若这是扫描版 PDF, 后续需要 OCR 才能支持。"

            return full_text, None
        except Exception as e:
            return None, f"PDF 解析失败: {str(e)}"

    return None, "暂不支持该文件类型, 请上传 txt、md 或 pdf 文件。"


# -----------------------------
# 工具函数：生成 Markdown 文件名
# mode_name 用于区分不同模式导出的结果
# -----------------------------
def build_markdown_filename(mode_name: str) -> str:
    """
    生成 Markdown 导出文件名。
    """
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    safe_mode_name = mode_name.replace(" ", "_")
    return f"{safe_mode_name}_result_{timestamp}.md"


# -----------------------------
# 工具函数：构造 Markdown 导出内容
# 为导出的文件增加标题和模式信息
# -----------------------------
def build_markdown_content(mode_name: str, result_text: str) -> str:
    """
    将结果包装成更完整的 Markdown 文本，便于导出保存。
    """
    export_time = time.strftime("%Y-%m-%d %H:%M:%S")
    return f"""# AI 内容分析与创作助手导出结果

- 模式：{mode_name}
- 导出时间：{export_time}

---

{result_text}
"""


# -----------------------------
# 工具函数：渲染复制按钮
# 通过内嵌 HTML + JS 将结果复制到系统剪贴板
# -----------------------------
def render_copy_button(text: str, label: str, button_id_suffix: str) -> None:
    """
    渲染一个复制按钮，用于将指定文本复制到剪贴板。

    :param text: 需要复制的文本内容
    :param label: 按钮上显示的文字
    :param button_id_suffix: 用于生成唯一按钮 ID，避免多个按钮冲突
    :return: None
    """
    button_id = f"copy_btn_{button_id_suffix}_{uuid4().hex}"

    components.html(
        f"""
        <html>
        <head>
            <style>
                html, body {{
                    margin: 0;
                    padding: 0;
                    background: transparent;
                    overflow: hidden;
                }}

                .copy-btn {{
                    width: 100%;
                    height: 38px;
                    border: 1px solid #d0d7de;
                    border-radius: 0.5rem;
                    background: white;
                    color: #111827;
                    font-size: 0.95rem;
                    cursor: pointer;
                    box-sizing: border-box;
                }}

                .copy-btn:hover {{
                    background: #f9fafb;
                }}
            </style>
        </head>
        <body>
            <button id="{button_id}" class="copy-btn">{label}</button>

            <script>
                const btn = document.getElementById("{button_id}");
                btn.onclick = async () => {{
                    try {{
                        await navigator.clipboard.writeText({json.dumps(text)});
                        const oldText = btn.innerText;
                        btn.innerText = "已复制";
                        setTimeout(() => btn.innerText = oldText, 1500);
                    }} catch (err) {{
                        const oldText = btn.innerText;
                        btn.innerText = "复制失败";
                        setTimeout(() => btn.innerText = oldText, 1500);
                    }}
                }};
            </script>
        </body>
        </html>
        """,
        height=40,
    )


# -----------------------------
# 工具函数：渲染结果操作区
# 包括：
# 1. 复制当前结果
# 2. 导出 Markdown
# -----------------------------
def render_result_actions(result_text: str, mode_name: str, widget_key_suffix: str) -> None:
    """
    为 assistant 结果渲染操作按钮：
    1. 复制当前结果
    2. 导出 Markdown
    """
    if not result_text.strip():
        return

    markdown_content = build_markdown_content(mode_name, result_text)
    file_name = build_markdown_filename(mode_name)

    col1, col2 = st.columns(2, gap="small")

    with col1:
        render_copy_button(
            text=result_text,
            label="复制当前结果",
            button_id_suffix=widget_key_suffix
        )

    with col2:
        st.download_button(
            label="导出 Markdown",
            data=markdown_content,
            file_name=file_name,
            mime="text/markdown",
            key=f"download_md_{widget_key_suffix}",
            use_container_width=True,
        )


# -----------------------------
# 工具函数：渲染 workflow 结果操作区
# 支持单独复制：内容总结、问题分析、优化建议
# -----------------------------
def render_workflow_step_copy_actions(workflow_blocks: dict[str, str], widget_key_suffix: str) -> None:
    """
    为 workflow 结果渲染“分步复制”按钮。
    默认折叠，避免界面过于拥挤。
    """
    if not workflow_blocks:
        return

    with st.expander("分步复制", expanded=False):
        col1, col2, col3 = st.columns(3, gap="small")

        with col1:
            summary_text = workflow_blocks.get("summary", "").strip()
            if summary_text:
                render_copy_button(
                    text=summary_text,
                    label="复制总结",
                    button_id_suffix=f"{widget_key_suffix}_summary"
                )

        with col2:
            analysis_text = workflow_blocks.get("analysis", "").strip()
            if analysis_text:
                render_copy_button(
                    text=analysis_text,
                    label="复制问题",
                    button_id_suffix=f"{widget_key_suffix}_analysis"
                )

        with col3:
            suggestion_text = workflow_blocks.get("suggestion", "").strip()
            if suggestion_text:
                render_copy_button(
                    text=suggestion_text,
                    label="复制建议",
                    button_id_suffix=f"{widget_key_suffix}_suggestion"
                )


def build_text_fingerprint(text: str) -> str:
    """
    为文档生成一个简单指纹，用于判断是否需要重新索引。
    """
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def index_uploaded_document(session_id: str, file_name: str, document_text: str) -> tuple[bool, str]:
    """
    调用后端 /index_document 接口，为当前会话建立临时文档索引。
    """
    response = requests.post(
        "http://127.0.0.1:8000/index_document",
        json={
            "session_id": session_id,
            "file_name": file_name,
            "document_text": document_text
        },
        timeout=60
    )

    if response.status_code != 200:
        return False, f"文档索引失败: {response.text}"

    result = response.json()
    return True, f"文档索引完成，共切分 {result['chunk_count']} 个文本块。"


def clear_indexed_document(session_id: str) -> None:
    """
    调用后端清理接口，删除某个 session 对应的临时文档索引。

    说明：
    - 该函数不阻断主流程
    - 即使清理失败，也不影响前端继续新建会话
    """
    try:
        requests.delete(
            f"http://127.0.0.1:8000/clear_document/{session_id}",
            timeout=10
        )
    except Exception:
        # 第一阶段先做静默失败，避免清理动作影响主流程
        pass


# -----------------------------
# 展示当前模式的历史消息
# assistant 消息支持 markdown，便于工作流分段展示
# 并为 assistant 消息补充：
# - 复制当前结果
# - 导出 Markdown
# -----------------------------
for idx, message in enumerate(current_messages):
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

        if message["role"] == "assistant":
            # 整体结果操作：复制整段结果 + 导出 Markdown
            render_result_actions(
                result_text=message["content"],
                mode_name=mode,
                widget_key_suffix=f"history_{idx}"
            )

            # 如果是 workflow 结果，并且保留了分步结构，则额外支持分步复制
            if mode == "工作流优化" and message.get("workflow_blocks"):
                render_workflow_step_copy_actions(
                    workflow_blocks=message["workflow_blocks"],
                    widget_key_suffix=f"history_steps_{idx}"
                )


# -----------------------------
# 会话控制按钮
# -----------------------------
if st.sidebar.button("新建当前模式聊天"):
    # 先取旧的 session_id, 用于清理后端 RAG 内存索引
    old_session_id = st.session_state.mode_sessions[mode]["session_id"]
    clear_indexed_document(old_session_id)

    # 再重置当前模式会话
    st.session_state.mode_sessions[mode] = {
        "session_id": str(uuid4()),
        "messages": []
    }

    # 同步清理前端记录的索引状态
    st.session_state.rag_index_state.pop(mode, None)

    st.rerun()

if st.sidebar.button("清空全部聊天"):
    # 先清理所有模式当前 session 对应的后端 RAG 索引
    for mode_name, session_info in st.session_state.mode_sessions.items():
        old_session_id = session_info["session_id"]
        clear_indexed_document(old_session_id)

    # 再重置所有模式会话
    st.session_state.mode_sessions = create_mode_sessions(AVAILABLE_MODES)

    # 清空前端索引状态缓存
    st.session_state.rag_index_state = {}

    st.rerun()


# -----------------------------
# RAG 控件默认值
# 即使当前没有上传文件，也保证变量可安全使用
# -----------------------------
use_rag = False
rag_top_k = 3


# -----------------------------
# 文件上传分析区
# 仅在指定模式下启用
# -----------------------------
uploaded_file = None
uploaded_file_text = None
uploaded_file_error = None
use_uploaded_file = False

if mode in UPLOAD_ENABLED_MODES:
    st.markdown("### 文件上传分析")
    uploaded_file = st.file_uploader(
        "上传 TXT / Markdown / PDF 文件",
        type=["txt", "md", "pdf"],
        key=f"file_uploader_{mode}"
    )

    if uploaded_file is not None:
        uploaded_file_text, uploaded_file_error = extract_text_from_uploaded_file(uploaded_file)

        if uploaded_file_error:
            st.error(uploaded_file_error)
        elif uploaded_file_text:
            st.caption(f"已读取文件：{uploaded_file.name}（约 {len(uploaded_file_text)} 字）")

            use_rag = False
            rag_top_k = 3

            if mode in RAG_ENABLED_MODES:
                use_rag = st.checkbox(
                    "启用文档检索增强 (RAG) ",
                    value=True,
                    key=f"use_rag_{mode}"
                )

                if use_rag:
                    rag_top_k = st.slider(
                        "检索片段数量",
                        min_value=1,
                        max_value=5,
                        value=3,
                        key=f"rag_top_k_{mode}"
                    )

                st.caption("启用后，系统会先从上传文档中检索最相关片段，再交给模型处理。")

            preview_limit = 1200
            preview_text = uploaded_file_text[:preview_limit]
            if len(uploaded_file_text) > preview_limit:
                preview_text += "\n\n...[预览已截断，实际处理会使用完整文本]"

            st.text_area(
                "文件内容预览",
                value=preview_text,
                height=180,
                disabled=True,
                key=f"file_preview_{mode}"
            )

            use_uploaded_file = st.button(
                "使用上传文件开始处理",
                key=f"process_uploaded_file_{mode}",
                use_container_width=True
            )
else:
    st.caption("当前模式暂不支持文件上传分析。")


## -----------------------------
# 输入框
# 支持两种输入来源：
# 1. 手动输入
# 2. 上传文件后直接处理
# -----------------------------
prompt = st.chat_input("请输入您要处理的内容...")

submit_display_text = None   # 用于前端展示的文本
submit_raw_text = None       # 真正发送给后端的完整文本

# 优先处理手动输入
if prompt:
    submit_display_text = prompt
    submit_raw_text = prompt

# 如果没有手动输入，则判断是否点击了“使用上传文件开始处理”
elif use_uploaded_file and uploaded_file is not None and uploaded_file_text:
    submit_display_text = f"【上传文件】{uploaded_file.name}"

    # 如果启用了 RAG，则把“默认任务指令”作为 query
    # 真正的文档内容通过后端索引来提供
    if mode in RAG_ENABLED_MODES and use_rag:
        submit_raw_text = DEFAULT_FILE_MODE_PROMPTS[mode]
    else:
        # 不启用 RAG 时，继续沿用你当前“直接把全文作为输入”的做法
        submit_raw_text = uploaded_file_text

if submit_raw_text:
    # 1. 先展示并保存用户输入（仅写入当前模式）
    with st.chat_message("user"):
        st.write(submit_display_text)

    current_messages.append({
        "role": "user",
        "content": submit_display_text,
        "raw_content": submit_raw_text
    })

    # 如果当前上传文件启用了 RAG，则先确保文档已经索引
    if uploaded_file is not None and uploaded_file_text and mode in RAG_ENABLED_MODES and use_rag:
        text_fingerprint = build_text_fingerprint(uploaded_file_text)
        current_index_state = st.session_state.rag_index_state.get(mode)

        need_reindex = (
                not current_index_state
                or current_index_state.get("session_id") != current_session_id
                or current_index_state.get("text_fingerprint") != text_fingerprint
        )

        if need_reindex:
            success, message = index_uploaded_document(
                session_id=current_session_id,
                file_name=uploaded_file.name,
                document_text=uploaded_file_text
            )

            if not success:
                st.error(message)
                st.stop()

            st.success(message)

            st.session_state.rag_index_state[mode] = {
                "session_id": current_session_id,
                "file_name": uploaded_file.name,
                "text_fingerprint": text_fingerprint
            }

    # 2. 根据模式决定调用哪个接口
    is_workflow = mode == "工作流优化"
    url = "http://127.0.0.1:8000/workflow_stream" if is_workflow else "http://127.0.0.1:8000/chat_stream"

    # 3. 构造符合 ChatRequest 的请求体
    payload = {
        "session_id": current_session_id,
        "task_type": MODE_TO_TASK_TYPE[mode],
        "input_text": submit_raw_text,
        "persona": mode,
        "history": build_history_for_api(current_messages[:-1]),
        "user_options": {},
        "use_rag": use_rag,
        "rag_top_k": rag_top_k
    }

    # 4. 发送流式请求
    response = requests.post(
        url,
        json=payload,
        stream=True,
        timeout=120
    )

    # 5. 请求失败直接报错
    if response.status_code != 200:
        st.error(f"请求失败: {response.text}")
    else:
        with st.chat_message("assistant"):
            placeholder = st.empty()
            placeholder.markdown("思考中... 🤔")

            # 用于聊天模式的完整文本
            full_response = ""

            # 用于工作流模式的分步骤结果
            workflow_blocks: dict[str, str] = {}

            # 标记是否收到第一条有效事件，用来清理“思考中”
            first_event_received = False

            # 6. 逐行解析 SSE 事件流
            for raw_line in response.iter_lines():
                if not raw_line:
                    continue

                raw_text = raw_line.decode("utf-8").strip()

                # SSE 标准格式：data: {...}
                if not raw_text.startswith("data: "):
                    continue

                json_text = raw_text[6:]

                try:
                    event = json.loads(json_text)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("event_type")
                step_name = event.get("step_name")
                content = event.get("content", "")
                error_message = event.get("error_message")

                if not first_event_received:
                    placeholder.empty()
                    first_event_received = True

                # 工作流开始 / 步骤开始：可选择显示状态，不强制写入最终结果
                if event_type in {"workflow_start", "step_start"}:
                    if is_workflow and step_name:
                        current_markdown = format_workflow_blocks(workflow_blocks)
                        if current_markdown:
                            placeholder.markdown(current_markdown)

                # 增量事件：按模式分别处理
                elif event_type == "delta":
                    if is_workflow:
                        if step_name:
                            workflow_blocks.setdefault(step_name, "")
                            workflow_blocks[step_name] += content

                        placeholder.markdown(format_workflow_blocks(workflow_blocks) + "\n\n▌")
                    else:
                        full_response += content
                        placeholder.markdown(full_response + "▌")
                        time.sleep(0.01)

                # 步骤完成事件：用于工作流模式的最终分步内容落盘
                elif event_type == "step_complete":
                    if step_name:
                        workflow_blocks[step_name] = content
                        placeholder.markdown(format_workflow_blocks(workflow_blocks))

                # 最终完成事件
                elif event_type == "final":
                    if is_workflow:
                        placeholder.markdown(format_workflow_blocks(workflow_blocks))
                    else:
                        # 聊天模式下，final.content 可能是完整文本；
                        # 如果前面 delta 已完整累计，则无需重复追加
                        if not full_response and content:
                            full_response = content
                        placeholder.markdown(full_response)

                # 错误事件
                elif event_type == "error":
                    st.error(error_message or "请求失败")
                    break

            # 7. 生成最终写入聊天记录的 assistant 内容（仅写入当前模式）
            if is_workflow:
                final_display_text = format_workflow_blocks(workflow_blocks)
            else:
                final_display_text = full_response

            # 8. 当前轮结果操作区
            # 在新结果刚生成时，立即支持：
            # 1. 整体复制
            # 2. Markdown 导出
            # 3. workflow 分步复制
            if final_display_text.strip():
                render_result_actions(
                    result_text=final_display_text,
                    mode_name=mode,
                    widget_key_suffix="latest_result"
                )

                if is_workflow and workflow_blocks:
                    st.markdown("<div style='height: 0.25rem;'></div>", unsafe_allow_html=True)

                    render_workflow_step_copy_actions(
                        workflow_blocks=workflow_blocks,
                        widget_key_suffix="latest_steps"
                    )

            # 9. 防止空内容写入历史
            if final_display_text.strip():
                assistant_message = {
                    "role": "assistant",
                    "content": final_display_text
                }

                # workflow 模式下，把分步结果一并保存到消息里
                # 这样历史消息也能继续支持“分步复制”
                if is_workflow and workflow_blocks:
                    assistant_message["workflow_blocks"] = workflow_blocks.copy()

                current_messages.append(assistant_message)