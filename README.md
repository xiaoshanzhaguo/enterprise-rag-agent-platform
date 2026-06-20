# 基于 RAG 的企业知识库问答与评测平台

> 面向企业内部文档的知识库问答 Agent，支持文档上传、语义检索、引用回答、无依据拒答、会话持久化和评测集验证。项目采用 Streamlit + FastAPI + SQLite + ChromaDB 的前后端分离架构，形成从文档入库、检索问答到效果评测的完整闭环。

---

## 项目定位

本项目定位为一个 **企业知识库问答 Agent**，核心目标是让用户可以上传企业内部文档，并围绕文档内容进行可追溯、可解释、可评测的问答。

它不是一个只会调用大模型接口的聊天页面，而是围绕真实 RAG 应用链路实现了：

- 文档上传与文本切块
- Embedding 生成与 ChromaDB 向量检索
- SQLite 持久化会话、消息、文档、chunk、检索记录
- 带来源引用的回答
- 检索不到依据时明确拒答
- 前端展示引用来源、命中片段和检索原因
- Eval 评测集量化验证 RAG 效果
- Agent 路由决策判断问题是否需要知识库，并自动改写检索 query

除企业知识库问答主流程外，系统还提供内容分析、结构优化、风格改写、多版本生成和工作流优化等附加文本处理能力，用于覆盖知识库问答之外的常见文本处理场景。

---

## 核心功能

### 1. 企业知识库问答

用户可以上传一份或多份企业文档，例如员工手册、制度说明、项目文档、FAQ 等，然后直接围绕文档提问。系统会判断问题是否需要知识库，并在需要时改写检索 query 后执行 RAG 检索。

示例：

```text
试用期是多久？
```

回答示例：

```text
根据星辰科技有限公司的企业管理制度，普通岗位试用期为3个月，管理岗位试用期为6个月。[来源: 星辰科技有限公司企业管理制度（2026版）.md#chunk-36]
```

### 2. 文档上传与切块

前端支持在聊天输入框中附加一份或多份文档，后端会提取文本、按章节和段落切分 chunk，并保存到 SQLite 数据库。同一会话可以持续追加文档，后续问题会在当前会话已保存的文档集合中检索。

每个 chunk 会记录：

- `document_id`
- `file_name`
- `chunk_id`
- `text`
- `text_length`
- `created_at`

### 3. Embedding + ChromaDB 向量检索

项目支持基于 Embedding 的语义检索，并使用本地 ChromaDB 持久化向量数据，默认目录为：

```text
data/chroma/
```

向量库 metadata 会保存：

- `session_id`
- `document_id`
- `db_chunk_id`
- `file_name`
- `chunk_id`

同时保留关键词检索作为 fallback，可通过配置切换检索方式。

### 4. 参考依据与命中片段展示

RAG 回答会在页面正文中保留来源标记，前端会在回答下方提供默认折叠的“参考依据”区域。展开后可以看到证据卡片、检索详情和完整原文，方便用户判断模型回答是否有依据。

展示内容包括：

- `rank`：命中排序
- `score`：相似度或关键词得分
- `file_name`：来源文件名
- `chunk_id`：命中文本块编号
- `text_preview`：命中内容预览
- `retrieval_mode`：实际检索方式，如 `vector`、`keyword`、`no_hit`

复制答案时，系统会自动去掉 `[来源: ...]` 这类内部引用标记，让复制出去的正文更自然；页面展示和参考依据区域仍保留引用信息，便于追溯。

### 5. 无依据拒答

当知识库中没有可靠依据时，系统会明确返回：

```text
知识库中没有找到依据。
```

这种设计可以避免模型在企业知识库场景中凭空编造答案，更符合严肃业务场景下的问答要求。

### 6. RAG 可解释面板

前端会通过“参考依据”折叠面板展示本轮问题命中的 top_k 片段、分数、来源文件、chunk 编号和原文预览。后端也会记录检索过程，便于后续调试和展示。

相关数据库表：

- `rag_queries`
- `rag_hits`

### 7. 聊天会话持久化

聊天会话和消息会写入 SQLite，默认数据库文件为：

```text
data/app.db
```

前端侧边栏支持：

- 新建会话
- 查看最近 10 条会话
- 点击历史会话恢复消息
- 当前会话高亮
- 删除单条历史会话
- 回复完成后自动刷新历史列表

### 8. Eval 评测集

项目内置轻量评测集，用于量化 RAG 效果。

评测相关文件：

```text
eval/questions.jsonl
eval/run_eval.py
eval/report.md  # 运行评测后生成
docs/eval_report_sample.md  # 可提交的评测报告样例
```

运行命令：

```bash
python eval/run_eval.py
```

评测报告会展示：

- 检索命中率
- 引用命中率
- 关键词包含率
- 无依据拒答准确率
- 失败案例

说明：`eval/report.md` 是本地运行后生成的报告文件，默认不提交；仓库中保留 `docs/eval_report_sample.md` 作为可查看的样例评测证据。

---

## 附加文本处理能力

除企业知识库问答主流程外，项目还提供以下附加能力：

