# 第 18 天：流程字段提取

Agent 的 `process_apply` 和 `finance_process` 路由在“生成回答”阶段输出经过 Pydantic 校验的 JSON。其他路由保留原回答方式。明确的 VPN、账号和权限申请优先归入流程申请，故障排查仍归 IT 支持。

示例输入：张三，研发部，申请 VPN 只读权限，用于远程办公，审批人李经理。

```json
{
  "fields": {
    "application_type": "VPN权限",
    "applicant": "张三",
    "department": "研发部",
    "permission_level": "只读",
    "approver": "李经理",
    "additional_info": "用于远程办公"
  },
  "missing_fields": [],
  "status": "extracted",
  "error": null
}
```

字段键分别表示申请类型、申请人、部门、权限级别、审批人和补充信息。只从本次输入提取，不从知识库或历史消息推断申请信息。未提供的字段为 null，空白值归一为 null；`missing_fields` 由后端根据实际字段重新计算，表示未提供的字段，不代表每一种申请都必须填写这些字段。

模型输出为非法 JSON、字段类型错误、含未知字段或调用失败时，返回 `status: "failed"`、全 null 字段和明确错误提示，不把失败当作提取成功。允许缺省字段和常见 JSON Markdown 围栏。这里只校验结构和类型，提取内容的语义准确性仍需真实模型评测及用户确认。

实现入口：

- `backend/schema/process_schema.py`：固定字段和结果协议。
- `backend/services/process_extraction.py`：复用第 16 天模板，调用模型并校验结果。
- `backend/services/agent_service.py`：SSE `generate_answer` 输出 JSON 字符串；最终事件和历史消息的 `metadata.process_extraction` 保存相同的结构化对象。现有分步骤外层 JSON 协议保持兼容。
- `backend/services/agent_router.py`：关闭 RAG 时仍进行业务分类，但强制禁用检索。因此关闭 RAG 的 Agent 请求也会有一次分类模型调用。

知识库没有命中时仍可提取用户明确提供的信息；这不表示已确认企业规则、已提交申请、审批通过或权限生效。若开启 RAG，既有检索步骤和日志继续保留。前端可在现有最终回答中查看 JSON，无需增加新的步骤事件。

离线测试：

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

测试使用模拟模型及存储，覆盖完整和缺失字段、异常响应、RAG 关闭时的路由、知识库无命中时的提取、SSE 和消息保存，不访问真实模型或业务数据库。
