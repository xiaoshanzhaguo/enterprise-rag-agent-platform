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

## R2 单文档删除验收（2026-11-04）

范围保护独立提交 `508463a`；单文档删除与向量一致性实现、最终审查修复提交 `30fb954`。本节补充 R2，不覆盖前文 R1 及历史 Docker 记录。

已交付 active/deleting 生命周期、无 Document 外键的 `document_deletion_tasks`、Repository/Service/Adapter 分层、精确 Vector ID 删除与核验、索引领取互斥、CAS/token、幂等重试和受控 CLI 恢复。React 已提供二次确认、查询、失败提示、显式继续/重试和完成后刷新。旧清空接口在 API 与共享入口拒绝公共知识库内部 session；未新增批量清空。

| 验证 | 实际结果与边界 |
| --- | --- |
| 后端实现阶段 | 完整回归 262/262；包含 R2 删除专项 28 项及既有 R1、范围保护、RAG/Agent 测试，均使用临时数据/Fake；后续新增最终查询地址用例后未重复全量运行 |
| 前端实现阶段 | 完整前端 34/34；后续新增两个跨控制器竞争用例，不能将 34 项当作最终版本全量测试数 |
| 最终审查修复后 | 受影响后端 58/58（删除 29、索引 API 22、范围保护 7）；前端删除与索引状态相关 28/28（删除 11、索引 17）；TypeScript、ESLint、生产构建和暂存区 diff 检查通过 |
| 故障与并发 | 覆盖部分删除、多批次失败、确认丢失、Chroma 成功而 SQLite 删除/任务确认失败、重复删除、领取失败、显式重试、旧 token/CAS、索引 claim/retry 竞争、删除后旧索引不能重新执行、孤立向量与日志外键保护 |
| 真实浏览器与 Chroma | 原生 FastAPI 18931 + Vite 18932，独立测试知识库、SQLite 与 Collection；使用确定性 3 维测试向量，不调用 Embedding，不验证模型质量 |
| 删除链路 | 两份测试文档、各两个 Chunk；对目标实际删除一条向量后注入异常，UI 显示删除失败；刷新恢复 deleting/failed，只有另一份文档的 Chunk 处于活动范围；显式重试后任务 completed、attempt_count=2、目标向量剩余 0，另一份文档及其两条向量保留 |
| 检索集成 | 隔离真实 Chroma 接受活动文档白名单过滤；以确定性查询向量执行现有检索，只召回保留文档的两个 Chunk |
| 离线回归 | 原 30 题：检索 20/20、规则路由与流程意图 30/30、异常 0；答案引用、事实覆盖和拒答生成未评估，历史报告/金标未改 |
| 最终审查问题 | P2：在途旧列表 GET 被完成回调复用；现已在删除通知时使旧请求失效并追加最新读取，两个 Fake 跨控制器用例通过。P3：查询地址缺少范围/角色；现已验证 200/202/502/503 API 回执的 status_url/Location 可直接查询，含编码字符的知识库范围和 retry 回执也通过 |
| 清理与安全 | 本次测试服务、测试 Collection 和临时数据均清理；真实浏览器验收发生在最终两项审查修复前，修复后采用上述专项回归验证，未重复浏览器全链路；未操作业务 Chroma、未调用真实模型、未依赖 Docker |

claimed 任务中断后须由操作人员确认旧执行者全部停止，再经 CLI 释放执行权并显式 retry；不因超时自动接管。角色校验仍为项目既有演示机制。R2 不支持撤销、批量删除或文档版本切换；历史 Embedding 模型来源未确认的限制继续保留。协议、分层与恢复操作分别见 [数据协议](data_contract.md#7-r2-单文档删除与生命周期)、[架构](architecture.md#r2-单文档删除)、[部署说明](deployment.md#r2-删除任务与人工恢复)。

## R3 文档版本管理验收（2026-11-04）

实现及最终审查修复提交 `f450739cb0a3e28a55ee6ada97744eb5d36b2bf1`，提交说明 `feat: 增加知识库文档版本管理`。包含原 24 个 R3 文件，以及审查修复必需的 Agent 引用和前端索引控制器两个依赖，共 26 个文件。R1、R2 与前文 Docker 记录保留，不将它们的验证范围合并为 R3 真实模型验收。

| 验证 | 实际结果与边界 |
| --- | --- |
| 数据结构与迁移 | logical_documents 管理身份及当前指针，documents 保留具体版本与内容/元数据摘要；临时旧 SQLite 表结构迁移、重复初始化、外键和稳定 ID 验证通过，同名历史文档独立成为 V1；未迁移用户业务数据库 |
| 上传与发布 | 显式选身份创建 V2/V3，相同内容/元数据及改名复用，元数据变更保留旧快照；事务分配版本号、索引失败保留旧版、ready 自动发布、发布 CAS 冲突保留候选、回退不增加版本号 |
| R1/R2 兼容 | 每版本使用原有 R1 任务、稳定 Chunk/Vector ID 及重试；当前版本删除保护、历史删除、停用、删除/发布竞争、索引领取互斥和版本号不回收均覆盖；R2 删除任务保留版本审计与失败恢复 |
| 检索与引用 | 关键词、向量 SQLite 白名单与回查、上下文、rag_hits 及新消息引用校验；覆盖候选/历史/deleting/停用排除，引用构造后发生切换时在消息写事务中再校验，SSE 与持久化回执一致 |
| 实现阶段浏览器 | 原生 FastAPI 18931 + Vite 18932，独立临时 SQLite 和 Fake Embedding/Vector Store；页面上传 V3、显示索引成功并自动发布、回退 V1、刷新恢复、停用及重新发布通过 |
| 检索预览 | 临时环境走真实 keyword 检索代码，仅返回新发布 V3 的差旅材料片段；不是生成答案质量评测，也不是实际 Embedding/Chroma 测试 |
| 最终审查修复 | 公共版本 API 拒绝 session_scoped 并核对存储会话；ready 后整组刷新；Agent/消息写入再次过滤引用；管理状态改为同一 SQLite 读快照。补充 API 范围、真实线程竞争、引用写入锁及前端迟到响应用例 |
| 最终后端 | 完整 308/308，其中 R3 专项 45 项；使用临时 SQLite/Fake，审计保护禁止业务数据库、真实 Chroma 和模型访问 |
| 最终前端 | 完整 49/49，其中版本专项 13 项；TypeScript、ESLint、React 生产构建通过，git diff --check 通过 |
| 验证时点 | 浏览器验收发生在最终审查修复前；修复后采用上述专项和完整回归，未重复浏览器全链路；本次 2026-11-05 文档收尾仅核对代码与既有记录，未重跑测试/质量评测 |
| 清理与安全 | 本轮测试服务、测试标签页及临时数据已清理；未使用 Docker，未调用真实模型，未操作业务 Collection、历史评测金标或真实知识库文档 |

历史向量继续保留；SQLite 当前版本白名单解决检索范围，不证明历史模型与当前 Embedding 的兼容性。首次 R3 后端启动会应用兼容迁移，实际业务库应先备份；本次未以业务库做开发测试。R3 不包含 Hybrid、Rerank、生成答案质量升级或完整身份认证，工程回归通过不能替代检索效果指标。

协议与架构分别见 [R3 数据契约](data_contract.md#8-r3-逻辑文档与版本管理)、[版本发布](architecture.md#r3-逻辑文档与版本发布)。R4 的只读检索与评测调研集中在 [现状与下一步](architecture.md#r4-检索质量现状与下一步2026-11-05)，不另建重复方案；当前 HEAD 的新检索质量基线尚未执行。
