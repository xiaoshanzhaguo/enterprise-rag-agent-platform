"""
工作流结果格式化工具模块。

职责：
1. 维护 workflow 内部步骤名与展示标题的映射关系。
2. 将 workflow 分步骤结果统一格式化为 Markdown 文本。

说明：
- 当前模块是纯工具层，不依赖 Streamlit、数据库或后端服务。
- 前端展示层和后端历史恢复逻辑可以共同引用这里的格式化函数。
- 这样可以避免同一份 workflow 展示格式在多个文件里重复维护。
"""

# 未来版本兼容特性
from __future__ import annotations


# workflow 步骤名到前端展示标题的映射表
WORKFLOW_STEP_TITLE_MAP = {
    "summary": "🧠 内容总结",
    "analysis": "🔍 问题分析",
    "suggestion": "✨ 优化建议",
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
        title = WORKFLOW_STEP_TITLE_MAP.get(step_name, step_name)
        formatted_parts.append(f"### {title}\n\n{content}\n")

    # 用空行拼接各步骤内容，返回完整 Markdown 文本
    return "\n\n".join(formatted_parts)
