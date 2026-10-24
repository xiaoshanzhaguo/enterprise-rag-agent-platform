# 第 16 天：LangChain PromptTemplate

- `backend/prompt/agent_prompts.py`：分别维护意图分类、Query Rewrite 规则，组合为决策提示词；保留一次模型调用和原 JSON 协议。
- `backend/services/agent_router.py`：调用 `build_agent_decision_messages(question)`，不再内联拼接决策提示词。
- `backend/prompt/process_prompts.py`：通过 `build_process_field_extraction_messages(process_name, field_definitions, input_text)` 构造字段提取消息，字段由调用方指定。要求模型返回 `fields` 和 `missing_fields`，缺失值为 `null`。

模板使用 `langchain_core.prompts.PromptTemplate`。固定 JSON 示例中的花括号使用双花括号转义；用户原文和字段定义作为参数传入，不会被当成模板再次解析。沿用现有 ChatModel 适配层的 role/content 消息格式。

流程字段提取目前只提供模板入口，尚未接入 API 或申请业务流程。后续调用方需要解析、校验模型返回的 JSON；模板本身不保证模型输出合法。现有工作流仍负责总结、问题分析和优化建议。

离线验证（无需模型 API）：

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -p test_prompt_templates.py -v
```
