"""
前端结果展示与操作模块。

职责：
1. 负责将普通结果、工作流结果和 RAG 检索结果格式化为适合前端展示的内容
2. 提供结果复制、Markdown 导出、workflow 分步复制等用户操作能力
3. 封装前端结果区域的展示逻辑，避免页面主代码过于臃肿

说明：
- 当前模块属于前端展示层，不直接负责业务处理与模型调用
- 主要服务于 Streamlit 页面中的结果渲染、操作按钮和辅助可视化展示
- 适合当前项目“多模式内容处理 + workflow 结果展示 + RAG 检索预览”的交互场景
"""
# 导入 json 模块。后面在 render_copy_button() 里，会用 json.dumps(text) 把 Python 字符串安全地转成 JS 里可用的字符串。
import json
# 导入时间模块。后面生成导出文件名和导出时间时会用到。
import time
# 导入 uuid4()。给复制按钮生成唯一 ID，避免页面上多个按钮的 DOM ID 冲突。
from uuid import uuid4

# 导入 Streamlit 主模块，并简写成 st。后面所有页面组件都通过 st.xxx() 调用。
import streamlit as st
# 导入 Streamlit 的自定义组件模块。后面用 components.html(...) 渲染 HTML + CSS + JS，自定义复制按钮。
import streamlit.components.v1 as components


# workflow 步骤名到前端展示标题的映射表
STEP_TITLE_MAP = {
    "summary": "🧠 内容总结",
    "analysis": "🔍 问题分析",
    "suggestion": "✨ 优化建议"
}


def format_workflow_blocks(workflow_blocks: dict[str, str]) -> str:
    """
    将工作流分步骤结果格式化为 Markdown 展示文本。

    :param workflow_blocks: workflow 三个步骤的结果字典
    :return: 格式化后的 Markdown 字符串
    """
    # 创建一个空列表，用来收集格式化后的每一部分文本
    formatted_parts = []

    # 按固定顺序渲染 workflow 三个步骤，避免顺序混乱
    for step_name in ["summary", "analysis", "suggestion"]:
        # 从 workflow_blocks 里取当前步骤对应的内容。如果没有这个步骤，就给空字符串。再 .strip() 去掉首尾空白
        content = workflow_blocks.get(step_name, "").strip()
        # 如果当前步骤没有内容，就跳过这一轮，不展示这个步骤
        if not content:
            continue

        # 将内部步骤名映射成更友好的中文标题。如果找不到，就退回原始步骤名
        title = STEP_TITLE_MAP.get(step_name, step_name)
        formatted_parts.append(f"### {title}\n\n{content}\n")

    # 用空行拼接各步骤内容，返回完整 Markdown 文本
    return "\n\n".join(formatted_parts)


def build_markdown_filename(mode_name: str) -> str:
    """
    生成 Markdown 导出文件名。

    :param mode_name: 当前模式名称
    :return: 导出文件名
    """
    # 生成当前时间字符串，格式如：20260601_153045。用于保证导出的文件名唯一，并且便于按时间识别
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    # 将模式名中的空格替换为下划线，避免文件名不规范
    safe_mode_name = mode_name.replace(" ", "_")

    return f"{safe_mode_name}_result_{timestamp}.md"


def build_markdown_content(mode_name: str, result_text: str) -> str:
    """
    将结果包装成更完整的 Markdown 文本，便于导出保存。

    :param mode_name: 当前模式名称
    :param result_text: 当前结果文本
    :return: 导出的 Markdown 内容
    """
    # 记录当前导出时间
    export_time = time.strftime("%Y-%m-%d %H:%M:%S")

    return (
        "# AI 内容分析与创作助手导出结果\n\n"
        f"- 模式：{mode_name}\n"
        f"- 导出时间：{export_time}\n\n"
        "---\n\n"
        f"{result_text.strip()}\n"
    )


def render_copy_button(text: str, label: str, button_id_suffix: str) -> None:
    """
    渲染一个复制按钮，用于将指定文本复制到剪贴板。通过内嵌 HTML + JS 将结果复制到系统剪贴板。

    :param text: 需要复制的文本内容
    :param label: 按钮显示文字
    :param button_id_suffix: 用于生成唯一按钮 ID，避免多个按钮冲突
    :return: None
    """
    # 生成一个唯一按钮 ID，避免多个复制按钮冲突。button_id_suffix：人为区分按钮用途、uuid4().hex：再加一个随机唯一值
    button_id = f"copy_btn_{button_id_suffix}_{uuid4().hex}"

    # 渲染HTML自定义组件
    components.html(
        # 开始写一个 Python f-string 多行 HTML 模板
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
        # 表示这个 HTML 组件在页面里占 40 像素高
        height=40
    )


