# 数据表与接口字段设计

本文档记录企业内部知识与流程自动化助手当前的数据字段边界，覆盖知识库分类、流程信息、Agent 路由、n8n 执行记录及 R1/R2/R3 文档生命周期。

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
| 200 | ready、not_requested，或正常查询；ready 不重复写向量，R3 可尝试补确认尚未发布候选的发布 |
| 202 | 已被领取或执行名额不足，查询 Location/status_url；不自动再发 POST |
| 409 | 版本、动作、输入或配置冲突；先 GET 核对 |
| 502 | Embedding/向量写入等执行失败；回执可能表明文本已保存、任务 failed |
| 503 | 存储不可用、执行预算耗尽/中断或状态未确认；不得推断未写入 |
| 400 / 403 / 404 / 422 | 参数、角色、资源范围或 FastAPI 请求校验失败 |

执行错误响应采用 `{ "detail": "说明", "receipt": ... }`；存储异常等无法获取回执时 `receipt` 为 null，FastAPI 自身 422 保留框架格式。`state_confirmed=false` 时 `vector_index` 为 null，前端仅查询；断网且未知 operation_id 时先刷新文档列表核对，禁止自动重新上传。React 展示规则见 [架构说明](architecture.md#r1-索引管理)。

## 7. R2 单文档删除与生命周期

实现提交 `30fb954`，普通会话范围保护提交 `508463a`。仅支持公共知识库中的单个 Document，不提供批量删除或撤销。

`documents` 新增 `lifecycle_state`（active/deleting）及 `lifecycle_version`（初始 1，标记删除时递增）。它们控制可检索性和删除并发，不是内容版本号。`document_deletion_tasks` 保存唯一 `document_id`、UUID operation_id、知识库及 storage_session_id、文件名、Vector ID JSON、目标 Collection/存储指纹 JSON 和 SHA-256、状态、state_version、attempt_token、attempt_count、错误与时间戳。任务不设置 Document/Chunk 外键，因此最终级联删除不会丢失目标快照。

| 删除任务状态 | 行为 |
| --- | --- |
| pending，未领取 | 可通过 retry 接口显式继续；不自动重新 DELETE |
| pending，已领取 | 只查询；不能重复执行或按超时抢占 |
| failed | 保留目标，Document 仍为 deleting；按版本显式 retry |
| completed | Document/Chunk 已删除，任务仍可查询；重复删除返回原任务，不重复执行 |

| 接口 | 请求与行为 |
| --- | --- |
| `DELETE /documents/{document_id}` | JSON `{ "expected_document_version": 1 }`；原子检查索引领取、创建/复用任务并同步尝试删除 |
| `GET /document_deletions/{operation_id}` | 只读查询 |
| `POST /document_deletions/{operation_id}/retry` | JSON `{ "expected_state_version": 3 }`；显式重试 failed 或继续未领取 pending |

三个接口均需查询参数 `knowledge_base_id` 和有管理权限的 `user_role`（kb_admin/admin），后端校验角色及资源范围。版本字段为严格正整数，禁止额外请求字段。仍沿用项目的演示角色机制，不代表正式身份认证。

删除 API 回执包括 operation/document/knowledge_base ID、文件名、status/state_version、attempt_count、execution_claimed、state_confirmed、retryable、受控 error_message、时间及 status_url；不暴露 token 或完整目标。回执与 Location 中的查询地址携带编码后的知识库及调用者角色，可直接跟随；列表投影不代填管理员身份，React 查询时使用当前页面的角色范围。

200 表示查询或完成，202 表示 pending；403/404 分别为权限或范围失败，409 为索引正在执行、版本或状态冲突，R3 中还包括试图删除启用逻辑文档的当前版本；502 为已确认执行失败，503 为存储不可用或结果未确认。执行已取得回执时，错误 HTTP 可直接返回删除回执对象；参数/范围等错误使用 `{ "detail": "说明" }`，不能套用 R1 的嵌套 receipt 格式。状态未确认时先查询，不能据超时推断没有删除。

`GET /rag_status/{session_id}` 的每项 Document 还返回生命周期与可用的 deletion 回执；deleting 项仍保留在管理列表供查询/恢复，但不参与正常检索。完成后文档从列表移除。索引已领取时 DELETE 返回 409，未领取索引由生命周期及 CAS 阻断。人工恢复命令见 [部署说明](deployment.md#r2-删除任务与人工恢复)。

## 8. R3 逻辑文档与版本管理

实现与最终审查修复提交 `f450739`；文档按 2026-11-05 的该 HEAD 核对。保留 R1/R2 接口与稳定 ID，不把内容版本号、发布 CAS 和删除生命周期版本混用。

| 位置 | 字段与含义 |
| --- | --- |
| `logical_documents` | UUID `logical_document_id`；`knowledge_base_id`、`storage_session_id`；`display_name`；可空 `current_document_id`（Document 外键，删除后 SET NULL）；`version_counter`（空身份为 0）；`state_version`（初始 1）；`enabled`（0/1，默认 1）；创建/更新时间 |
| `documents` | 新增 `logical_document_id` 外键、`version_number`、`content_sha256`、`metadata_sha256`、`auto_publish_version`、`published_at`；原 `id` 仍标识一个具体版本，Chunk 继续关联此 ID |
| 版本约束 | `(logical_document_id, version_number)` 唯一；事务内分配从 1 开始的递增号，删除后不回收。检索元数据快照包括分类、部门、流程类型/状态；文件名不参与显式版本去重 |
| R2 删除任务 | 新增可空 `logical_document_id`、`version_number` 审计快照，无版本/Document 外键；既有删除目标与摘要格式保持兼容 |

`init_database()` 在同一事务中建表、补列、回填身份和索引；每条旧 Document 独立成为 V1，保留原 Document、Chunk ID 和 R1/R2 任务，不合并同名文件。active 且无索引任务或任务 ready/not_requested 的记录设为当前；pending/failed/deleting 不推断发布成功。重复初始化只迁移尚未绑定身份的记录。没有重新生成 Embedding，也没有改写既有 Chroma Metadata；业务库迁移未在本次调研执行，临时旧库重复迁移及外键测试见交付记录。

### 上传与发布规则

`POST /index_document` 新增可选 `logical_document_id`。不传时使用独立文档入口及原同名同内容复用约定，不会隐式加入已有多版本链；传入时必须属于当前上传范围。相同正文哈希和相同检索元数据复用已有版本，文件改名也复用原文件名/Chunk 来源快照；内容或元数据变化创建候选。需要修改既有文档的分类/部门时应明确选择逻辑文档，不就地修改旧版本。

vector 候选确认 ready 后才尝试自动发布；keyword 在文本和 not_requested 任务保存后可发布。`auto_publish_version` 保存创建时的逻辑 CAS，发布成功才递增逻辑 `state_version`；索引失败或发布冲突不改当前指针。ready 但发布冲突的候选可显式发布，不能通过刷新预期版本自动覆盖别人。历史已发布版本不因重复索引隐式回退。

显式发布须满足版本 active、索引 ready/not_requested（vector 模式要求任务 ready；无任务的历史记录须有既有发布时间）。回退复用相同发布接口和 Document ID，不新建版本号；成功后 enabled=1。停用递增逻辑 CAS、设置 enabled=0，保留指针和历史；所有版本立即退出正常检索，随后可显式重新发布有效版本。R2 拒绝直接删除启用状态的当前版本；删除和发布通过同一 SQLite 写事务及状态条件互斥。

### FastAPI 与 React

| 接口 | 请求与返回 |
| --- | --- |
| `POST /logical_documents` | `{ "display_name": "差旅制度" }`，创建空逻辑身份，返回 current_document_id=null、version_counter=0；之后用上传接口写 V1 |
| `GET /logical_documents` | 返回当前公共知识库的逻辑文档列表，不混入普通会话范围 |
| `GET /logical_documents/{identity}` | 返回身份、当前指针、启停/CAS 状态及按版本号倒序的 `versions`，包含索引状态与发布时间 |
| `POST /index_document` | 传 logical_document_id 上传候选；原响应追加逻辑身份、version_number、is_current_version、logical_state_version |
| `POST /logical_documents/{identity}/publish` | `{ "document_id": 12, "expected_state_version": 3 }`，用于候选发布或历史回退，返回更新后的身份及版本列表 |
| `POST /logical_documents/{identity}/disable` | `{ "expected_state_version": 4 }`，停用后返回更新后的身份及版本列表 |

逻辑文档接口要求查询参数 `knowledge_base_id`；写操作还要求 `user_role=kb_admin/admin`。公共版本 Service 拒绝保留值 session_scoped，并校验 `storage_session_id` 为对应知识库内部会话；不提供普通会话跨范围版本操作。UUID 路径、严格正整数 CAS/Document ID 及禁止额外写入字段由 Schema 校验。成功为 200，发布状态/CAS 冲突为 409，角色/保留范围为 403，资源或范围不符为 404，存储状态未确认为 503；参数失败为 400/422。错误使用 `{ "detail": "说明" }`，不返回执行 token。角色仍是演示约定，未实现正式登录和多租户鉴权。

`GET /rag_status/{session_id}` 的每个 Document 追加逻辑身份、版本号、逻辑 CAS、logical_enabled、current_document_id、display_name、published_at 和 is_current_version。列表保留候选/历史/deleting 供管理，`has_document` 表示存在当前生效版本。Repository 在单一读事务中取得文档、当前指针及索引/删除任务，避免同一响应混入两个发布时点。

React 显式选择新建独立文档或已有逻辑身份；选择已有身份时继承分类及未修改的部门/流程字段。文档按身份分组展示当前、候选、历史或停用状态，复用 R1 的查询/继续/重试及 R2 删除确认。索引操作或轮询到 ready 后刷新完整版本状态；发布/停用弹窗固定打开时的 CAS，409 只查询对账，不自动换新 CAS 重发。

### 正常检索与引用持久化

SQLite `current_document_id` + enabled + Document active 是权威条件：关键词读取、向量查询前的 ID 白名单、查询后的 Chunk 回查、上下文及 rag_hits 写入都检查它。Agent 新引用携带 document_id/db_chunk_id，消息写入在短写事务中再次校验；失效引用移除，`rag_citations_invalidated=true` 标记变化，提交后的同一份元数据用于 SSE。已有历史消息不自动删除或重写。

白名单能隔离版本，不能校验历史 Embedding 的语义来源。旧向量保留且不批量重建；历史模型未确认时，同为 1024 维也不代表可混用。R3 浏览器使用 Fake 向量适配器，不替代真实模型兼容性验收。结果见 [R3 交付记录](day27_delivery.md#r3-文档版本管理验收2026-11-04)。
