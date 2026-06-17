from backend.prompt.base_prompt import BASE_PROMPT
from backend.prompt.prompt_templates import MODE_PROMPTS

def build_system_prompt(mode_name: str = "default", custom_prompt: str = "") -> str:
    """
    根据当前功能模式构造系统提示词。

    :param mode_name: 当前功能模式名称，例如“企业知识库问答”“内容分析”“结构优化”等
    :param custom_prompt: 用户额外补充的提示词，默认为空
    :return: 拼接完成后的提示词字符串
    """
    # 根据当前模式读取对应提示词；没有匹配时使用 default 模式兜底
    mode_prompt = MODE_PROMPTS.get(mode_name, MODE_PROMPTS["default"])

    final_prompt = f"""
         {BASE_PROMPT}
         
         【当前功能模式设定】
         {mode_prompt}
         
         【用户自定义补充】
         {custom_prompt if custom_prompt else "无"}
         
         请严格按照以上设定完成回复。
    """.strip()

    return final_prompt