- **内容分析**：提炼主题、关键信息、主要结论和关键词
- **结构优化**：优化文本结构和表达层次
- **风格改写**：调整文本风格，使表达更自然或更适合目标场景
- **多版本生成**：围绕同一输入生成多个表达版本
- **工作流优化**：按步骤总结状态、分析问题并给出优化建议

这些能力用于补充知识库问答之外的文本处理需求，让同一个工作台可以覆盖更多轻量文本任务。

---

## 技术亮点

- **前后端分离**：Streamlit 负责交互，FastAPI 负责接口和业务流程
- **SSE 流式输出**：模型回答逐步返回，提升对话体验
- **SQLite 持久化**：保存会话、消息、文档、chunk、检索问题和命中记录
- **ChromaDB 向量库**：支持本地持久化语义检索
- **Embedding 配置化**：支持本地模型或 OpenAI 兼容 embedding 服务
- **RAG 引用可解释**：前端展示来源、分数、chunk 和原文片段
- **无依据拒答**：检索不到可靠依据时避免强行回答
- **Eval 评测集**：用命中率、引用准确率和拒答准确率量化效果
- **Agent 路由决策**：先判断问题是否需要知识库，再决定是否执行 RAG，避免所有问题都盲目检索
- **Query Rewrite**：需要知识库时由 LLM 将用户问题改写为更适合向量检索的短查询
- **多模式扩展**：在知识库问答主流程之外，提供内容分析和工作流等附加能力

---

## 系统架构

```text
+-----------------------------+
|        Streamlit 前端        |
|  会话历史 / 文档上传 / RAG展示 |
+--------------+--------------+
               |
               | HTTP / SSE
               v
+-----------------------------+
|        FastAPI 后端          |
|  Chat API / Agent / RAG 服务 |
+--------------+--------------+
               |
      +--------+--------+
      |                 |
      v                 v
+------------+    +-------------+
|  SQLite    |    |  ChromaDB   |
| 会话/文档/检索 |    |  向量索引   |
+------------+    +-------------+
      |
      v
+-----------------------------+
| OpenAI SDK 兼容模型与 Embedding |
+-----------------------------+
```

---

## 目录结构

```text
enterprise-rag-agent-platform/
├── backend/
│   ├── api/                 # FastAPI 路由
│   ├── db/                  # SQLite 连接、建表和数据读写
│   ├── llm/                 # LLM 客户端封装
│   ├── prompt/              # Prompt 模板与构建逻辑
│   ├── rag/                 # chunk、embedding、向量库和检索服务
│   ├── schema/              # 请求和响应数据结构
│   ├── services/            # 聊天、Agent、工作流和元数据服务
│   └── main.py              # FastAPI 入口
├── frontend/
│   ├── app.py               # Streamlit 前端入口
│   ├── api_client.py        # 前端请求封装
│   ├── file_parser.py       # 文件解析
│   ├── renderers.py         # 前端展示组件
│   └── state_manager.py     # 前端状态管理
├── eval/
│   ├── questions.jsonl      # 评测问题集
│   ├── run_eval.py          # 评测脚本
│   └── report.md            # 评测报告，运行后生成
├── data/
│   ├── app.db               # SQLite 数据库，运行后生成
│   └── chroma/              # ChromaDB 向量库，运行后生成
├── requirements.txt
├── .env.example
└── README.md
```

---

## 数据库设计

项目默认使用 SQLite，数据库路径：

```text
data/app.db
```

核心表包括：

- `chat_sessions`
- `chat_messages`
- `documents`
- `document_chunks`
- `rag_queries`
- `rag_hits`
- `eval_cases`
- `eval_results`

这些表分别用于保存聊天会话、消息、上传文档、文档切块、RAG 查询记录、命中片段和评测结果。

---

## 运行方式

### 1. 克隆项目

```bash
git clone https://github.com/xiaoshanzhaguo/enterprise-rag-agent-platform.git
cd enterprise-rag-agent-platform
```

### 2. 创建虚拟环境并安装依赖

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

macOS / Linux：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. 配置环境变量

复制 `.env.example` 为 `.env`，然后填写自己的模型服务配置。

```bash
cp .env.example .env
```

主要配置项：

```env
BASE_URL=your_llm_base_url
DEEPSEEK_API_KEY=your_api_key
LLM_MODEL=your_llm_model

EMBEDDING_PROVIDER=local
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_BASE_URL=your_embedding_base_url
EMBEDDING_API_KEY=your_embedding_api_key

VECTOR_STORE_DIR=./data/chroma
RAG_RETRIEVAL_MODE=vector
RAG_PREVIEW_TEXT_LIMIT=120
DATABASE_URL=sqlite:///./data/app.db
FRONTEND_BACKEND_BASE_URL=http://127.0.0.1:8000
```

说明：

- `EMBEDDING_PROVIDER=local` 时，使用本地 embedding 模型，适合演示和低成本测试。
- `EMBEDDING_PROVIDER=openai` 时，使用 OpenAI 兼容 embedding 服务，需要配置 `EMBEDDING_BASE_URL` 和 `EMBEDDING_API_KEY`。
- `RAG_PREVIEW_TEXT_LIMIT` 统一控制参考依据卡片中的片段预览长度；前端直接展示后端返回的 `text_preview`，不再二次截断。
- 如果本机设置了 SOCKS 代理，需要确保安装了 `httpx[socks]`，当前 `requirements.txt` 已包含。

