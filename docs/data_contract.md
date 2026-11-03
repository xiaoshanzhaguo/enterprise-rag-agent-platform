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

当前前端上传入口已经支持选择 `HR / 财务 / IT / 产品` 分类。用户显式选择分类时，以前端选择为准；如果旧调用方没有传分类字段，后端仍会根据文件名和正文关键词做轻量自动推断。

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

检索命中的 chunk 会透出以下字段，前端会在“参考依据”卡片、检索详情表和完整原文说明中展示来源分类：

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

- 已实现：新库初始化时创建完整数据库字段、上传分类选择、文档索引接口字段、RAG 状态透出、检索结果来源分类展示、Agent 路由结果持久化、n8n 执行记录表和写入函数。
- 暂未实现：按部门/流程筛选检索、真实 n8n Webhook 调用。
- 后续可扩展：在知识库管理页增加部门/流程筛选，在 Agent 判断到流程执行类意图时触发 n8n，并把执行状态回写到 `n8n_execution_records`。

## 6. R1 索引任务与管理接口

本节记录 2026-11-03 的 R1 实现；前文第 4、5 节中关于 n8n 尚未接入的描述是早期记录，不代表当前状态。现有流程接口另见 [第 20 天说明](day20_n8n_sqlite.md)，本次 R1 不变更该链路。

### 持久化与状态

`rag_index_tasks` 通过唯一 `document_id` 关联 `documents.id`，与 `document_chunks` 在同一 SQLite 事务准备。`operation_id` 为任务 UUID；另保存输入 manifest/摘要、`target_profile_json`/摘要、预期 Chunk 数、`state_version`、`attempt_count`、当前执行 token/owner、错误及时间戳。任务状态独立于文档业务字段 `process_status`。

| 状态 | 含义与允许操作 |
| --- | --- |
| `not_requested` | keyword 文本已保存，未请求向量索引，初始 attempt_count=0 |
| `pending` + `execution_claimed=false` | 尚未领取；配置兼容时可显式 resume |
| `pending` + `execution_claimed=true` | 已领取，只查询；不自动超时接管 |
| `ready` | 向量写入且 SQLite 成功确认；重复执行不重新生成向量 |
| `failed` | 索引失败；配置兼容时显式 retry，复用任务及稳定 ID |
| `unknown`（仅公开投影） | 历史文档无任务，不推断索引成功或失败 |

人工恢复将指定版本的 claimed pending 转为 `failed`，错误码 `interrupted`，清除旧 token 并递增版本；保留 attempt_count 与所有输入 ID，之后必须走 retry。仅有 Repository/CLI 入口，无 recover HTTP API。

Profile 保存 provider、model、存储/端点指纹、Collection 名称、维度、归一化策略及距离度量，不保存 Key 或敏感连接串。绑定使用当前 token + 版本 CAS，同事务更新 Profile、摘要与版本；同版本同值绑定幂等，旧版本和修改已知值均拒绝。具体实现见 `backend/db/index_repository.py`。

### FastAPI

完整字段与校验以 `backend/schema/rag_index_schema.py`、`chat_schema.py` 及运行中的 `/docs` 为准。

| 方法与路径 | 行为 |
| --- | --- |
| `POST /index_document` | 保留原上传请求及原响应字段，新增任务回执；keyword 不调用向量适配器，vector 同步执行，重复 failed 上传不会隐式重试 |
| `GET /rag_index_operations/{operation_id}` | 只读查询单个任务 |
| `POST /rag_index_operations/{operation_id}/resume` | 继续未领取的 pending |
| `POST /rag_index_operations/{operation_id}/retry` | 重试 failed，包括人工恢复后的 interrupted |
| `GET /rag_status/{session_id}` | 文档列表增加 `text_status`、`vector_index`、`tracking_source`，并返回 `vector_index_summary` |

单任务三个接口均要求查询参数 `session_id`，可附 `knowledge_base_id`、`user_role`；公共知识库写操作沿用演示级 `kb_admin` 检查，不代表完整身份认证。resume/retry 的 JSON 仅接受严格正整数 `expected_state_version`，禁止额外字段。上传请求示例需另加 `knowledge_base_id` 和 `user_role: "kb_admin"` 才写入公共知识库；不传知识库时保留 session 范围。

上传新增字段包括 `document_id`、`operation_id`、`text_status: "persisted"`、`state_confirmed`、`document_reused`、`effective_retrieval_mode`、`vector_index`、`status_url`。单任务回执另有 `reason`、`resume_applied`、`retry_applied`。`vector_index` 包含状态、版本、次数、`execution_claimed`、`current_config_compatible`、`runtime_validation_required`、`resumable`、`retryable`、受控错误和查询/操作 URL；绝不返回 `attempt_token`。

| HTTP | 含义 |
| --- | --- |
| 200 | ready、not_requested，或正常查询；ready 的重复动作保持只读 |
| 202 | 已被领取或执行名额不足，查询 Location/status_url；不自动再发 POST |
| 409 | 版本、动作、输入或配置冲突；先 GET 核对 |
| 502 | Embedding/向量写入等执行失败；回执可能表明文本已保存、任务 failed |
| 503 | 存储不可用、执行预算耗尽/中断或状态未确认；不得推断未写入 |
| 400 / 403 / 404 / 422 | 参数、角色、资源范围或 FastAPI 请求校验失败 |

执行错误响应采用 `{ "detail": "说明", "receipt": ... }`；存储异常等无法获取回执时 `receipt` 为 null，FastAPI 自身 422 保留框架格式。`state_confirmed=false` 时 `vector_index` 为 null，前端仅查询；断网且未知 operation_id 时先刷新文档列表核对，禁止自动重新上传。React 展示规则见 [架构说明](architecture.md#r1-索引管理)。