def render_result_actions(result_text: str, mode_name: str, widget_key_suffix: str) -> None:
    """
    为 assistant 结果渲染操作按钮：
    1. 复制当前结果
    2. 导出 Markdown

    :param result_text: 当前结果文本
    :param mode_name: 当前模式名称
    :param widget_key_suffix: 用于生成组件唯一 key 的后缀
    :return: None
    """
    # 如果结果为空，则不渲染操作按钮
    if not result_text.strip():
        return

    # 构造导出内容和导出文件名
    markdown_content = build_markdown_content(mode_name, result_text)
    file_name = build_markdown_filename(mode_name)

    # 创建两列布局：左边复制，右边导出
    col1, col2 = st.columns(2, gap="small")

    # with col1: 表示下面这一小段组件渲染到左边那一列里
    with col1:
        render_copy_button(
            text=result_text,
            label="复制当前结果",
            button_id_suffix=widget_key_suffix
        )

    with col2:
        st.download_button(
            label="导出 Markdown",
            data=markdown_content.encode("utf-8-sig"),  # 下载的内容本体，带 BOM 便于 Windows 编辑器识别中文
            file_name=file_name,
            mime="text/markdown; charset=utf-8",  # 声明下载文件类型，告诉浏览器这是 UTF-8 Markdown 文本文件
            key=f"download_md_{widget_key_suffix}",  # 保证按钮唯一 key
            on_click="ignore",  # 点击时只执行下载动作，减少页面状态干扰
            use_container_width=True,  # 宽度撑满当前列
        )


def render_workflow_step_copy_actions(workflow_blocks: dict[str, str], widget_key_suffix: str) -> None:
    """
    为 workflow 结果渲染“分步复制”按钮。默认折叠，避免界面过于拥挤。

    :param workflow_blocks: workflow 三个步骤的结果字典
    :param widget_key_suffix: 用于生成组件唯一 key 的后缀
    """
    # 如果没有 workflow 数据，则不渲染任何内容
    if not workflow_blocks:
        return

    # 使用折叠面板避免界面过于拥挤
    with st.expander("分步复制", expanded=False):
        # 创建三列布局，分别放总结 / 问题 / 建议复制按钮
        col1, col2, col3 = st.columns(3, gap="small")

        with col1:
            summary_text = workflow_blocks.get("summary", "").strip()
            # 如果这一步确实有内容，才显示按钮
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


def render_rag_preview(chunks: list[dict], status: dict | None = None) -> None:
    """
    展示本次 RAG 检索命中的片段，帮助用户判断回答依据。

    :param chunks: RAG 命中的片段摘要列表
    :param status: 当前 session 的 RAG 状态信息
    :return: None
    """
    # 如果没有命中任何片段，则不渲染预览区
    if not chunks:
        return

    # 如果 status 为 None，则退回为空字典。这样后面 .get(...) 不会报错
    status = status or {}
    # 优先展示当前文件名；如果没有则显示默认文案
    file_name = status.get("file_name") or "当前文档"
    # 取出当前索引距离过期还剩多少秒
    expires_in_seconds = status.get("expires_in_seconds")

    # 构造顶部摘要说明
    caption_parts = [f"文档：{file_name}", f"命中片段：{len(chunks)}"]
    # 如果过期时间有效且大于 0，就追加一条：索引大约多少分钟后过期
    if isinstance(expires_in_seconds, int) and expires_in_seconds > 0:
        caption_parts.append(f"索引约 {expires_in_seconds // 60} 分钟后过期")

    # 用折叠面板展示本次命中的 RAG 片段
    with st.expander("本次 RAG 检索片段", expanded=False):
        # 把顶部说明用 · 拼成一行灰色小字说明
        st.caption(" · ".join(caption_parts))

        # 遍历每个命中的检索片段，并从 1 开始编号
        for index, chunk in enumerate(chunks, start=1):
            chunk_id = chunk.get("chunk_id", "-")
            score = chunk.get("score", 0)
            text_preview = chunk.get("text_preview", "").strip()
            text_length = chunk.get("text_length", 0)

            # 渲染片段标题说明行
            st.markdown(f"**片段 {index}** · chunk_id={chunk_id} · score={score} · {text_length} 字")
            st.markdown(text_preview or "（无预览内容）")
