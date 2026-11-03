# 第 27 天交付记录

日期：2026-10-27。README、架构与演示资料已整理；**当时完整 Docker 部署验收未完成，受本机 Docker 引擎故障阻塞**。以下原记录保留；2026-11-03 的 R1 原生验收见文末，不能与历史 Docker 结论混用。

## 已交付

- [README](../README.md)：以 React 为主入口，说明知识问答、流程申请、拒答、记录与评测边界。
- [部署说明](deployment.md)：本地与 Docker 两种方式，端口、可选 profile、n8n 地址、持久化、停止与排错。
- [架构与数据流](architecture.md)：部署视图、问答链路、提取/确认/提交/回执时序及当前限制。
- [3 分钟演示脚本](demo_script_3min.md)：三条演示链路、准备材料和异常处理。原有忽略文件 `docs/demo_script.md` 保留，未覆盖个人笔记。
- `Dockerfile.web` 与 Nginx 配置：构建 React，代理同源 `/api`，支持 SPA 刷新和 POST SSE，不自动重试上游请求。
- 根 Compose 默认 backend + web，旧版 Streamlit 与 n8n 分别使用 `legacy-streamlit` 和 `automation`。后端宿主机默认端口从 8000 调整为 18000；容器内部仍使用 8000，保留原数据挂载与 n8n 命名卷。
- Python 镜像改为先安装官方 CPU 版 PyTorch 2.12.0，避免默认拉取 CUDA 运行库；本机 `.env` 未覆盖，示例提供可解析默认值。
- 构建上下文排除本机环境变量、Dify 运行目录、node_modules、本地数据与个人演示笔记；本地评测输出建议保存在忽略的 `eval/local`。
- [显式 Docker 冒烟脚本](../tests/docker_deployment_smoke.py)：使用独立项目和临时数据卷，不加载真实模型/Webhook 配置；当前尚未完成运行验证。

## 实际检查结果

| 检查 | 状态与证据 |
| --- | --- |
| Compose 解析 | `docker compose config --quiet` 通过 |
| 展开配置 | 已检查 profile、回环端口绑定、web 等待 backend 健康、数字默认值及空示例秘密；检查时未打印真实配置 |
| React 镜像 | `docker build -f Dockerfile.web -t enterprise-rag-agent-web:day27 .` 成功；容器内 npm ci、TypeScript 与 Vite 构建完成 |
| 原后端构建 | 安装间接 CUDA 依赖时出现 `[Errno 5] Input/output error`，构建失败 |
| CPU 后端构建 | 官方索引确认存在 Python 3.11 / Linux x86_64 的 2.12.0+cpu wheel；Dockerfile 已调整，但本机引擎无法启动，尚未完成构建 |
| Docker 引擎恢复 | 普通重启和一次针对 Docker 进程的恢复均未解决；最近日志显示 `service containerd failed`、`SIGBUS` panic |
| Nginx/SPA/API/SSE 联调 | 未执行成功，不能标记通过；尚无成功的 `day27_deployment_result.json` |
| 业务回归 | 本轮未修改业务服务逻辑；最近第 26 天的 48 项测试通过，不能将其当作 Docker 部署测试 |
| 文档与脚本 | 本地链接、Markdown 代码块、Python 脚本语法已检查 |

构建时 npm 审计还报告 10 项依赖提示（1 中、9 高）；本次未自动升级依赖，也未宣称安全审计通过。需要另行检查具体影响及兼容升级方案。

## 受阻时保留了什么

未执行工厂重置、全局镜像/卷清理或业务数据删除；未启动根项目的 backend/web 去替换现有服务。仅启动并尝试恢复已安装的 Docker Desktop，未终止 Ubuntu 发行版。引擎故障期间 Docker 依赖的 Dify/n8n 服务不可据此认定可用，后续需重新确认状态。

用户反馈错误持续出现后，已关闭本轮启动的 Docker Desktop 及其 backend/build 进程，并确认这些进程停止，不再自动重启或重试构建。保留所有资料，待 Docker 环境恢复后再继续验收。

引擎日志的启动失败原因属于 containerd 崩溃，现有信息不足以确定具体磁盘或底层故障。不能把它简单归因于模型 API、前端代码或已证实的磁盘空间不足。没有执行可能丢失原容器数据的重置操作。

## 恢复后的验收步骤

先确认 `docker info` 正常，再继续一次构建与隔离验证，不反复请求已知失效的引擎：

