# 第 19 天：FastAPI → n8n Webhook 模拟申请

## 已交付

- `POST /process_applications`：显式提交确认后的申请字段，不在第 18 天字段提取成功后自动提交。
- `integrations/n8n/process_application.json`：可导入工作流，默认未发布，不包含凭据或秘密。
- 流程：Receive Application → Validate Application → Simulate Table Row → Simulate Notification → Simulate Ticket → Respond to FastAPI。
- Docker Compose 的可选 `automation` profile，持久化 n8n 配置和执行记录。

表格记录和通知只生成模拟节点输出，工单编号以 `SIM-` 开头。不会连接真实表格、发送真实通知或创建外部工单，也不代表审批通过。可在 n8n 执行详情里查看每个节点的输入输出。

## 本地启动与配置

1. 启动 Docker Desktop，确认 Docker Engine 可用。在项目根目录执行：

   ```powershell
   docker compose --profile automation up -d n8n
   ```

2. 打开 `http://localhost:5678`，完成 n8n 本地账号初始化。从工作流菜单选择 **Import from File**，导入 `integrations/n8n/process_application.json`。
3. 打开 **Receive Application** 节点，保留 **Header Auth**。创建 Header Auth 凭据：Name 为 `X-Webhook-Token`，Value 为自行生成的随机秘密。将凭据绑定到该节点。秘密不写入工作流 JSON 或 Git。
4. 发布/激活工作流，使用该节点显示的 **Production URL**。测试 URL 需要先点击 **Listen for test event**，不能作为长期运行地址。响应模式已设为 **Using Respond to Webhook Node**，在流程结束时返回实际模拟回执。
5. 在本地 `.env` 填写并重启 FastAPI：

   ```dotenv
   N8N_WEBHOOK_URL=http://127.0.0.1:5678/webhook/process-application
   N8N_WEBHOOK_TOKEN=与HeaderAuth凭据一致的秘密
   N8N_WEBHOOK_TIMEOUT_SECONDS=10
   ```

   如果 FastAPI 也在当前 Compose 网络内运行，URL 使用 `http://n8n:5678/webhook/process-application`，然后重新创建 backend 容器以更新环境变量：

   ```powershell
   docker compose up -d --build backend
   ```

n8n 镜像默认使用 `n8nio/n8n:stable`；需要固定部署版本时可将标签替换为自己验证过的版本。端口只绑定本机。沿用项目现有 FastAPI 接口访问方式，本次没有增加面向公网的用户鉴权体系。

## 提交与查看结果

在 FastAPI `/docs` 中调用 **POST /process_applications**。也可以执行：

```powershell
$applicationBody = @{
    request_id = [guid]::NewGuid().ToString()
    session_id = 'demo-session'
    fields = @{
        application_type = 'VPN权限'
        applicant = '张三'
        department = '研发部'
        permission_level = '只读'
        approver = '李经理'
        additional_info = '用于远程办公'
    }
} | ConvertTo-Json -Depth 4
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/process_applications' -Method Post -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($applicationBody))
```

`fields` 可来自上一天的 `metadata.process_extraction.fields`，确认并补齐后提交，不应直接发送整个提取结果。必填 `application_type`、`applicant`、`department`；其余字段允许 null。省略 request_id 时服务端生成 UUID；自行传入时可用于核对执行记录。字段提取接口保持原样，本次没有增加前端提交按钮。

FastAPI 发送的 Webhook body 包含 `event: "process_application.submitted"`、`request_id`、`session_id` 和 `fields`。成功回执示例：

```json
{
  "request_id": "00000000-0000-4000-8000-000000000001",
  "status": "simulated",
  "ticket_id": "SIM-00000000-0000-4000-8000-000000000001",
  "actions": ["table_row_simulated", "notification_simulated", "ticket_simulated"]
}
```

在 n8n **Executions** 中核对同一个 request_id，查看模拟表格行、模拟通知文案和模拟工单输出。

## 错误及验证范围

| HTTP 状态 | 含义 |
| --- | --- |
| 422 | 申请字段不完整、类型错误或包含额外字段；不发送 Webhook |
| 503 | Webhook URL、Token 或超时配置不完整/不合法 |
| 504 | Webhook 超时，下游可能已执行，需要先核对 request_id |
| 502 | 网络失败、HTTP 错误、非预期 JSON 或回执编号不匹配 |

不自动重试，不跟随重定向，不返回含 Token 或 Webhook URL 的底层错误。request_id 目前用于关联和核对，不提供持久化去重；不要将重复提交当作安全重试。真实表格或工单接入时应另行实现幂等记录和权限控制。

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
docker compose --profile automation config --quiet
```

离线测试覆盖 HTTP 请求契约、鉴权头、错误映射、回执校验、字段校验，以及工作流连接关系和 Code 节点 JS 执行。当前开发环境 Docker Engine 未运行，未执行 n8n 真实导入、凭据绑定及端到端联调；上述步骤需在可运行的 n8n 实例上完成。

官方参考：[Webhook](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/)、[Respond to Webhook](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.respondtowebhook/)、[Docker 安装](https://docs.n8n.io/hosting/installation/docker/)。
