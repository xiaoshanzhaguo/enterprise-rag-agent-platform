# 第 27 天交付记录

日期：2026-10-27。README、架构与演示资料已整理；**完整 Docker 部署验收未完成，受本机 Docker 引擎故障阻塞**。

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