```powershell
docker build -f Dockerfile.backend -t enterprise-rag-agent-backend:day27 .
docker build -f Dockerfile.web -t enterprise-rag-agent-web:day27 .
.venv\Scripts\python.exe tests/docker_deployment_smoke.py
```

脚本需要支持 `!override` 的 Compose 2.24.4+，默认使用独立的 18020/18021 端口；若被占用，可用 `--backend-port` / `--web-port` 指定空闲端口。只清理自己新生成的唯一测试项目与临时卷，不清理原项目。

通过后会记录 Nginx 配置、SPA 路由、真实 FastAPI 健康与空 SQLite、JSON 404、POST SSE 分段到达、构建上下文排除和测试资源清理结果。SSE 检查使用测试上游，不调用真实模型；它不等于重新验证模型质量或实际 n8n 流程。

恢复 Docker 后还需单独确认已有 n8n 工作流发布、模型配置和业务数据保留情况，之后按 [3 分钟脚本](demo_script_3min.md) 演练。完整部署验收通过前，第 27 天不标记为整体验收完成。

## R1 原生环境最终验收（2026-11-03）

代码提交：阶段 A `1d2286f`（任务持久化）、阶段 B `a62402f`（执行与恢复）、阶段 C `389eb07`（人工恢复、FastAPI 与 React 接入）。D 阶段仅使用隔离测试数据验证，未修改业务代码、Router 或历史评测金标。

| 项目 | 实际结果与边界 |
| --- | --- |
| D1 真实 Embedding | 使用项目现有 `openai` 兼容配置、`qwen3.7-text-embedding-flash`；单条请求返回 1024 维有效非零向量，临时 SQLite 中 Profile 绑定、摘要/快照校验、同值幂等及不同维度拒绝覆盖通过；不访问业务 Collection |
| D2 业务只读检查 | 通过 SQLite 只读方式确认 `rag_chunks` 存在，维度 1024、距离度量 l2；历史模型来源无法确认，语义兼容性未评估 |
| D2 隔离索引/检索 | 专用 Collection + 临时 SQLite，现有 Service/Adapter 真实执行后 ready；文档、查询各一次 Embedding，召回 chunk-1，得分 0.7376；重复 upsert ID/正文/Metadata 不变，数量 1→1；测试 Collection 和临时数据已清理 |
| D3 真实浏览器 | 本机 Python FastAPI 18931 + Vite 18932，内置浏览器上传差旅制度到专用知识库；后端进程固定隔离存储路径和 Collection，禁止访问业务库；不是直接在原业务页面上传 |
| D3 索引与 UI | 一份文档、一个 Chunk，SQLite ready、实际向量维度 1024；React 显示“文本已保存 / 索引成功”，刷新仍成功；新进程核验 Chroma 持久化向量、稳定 ID 和 Metadata 通过 |
| D3 真实问答 | 通过现有 Agent 问答流程，实际 vector 检索命中 `差旅报销制度.md#chunk-1`，得分 0.7583（阈值 0.6）；答案及命中记录落库。回答说明制度未列明具体材料清单，没有补造细节；本轮 Embedding 共 2 次 |
| 后端回归 | 此前完整后端 227/227；D3 本轮复跑 API、Service、恢复及路由边界共 71/71，未重复全量测试；使用 Fake/临时数据并禁止真实向量调用 |
| 前端回归 | D3 本轮 25/25；TypeScript、ESLint、React 生产构建均通过；包含状态展示、轮询、显式重试、202/409、未知结果和取消/旧响应保护 |
| 离线回归 | D3 本轮 30 题：文件检索 20/20、规则路由 30/30、流程意图 30/30、异常 0；答案引用、关键词事实覆盖与拒答生成质量未评估，不能用历史 live 指标代替 |
| 范围与清理 | 本轮 281 份已有项目文件摘要未变；只清理专用测试 Collection、SQLite/临时目录及自己启动的前后端，保留用户业务服务；未运行 Docker |

D1 曾受 `Arrearage`、`AllocationQuota.FreeTierOnly` 阻塞；更新项目实际模型配置后真实调用通过。浏览器验收也曾因工具无法确认 URL 暂停，后在内置浏览器控制可用后完成；此前受阻结果未当作通过项。

R1 核心链路验收完成，不代表历史向量已兼容新模型、历史文档已全部建向量，或 Docker/n8n/Dify 在本轮通过验收。人工 recover 仍要求旧执行者真实停止；R1 未扩展到删除一致性、文档版本管理、正式认证与多租户权限。接口及操作方式见 [数据协议](data_contract.md#6-r1-索引任务与管理接口)、[原生部署说明](deployment.md#r1-向量配置与恢复)。