### 4. 初始化数据库

```bash
python -m backend.db.init_db
```

启动项目时也会连接数据库并创建表，手动执行可以提前确认数据库是否正常生成。

### 5. 启动后端

```bash
uvicorn backend.main:app --reload --port 8000
```

启动后可以检查后端健康状态：

```bash
curl http://127.0.0.1:8000/health
```

健康检查会返回服务状态、SQLite 连接状态、当前 RAG 检索模式和向量库目录是否可写，便于本地演示和 Docker 排错。

### 6. 启动前端

```bash
streamlit run frontend/app.py
```

默认打开 Streamlit 本地页面后，即可开始上传文档并进行企业知识库问答。

### 7. 使用 Docker Compose 启动

如果希望用容器快速启动完整项目，可以在项目根目录执行：

```bash
docker compose up --build
```

启动完成后访问：

```text
http://localhost:8501
```

Docker 启动说明：

- 后端容器端口：`8000`
- 前端容器端口：`8501`
- `./data` 会挂载到容器内 `/app/data`
- SQLite 数据库会写入 `./data/app.db`
- ChromaDB 向量库会写入 `./data/chroma/`
- 本地 embedding 模型缓存会写入 `./data/huggingface/`
- 前端容器会通过 `http://backend:8000` 访问后端服务

如果使用云端大模型或云端 embedding，请先在本地 `.env` 中配置对应环境变量，`docker-compose.yml` 会读取这些变量。

---

## 评测方式

运行：

```bash
python eval/run_eval.py
```

生成或更新：

```text
eval/report.md
```

报告适合作为演示材料，展示当前 RAG 链路在测试集上的命中率、引用准确率、关键词覆盖率和无依据拒答准确率。由于 `eval/report.md` 属于本地运行产物，仓库中额外提供 `docs/eval_report_sample.md` 作为可提交的样例评测证据。

---

## 截屏展示

### 1. 企业知识库问答首页

默认进入“企业知识库问答”核心入口，侧边栏展示会话入口、功能类型和对话设置。

![企业知识库问答首页](https://blog-1301840846.cos.ap-nanjing.myqcloud.com/img/image-20260619202013105.png)

### 2. 上传企业文档

在聊天输入框中上传企业知识库测试文档，作为本轮知识库问答的数据来源。

![上传企业知识库文档](https://blog-1301840846.cos.ap-nanjing.myqcloud.com/img/image-20260619202238523.png)

企业知识库测试文档，具体内容如下所示。

![企业知识库测试文档详情](https://blog-1301840846.cos.ap-nanjing.myqcloud.com/img/image-20260619202340742.png)

### 3. 基于文档提问

用户提问“试用期是多久？”，系统进入知识库检索和回答流程。

![基于企业知识库提问试用期](https://blog-1301840846.cos.ap-nanjing.myqcloud.com/img/image-20260619202545338.png)

### 4. 引用回答

回答中包含明确来源，便于追溯答案依据。

![带引用来源的回答](https://blog-1301840846.cos.ap-nanjing.myqcloud.com/img/image-20260619202715988.png)

### 5. 参考依据与命中原文

前端默认折叠“参考依据”，展开后展示命中的 chunk、score、证据卡片和完整原文，增强 RAG 可解释性，同时避免回答页面过长。

![RAG 参考依据折叠面板](https://blog-1301840846.cos.ap-nanjing.myqcloud.com/img/image-20260619202813636.png)

![RAG 命中原文详情](https://blog-1301840846.cos.ap-nanjing.myqcloud.com/img/image-20260619202824477.png)

### 6. 无依据拒答

当知识库没有相关依据时，系统明确拒答，避免凭空生成答案。

![无依据拒答示例](https://blog-1301840846.cos.ap-nanjing.myqcloud.com/img/image-20260619203035404.png)

### 7. 会话历史

侧边栏展示最近 10 条历史会话，支持点击恢复对应聊天记录，并通过高亮状态标识当前会话，体现 SQLite 会话持久化能力。

![会话历史列表](https://blog-1301840846.cos.ap-nanjing.myqcloud.com/img/image-20260619203212120.png)

### 8. Eval 评测报告

运行评测脚本后生成报告，用命中率、引用准确率和拒答准确率验证 RAG 效果。

![Eval 评测报告](https://blog-1301840846.cos.ap-nanjing.myqcloud.com/img/image-20260615175719392.png)

---

## 项目边界

当前项目重点是完整走通企业知识库问答的工程链路，因此暂不追求复杂多 Agent 编排，也不强依赖 LangChain。后续可以继续扩展：

- 多知识库管理
- 用户登录与权限隔离
- 文档增量更新
- 更完整的检索评测平台
- 多轮上下文理解
- 更细粒度的 citation 对齐

---

## 致谢

本项目用于个人学习、能力验证与求职作品展示，欢迎交流与建议。
