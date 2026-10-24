"""调用字段提取模板并校验 JSON，失败时返回明确状态，不伪造申请信息。"""

import json

from backend.llm.chat_model import invoke_chat_model
from backend.prompt.process_prompts import build_process_field_extraction_messages
from backend.schema.process_schema import ProcessExtractionResult, ProcessFields


PROCESS_FIELD_DEFINITIONS = {
    name: field.description for name, field in ProcessFields.model_fields.items()
}


def extract_process_fields(input_text: str, client) -> ProcessExtractionResult:
    """仅提取本次原文，缺失字段由后端计算；不执行提交、审批或权限变更。"""
    fields = ProcessFields()
    error = None
    try:
        messages = build_process_field_extraction_messages(
            "企业流程申请", PROCESS_FIELD_DEFINITIONS, input_text,
        )
        raw = invoke_chat_model(client=client, messages=messages, temperature=0)
        # 兼容模型常见的 Markdown JSON 围栏，不接受 JSON 前后的额外说明。
        normalized = raw.strip()
        if normalized.startswith("```") and normalized.endswith("```"):
            lines = normalized.splitlines()
            if lines[0].lower() not in {"```", "```json"}:
                raise ValueError("不支持的 JSON 围栏")
            normalized = "\n".join(lines[1:-1])
        payload = json.loads(normalized)
        if not isinstance(payload, dict) or set(payload) - {"fields", "missing_fields"}:
            raise ValueError("无效的提取协议")
        fields = ProcessFields.model_validate(payload["fields"])
    except Exception:
        # 不将模型响应、连接信息等内部异常原文发送到前端。
        error = "流程字段提取失败，请补充申请信息后重试。"
    return ProcessExtractionResult(
        fields=fields,
        missing_fields=[name for name, value in fields.model_dump().items() if value is None],
        status="failed" if error else "extracted",
        error=error,
    )
