# Dify 简化知识库问答

本目录对应第 22 天：在独立 Dify 实例中复刻小规模企业制度问答，不替换原 FastAPI 系统，也不调用原系统检索接口。

## 本机部署

项目根目录执行：

```powershell
.venv\Scripts\python.exe integrations/dify/prepare_local.py --start
```

脚本固定使用官方 Dify **1.18.0**，下载官方 Docker 部署文件并核对 Git blob 哈希，创建随机本机秘密，使用独立 Compose 项目 `enterprise-dify-day22`。官方文件、PostgreSQL 等绑定数据和本机配置保存在 `.runtime` 中，不提交 Git；插件数据使用项目内独立 Docker 命名卷，以支持 Windows 下的 Linux 文件重命名操作。

需要 Docker Desktop 的 Linux 引擎及 Docker Compose 2.24.4+。默认入口为 `http://localhost:18080`，只绑定本机，不占用原系统的 18000 或 n8n 的 5678。仅配置原生知识库问答所需组件，未启用新版 Agent 执行环境。重复运行脚本不会重置现有随机秘密。

镜像启动后还需等待 API 完成初始化，`/console/api/setup` 返回 200 后再打开页面。停止时不要使用 `down -v`，以免清空命名卷；本任务不要求删除任何现有数据。

## 知识库内容

复用 `docs/sample_knowledge_base` 中的四份虚拟企业文档：

| 文件 | 场景 |
| --- | --- |
| `hr/leave_policy.md` | 年假、病假和审批要求 |
| `finance/reimbursement_policy.md` | 报销时限、材料和金额规则 |
| `it/gitlab_access_request.md` | GitLab 权限和审批角色 |
| `it/vpn_troubleshooting.md` | VPN 720 故障排查 |

文件清单和 SHA-256 见 `documents.json`。源文件保持不变，不把真实企业材料上传到演示知识库。

本次选择 Dify 原生 Economy 索引，用关键词检索做简化基线，不要求额外 Embedding 服务；生成模型单独配置。Prompt 位于 `system_prompt.txt`，要求只根据检索资料回答、保留限定条件、无依据时明确拒答，不执行流程申请。

## 复用应用

已从本机实际运行的应用导出 `enterprise_knowledge_chatbot.yml`（DSL 0.7.0）。本次模型为 DeepSeek `deepseek-flash`，温度为 0，开启引用，检索 top_k 为 3。导入其他 Dify 实例时需要重新绑定本机知识库和模型供应商；DSL 中的知识库 ID 仅对应本机实例，导出文件不包含知识库文档或索引。

本机已发布的演示入口：[企业制度简化问答](http://127.0.0.1:18080/chatbot/2MDrJdPnndQQRPJZ)。控制台入口为 [Dify](http://localhost:18080)。初始化管理员信息保存在本机 `.runtime/admin-access.json`，仅供本机查看，不提交或分享。模型密钥、控制台登录信息和应用 API Key 都不随 DSL 提交。

如为新部署，在 Dify 初始化管理员后：

1. 从官方 Marketplace 安装 DeepSeek 模型供应商，并配置有效的模型 API Key。
2. 创建知识库，选择 Economy 索引，导入表中的四份文档，等待所有文档状态为可用。
3. 在工作室选择“导入 DSL 文件”，导入 `enterprise_knowledge_chatbot.yml`，重新选择刚创建的知识库并确认模型可用。也可创建基础 Chatbot，关联知识库，填入 `system_prompt.txt`，启用引用来源。
4. 用 `eval_cases.jsonl` 中的问题测试，确认答案数值和审批角色与原文一致。
5. 启用应用 Web 页面及 API，获取应用 API Key，保存在本目录 `.env.local`。

## 真实 API 验收

复制 `.env.example` 为 `.env.local`，填写 Dify 应用 API Key 后运行：

```powershell
.venv\Scripts\python.exe integrations/dify/run_eval.py
```

脚本实际调用 Dify `/v1/chat-messages`，保存回答、检索来源和关键词检查结果到 `acceptance_result.json`。每条问题开启独立对话，避免历史串入。没有配置 API Key 或请求失败时不会生成“已完成”的假报告。关键词检查只作为验收辅助，还需要人工核对答案与来源。

报告是上次成功执行的快照：本次执行失败时旧报告仍会保留，判断当前运行结果应同时查看退出码和报告时间。当前 5 条用例覆盖年假、报销、GitLab 权限、VPN 排错和无依据拒答；它们用于第 22 天功能验收，不代表完整评测指标。Economy 检索可能附带不相关来源，不能将“正确来源在结果中”直接解释为所有引用均准确。

页面验收记录见 [第 22 天验收说明](../../docs/day22_dify_acceptance.md)。本次应用只做知识问答；流程申请继续通过原系统与 n8n 完成。

参考：[官方 Docker 部署](https://docs.dify.ai/en/self-host/deploy/quick-start/docker-compose)、[Dify 1.18.0 源码](https://github.com/langgenius/dify/tree/1.18.0/docker)。
