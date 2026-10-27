# 启动、部署与排错

默认交付 React + FastAPI；n8n 和旧版 Streamlit 按需启用，Dify 独立部署。以下命令都在仓库根目录执行，除非特别注明。

## 1. 准备配置

已有 `.env` 时保留，不要用示例覆盖真实配置：

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

填写 `DEEPSEEK_API_KEY`、`BASE_URL`、`LLM_MODEL`。示例为官方 DeepSeek 接口，兼容服务的地址与模型名由该服务决定。历史基线使用 `deepseek-chat`；示例模型名称变化不代表旧指标已对新模型验证。官方接口参数见 [DeepSeek 文档](https://api-docs.deepseek.com/)。

先以 keyword 模式启动可避免首次下载 Embedding 权重。切换 vector 前配置 `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL`，必要时配置云端 Embedding 的地址与 Key；已有关键词索引并不自动代表向量库完整，需重新索引相应文档。

## 2. 本地开发

后端：

```powershell
.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 18000
```

前端另开终端：

```powershell
cd frontend-react
npm ci
npm run dev
```

访问 Vite 打印的地址。`frontend-react/.env.local` 可填写 `BACKEND_PROXY_TARGET=http://127.0.0.1:18000`；它只控制开发代理。生产镜像使用固定的同源 `/api`，不将模型 API Key 传给浏览器。

健康检查 `GET /health` 只检查数据库与存储可用性，不证明模型凭据有效、模型回答正确或 n8n 工作流已发布。模型调用和流程触发分别按演示链路验证。

## 3. Docker 主入口

需要 Docker Linux 引擎与 Compose。新增 `Dockerfile.web` 使用 Node 22 构建 React，再由 Nginx 提供页面；后端使用 `Dockerfile.backend`。两个 Python 镜像先从官方 CPU 索引安装 `torch==2.12.0+cpu`，避免间接下载 CUDA 运行库，未配置 GPU 部署。安装方式参考 [PyTorch 官方 CPU 安装说明](https://pytorch.org/get-started/locally/#no-cuda)。首次下载 Python、CPU 运行库和基础镜像仍可能较久，先查看构建或容器日志。

```powershell
docker compose config --quiet
docker compose up -d --build backend web
docker compose ps
```

| 服务 | 宿主机入口 | 容器内部地址 |
| --- | --- | --- |
| web | `http://127.0.0.1:8080` | 80 |
| backend | `http://127.0.0.1:18000` | `http://backend:8000` |
| n8n（automation） | `http://127.0.0.1:5678` | `http://n8n:5678` |
| frontend（legacy-streamlit） | `http://127.0.0.1:8501` | 8501 |
| Dify（独立项目） | `http://127.0.0.1:18080` | 由 Dify 自己的 Compose 管理 |

web 等待 backend 健康检查通过后启动。Nginx 将 `/api/health` 转发为后端 `/health`，SPA 路由如 `/logs` 刷新时回退到 `index.html`。SSE 使用 HTTP/1.1 并关闭代理响应缓冲，避免等待整段内容后才显示；读取超时设为 180 秒，代理关闭自动上游重试。配置见 [nginx.conf](../deployment/nginx.conf)，相关行为参考 [Nginx 官方代理文档](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_buffering)。

可在 `.env` 覆盖 `WEB_PORT`、`BACKEND_PORT`、`STREAMLIT_PORT`。容器内地址仍不变。不要把宿主机的 18000 端口写成 `http://backend:18000`。

修改后端模型、Webhook 或其他环境变量后，重新创建 backend，并同步重新创建 web 使代理重新解析后端地址：

```powershell
docker compose up -d --build --force-recreate backend web
```

不会自动导入业务文档、初始化 n8n 凭据或创建 Dify 应用；按以下对应步骤完成。

## 4. n8n 模拟流程

```powershell
docker compose --profile automation up -d n8n
```

导入 [process_application.json](../integrations/n8n/process_application.json)，在 Receive Application 绑定 Header Auth 凭据，Header 名为 `X-Webhook-Token`，值自行设置；发布工作流后使用 Production URL。模板没有凭据，导入不等于已可调用。

| FastAPI 运行位置 | `.env` 中的 N8N_WEBHOOK_URL |
| --- | --- |
| 宿主机 Python | `http://127.0.0.1:5678/webhook/process-application` |
| 根 Compose 中 backend | `http://n8n:5678/webhook/process-application` |

`N8N_WEBHOOK_TOKEN` 与 Header Auth 值一致。宿主机运行时重启 uvicorn；Docker 运行时按上一节重新创建 backend/web。完整节点配置见 [第 19 天说明](day19_n8n_webhook.md)。

流程提交成功只表示模拟回执已落库。遇到 502/504，先用申请编号查日志并核对 n8n Executions，不要反复点提交或换编号重发。

## 5. 可选入口

旧版 Streamlit：

```powershell
docker compose --profile legacy-streamlit up -d --build frontend
```

服务保留旧名称 frontend，使用 `http://backend:8000` 访问后端，不作为 React 的容器代理。profiles 的启动规则可参考 [Compose 官方说明](https://docs.docker.com/compose/how-tos/profiles/)。

Dify：

```powershell
.venv\Scripts\python.exe integrations/dify/prepare_local.py --start
```

固定官方版本的部署准备脚本启动独立项目，之后仍需完成管理员、模型、文档导入与 DSL 绑定。按 [Dify README](../integrations/dify/README.md) 操作，不将其运行目录复制到根应用镜像。

## 6. 持久化与停止

- SQLite、Chroma 与后端模型缓存挂载到宿主机 `./data`，容器内为 `/app/data`。先停止使用该目录的服务，再复制目录做备份；不要在仍有写入时直接拷贝 SQLite 文件作为一致性备份。
- n8n 运行数据使用原来的 `n8n_data` 命名卷，模板 JSON 不包含已有运行数据或凭据。
- Dify 的绑定目录与插件卷由其独立项目管理，根 Compose 不清理它。
- `.env`、前端 `.env.local`、Dify `.runtime`、本地评测输出不提交 Git，也被排除在根 Docker 构建上下文之外。

停止本次前后端而保留数据及其他服务：

```powershell
docker compose stop backend web
```

如需停止可选服务，显式指定 `n8n` 或 `frontend`。本说明不使用 `down -v` 清空卷。旧版 Streamlit 原来已经运行时，默认启动新主入口不会主动停止它，可自行执行 `docker compose stop frontend`。

## 7. 常见问题

| 现象 | 检查与处理 |
| --- | --- |
| Docker Linux engine 管道不存在 | 先打开 Docker Desktop，等待 `docker info` 成功，再运行 Compose |
| Windows 10013 | 检查端口占用和系统保留范围：`netsh interface ipv4 show excludedportrange protocol=tcp`；本项目本地后端默认使用 18000 |
| React 502 / 未连接 | 本地检查 `BACKEND_PROXY_TARGET`；Docker 检查 `docker compose logs --tail 80 backend web` 与 backend 健康状态 |
| 页面刷新 `/logs` 404 | 确认使用本仓库 Nginx 的 SPA fallback；Vite 的开发代理不能代替生产代理 |
| 回答一直等待 | 检查模型配置、上游连通性及代理日志；健康检查成功不代表模型服务可用 |
| n8n 502 / 504 | 检查运行位置对应 URL、生产 Webhook、Header Auth、响应节点；先按编号核对，不自动重发 |
| vector 首次索引很慢 | 检查权重下载；可先用 keyword 模式完成演示，不能把回退结果称作纯向量检索 |

本轮实际构建、接口与代理验证结果见 [第 27 天交付记录](day27_delivery.md)。
