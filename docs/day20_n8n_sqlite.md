# 第 20 天：n8n 调用结果写入 SQLite

复用第 19 天的提交接口和 Webhook 服务，在 `n8n_execution_records` 中记录申请输入、执行状态和回执。增加按申请编号查询记录的接口，不改变 n8n 模板或成功提交的回执格式。

## 提交和查询

1. 按 `docs/day19_n8n_webhook.md` 配置 n8n Webhook 和 Header Auth。
2. 重启 FastAPI；启动初始化自动新增 `request_id`、`response_status_code` 列及申请编号唯一索引，不需要删除数据库。原表历史数据保留，旧记录 request_id 为空。
3. 显式调用 `POST /process_applications`，请求字段沿用第 19 天格式。建议由客户端生成并保留 request_id。
4. 调用 `GET /process_applications/{request_id}`，查看输入、输出、状态、错误和开始/结束时间。

未传编号时服务端生成 UUID。提交业务错误的响应头包含 `X-Request-ID`，即使配置不完整或超时也可以用它查询。请求校验失败的 422 不创建执行记录。不存在的编号返回 404；编号格式错误返回 422；数据库读取失败返回 503。

查询结果包含：`id`、`request_id`、`session_id`、`workflow_name`、`status`、`response_status_code`、`input`、`output`、`error_message`、`started_at`、`finished_at`。

## 记录生命周期

| 状态 | 含义 |
| --- | --- |
| running | 已提交本地记录，调用尚未完成；也可能因进程中断或结果落库失败而滞留，需要核对 |
| simulated | 收到校验通过、编号匹配的 n8n 模拟回执，并成功写入 SQLite |
| failed | Webhook 配置、网络、HTTP 或回执校验失败；部分情况下下游仍可能已执行 |
| timeout | 等待 Webhook 超时，下游是否完成未知 |

`response_status_code` 保存提交服务对应的 HTTP 返回码（200/502/503/504），不代表 n8n 原始响应状态码。`output` 只保存经过校验的模拟回执；不保存无效响应原文、Token、Webhook URL 或底层异常细节。当前模拟回执没有真实 n8n execution ID，表中的 `execution_id` 不会用模拟工单编号冒充。

先提交 running 记录，再发 HTTP；更新结果使用另一个短事务，网络等待期间不持有 SQLite 写事务。首次创建记录失败时返回 503，不调用 Webhook。收到回执后保存失败时返回 503，记录保持 running，不假装提交成功。开始和结束时间由 SQLite `datetime('now')` 记录为 UTC。

同一 request_id 再次提交一律返回 409，包括并发提交、成功、失败、超时及尚未完成的记录。唯一索引保证重复请求不会再次发送 Webhook；本次不自动重试或自动对账。确认需要发起新的业务申请时使用新编号；不要靠换编号绕过未核对的超时状态。

session_id 可为空。传入未保存的会话编号时创建最小会话记录；已有会话不覆盖。删除会话后执行记录通过外键 SET NULL 保留，原输入 JSON 中仍保留最初的会话编号，便于追溯。

沿用项目当前接口访问方式；查询接口包含申请人及部门等业务信息，部署到多人或公网环境时需要配合项目的用户鉴权和数据权限体系。

## 实现与验证

- `backend/db/process_repository.py`：创建、结束和读取执行记录，使用参数化 SQL。
- `backend/services/process_submission.py`：负责调用顺序、状态记录和重复编号错误映射。
- `backend/api/processes.py`：提交接口及执行查询接口。
- `backend/db/schema.py`：新增列、轻量迁移和唯一索引。
- `backend/db/init_db.py`：初始化事务结束后显式关闭连接，避免 Windows 文件句柄滞留。

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

使用临时 SQLite 和模拟 HTTP 验证实际落库、查询接口、并发重复提交、失败和超时、数据库写入异常及旧库反复迁移；不修改 `data/app.db`。保留 n8n 节点脚本和既有 RAG/字段提取回归测试。当前验证未连接真实 n8n 实例，真实 Webhook 联调仍依赖第 19 天的运行配置。
