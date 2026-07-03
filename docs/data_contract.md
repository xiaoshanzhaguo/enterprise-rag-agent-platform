# 数据表与接口字段设计

本文档记录企业内部知识与流程自动化助手当前的数据字段边界，重点覆盖知识库分类、流程信息、Agent 路由结果和 n8n 执行记录。

## 1. 文档业务标签

落库位置：`documents`

接口位置：`POST /index_document`

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `knowledge_base_type` | `TEXT` | `general` | 知识库类型，例如 `hr`、`finance`、`it`、`product` |
| `department` | `TEXT` | `NULL` | 文档所属部门，例如 `HR`、`财务`、`IT`、`产品` |
| `process_type` | `TEXT` | `NULL` | 流程类型，例如 `leave`、`reimbursement`、`vpn`、`gitlab_access` |
| `process_status` | `TEXT` | `active` | 流程状态，例如 `active`、`draft`、`archived` |

当前前端上传入口暂时没有额外表单，所以后端会根据文件名和正文关键词做轻量自动推断。未来如果要做更完整的知识库管理页，可以把这些字段做成下拉框或标签选择器。

请求示例：

```json
{
  "session_id": "demo-session",
  "file_name": "reimbursement_policy.md",
  "document_text": "FIN-01 日常费用报销制度...",
  "knowledge_base_type": "finance",
  "department": "财务",
  "process_type": "reimbursement",
  "process_status": "active"
}
```

## 2. RAG 检索与 Agent 路由结果

落库位置：`rag_queries`

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `query_text` | `TEXT` | 无 | 实际执行检索的 query |
| `retrieval_mode` | `TEXT` | `unknown` | 实际检索方式，例如 `vector`、`keyword`、`no_hit` |
| `agent_route_result` | `TEXT` | `NULL` | Agent 路由结果，例如 `use_knowledge_base` |
| `agent_route_reason` | `TEXT` | `NULL` | Agent 判断是否检索知识库的理由 |
| `agent_rewritten_query` | `TEXT` | `NULL` | Agent 改写后的检索 query |

说明：

- 当 Agent 判断需要知识库时，会写入 `rag_queries` 和 `rag_hits`。
- 当 Agent 判断需要知识库但没有命中依据时，`retrieval_mode` 会记录为 `no_hit`。
- 当 Agent 判断不需要知识库时，不会生成 RAG 查询记录，但路由结果会保存到 assistant 消息的 `metadata_json.agent_route` 中。

## 3. 检索片段业务标签

接口位置：`POST /rag_preview`、Agent final metadata 中的 `rag_preview_chunks`

检索命中的 chunk 会透出以下字段，方便前端后续展示“来自哪个部门、哪个流程”：

| 字段 | 说明 |
| --- | --- |
| `knowledge_base_type` | 来源文档的知识库类型 |
| `department` | 来源文档所属部门 |
| `process_type` | 来源文档流程类型 |
| `process_status` | 来源文档流程状态 |

## 4. n8n 执行记录

落库位置：`n8n_execution_records`

当前项目还没有真正调用 n8n，因此先预留执行记录表和 Repository 写入函数，后续接入 n8n Webhook 时可以直接复用。

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `session_id` | `TEXT` | `NULL` | 关联聊天会话 |
| `message_id` | `INTEGER` | `NULL` | 关联 assistant 消息 |
| `workflow_name` | `TEXT` | 无 | n8n 工作流名称 |
| `workflow_url` | `TEXT` | `NULL` | Webhook 或工作流地址 |
| `trigger_source` | `TEXT` | `agent` | 触发来源，例如 `agent`、`manual`、`webhook` |
| `execution_id` | `TEXT` | `NULL` | n8n 返回的执行 ID |
| `status` | `TEXT` | `planned` | 执行状态，例如 `planned`、`running`、`success`、`failed` |
| `input_json` | `TEXT` | `{}` | 传给 n8n 的输入参数 |
| `output_json` | `TEXT` | `{}` | n8n 返回的结果 |
| `error_message` | `TEXT` | `NULL` | 执行失败时的错误信息 |
| `started_at` | `TEXT` | `NULL` | 执行开始时间 |
| `finished_at` | `TEXT` | `NULL` | 执行结束时间 |

## 5. 当前实现边界

- 已实现：新库初始化时创建完整数据库字段、文档索引接口字段、RAG 状态透出、Agent 路由结果持久化、n8n 执行记录表和写入函数。
- 暂未实现：前端字段下拉框、按部门/流程筛选检索、真实 n8n Webhook 调用。
- 后续可扩展：在知识库管理页增加部门/流程筛选，在 Agent 判断到流程执行类意图时触发 n8n，并把执行状态回写到 `n8n_execution_records`。
