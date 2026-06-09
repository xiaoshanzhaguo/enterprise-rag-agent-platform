"""
前端结果展示与操作模块。

职责：
1. 负责将普通结果、工作流结果和 RAG 引用检索结果格式化为适合前端展示的内容
2. 提供结果复制、Markdown 导出、workflow 分步复制等用户操作能力
3. 封装前端结果区域的展示逻辑，避免页面主代码过于臃肿

说明：
- 当前模块属于前端展示层，不直接负责业务处理与模型调用
- 主要服务于 Streamlit 页面中的结果渲染、操作按钮和辅助可视化展示
- 适合当前项目“多模式内容处理 + workflow 结果展示 + RAG 引用来源展示”的交互场景
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


def _build_rag_preview_items(chunks: list[dict], fallback_file_name: str) -> list[dict]:
    """
    将后端返回的 RAG chunk 转换为前端展示用字段。

    函数说明：
    1. 统一读取 file_name、chunk_id、score、source 等展示字段。
    2. source 优先使用后端返回值；没有时用 file_name + chunk_id 兜底生成。
    3. display_text 优先展示完整原文；没有完整原文时退回 text_preview。

    :param chunks: 后端返回的 RAG 命中片段列表
    :param fallback_file_name: 当前文档兜底文件名
    :return: 前端渲染引用来源和原文片段时使用的字段列表
    """
    # 存放整理后的片段展示数据，避免后续渲染时重复解析同一批字段
    preview_items = []

    # 遍历每个命中的检索片段，并把字段整理成统一结构
    for chunk in chunks:
        # 优先使用 chunk 自带文件名；如果没有，则使用当前会话文档名兜底
        chunk_file_name = chunk.get("file_name") or fallback_file_name
        # 文本块编号用于拼接引用来源
        chunk_id = chunk.get("chunk_id", "-")
        # 检索分数用于展示当前片段和 query 的相关程度
        score = chunk.get("score", 0)
        # source 优先使用后端标准来源；没有时按同样格式在前端兜底生成
        source = chunk.get("source") or f"{chunk_file_name}#chunk-{chunk_id}"
        # 完整原文片段用于引用核对
        text = chunk.get("text", "").strip()
        # 预览文本作为完整原文缺失时的兜底
        text_preview = chunk.get("text_preview", "").strip()
        # 原文长度用于帮助用户判断片段规模
        text_length = chunk.get("text_length", 0)

        # 将整理后的字段加入列表，后续两个展示区域都复用这一份数据
        preview_items.append({
            "source": source,
            "score": score,
            "text_length": text_length,
            "display_text": text or text_preview or "（无原文内容）"
        })

    return preview_items


def render_rag_preview(chunks: list[dict], status: dict | None = None, expanded: bool = True) -> None:
    """
    展示本次 RAG 检索命中的引用来源和原文片段。

    :param chunks: RAG 命中的片段摘要列表
    :param status: 当前 session 的 RAG 状态信息
    :param expanded: 是否默认展开引用面板
    :return: None
    """
    # 如果没有命中任何片段，则明确提示知识库没有依据，避免用户误以为模型已经参考了文档
    if not chunks:
        st.warning("知识库中没有找到依据。")
        return

    # 如果 status 为 None，则退回为空字典。这样后面 .get(...) 不会报错
    status = status or {}
    # 优先展示当前文件名；如果没有则显示默认文案
    file_name = status.get("file_name") or "当前文档"
    # 取出当前索引距离过期还剩多少秒
    expires_in_seconds = status.get("expires_in_seconds")

    # 构造顶部摘要说明，先说明本次回答参考了哪个文档和多少个片段
    caption_parts = [f"文档：{file_name}", f"命中片段：{len(chunks)}"]
    # 如果过期时间有效且大于 0，就追加一条：索引大约多少分钟后过期
    if isinstance(expires_in_seconds, int) and expires_in_seconds > 0:
        caption_parts.append(f"索引约 {expires_in_seconds // 60} 分钟后过期")

    # 统一整理引用来源、检索分数和原文展示文本，避免下面两个展示区域重复解析 chunk 字段
    preview_items = _build_rag_preview_items(chunks, file_name)

    # 用折叠面板展示本次命中的 RAG 引用和原文片段
    with st.expander("引用来源与命中原文片段", expanded=expanded):
        # 把顶部说明用 · 拼成一行灰色小字说明
        st.caption(" · ".join(caption_parts))

        # 先集中展示引用来源，帮助用户快速判断模型答案引用了哪些文档片段
        st.markdown("**引用来源**")
        for index, item in enumerate(preview_items, start=1):
            st.markdown(f"{index}. [来源: {item['source']}] · score={item['score']}")

        st.markdown("---")

        # 遍历每个命中的检索片段，并从 1 开始编号
        for index, item in enumerate(preview_items, start=1):
            # 渲染片段标题说明行，字段与模型引用格式保持一致
            st.markdown(
                f"**原文片段 {index}** · "
                f"[来源: {item['source']}] · "
                f"score={item['score']} · "
                f"{item['text_length']} 字"
            )
            # 展示命中的原文片段，方便用户核对模型答案是否有依据
            st.markdown(item["display_text"])
