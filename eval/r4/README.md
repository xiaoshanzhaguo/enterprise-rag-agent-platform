# R4-01 证据级检索评测

当前基线：HEAD `76777888a7970f951613582145bf731aa5ee47d8`，生产检索代码未修改。R4 程序和数据尚未提交，报告保存其内容摘要。只评估检索，不生成答案；不得把文件命中、证据命中或工程测试通过解释为答案正确率。

## 数据与复核

- `corpus.json` 冻结原 12 份文档，包含来源、文件字节摘要（溯源）、LF 文本摘要、外部稳定 document_key 和版本 V1。
- `development.jsonl` 原样保留 30 题的 `legacy_case`，原始问题/金标文件未改。20 题有正证据，5 题流程申请不适用检索指标，5 题沿用历史“无正证据”分类，仅观察误召回，不推断拒答质量。
- `holdout.jsonl` 是另外 8 个新问法：6 道正证据题、2 道无正证据待复核题。与开发集 ID/问题不重叠，但共享文档，**不是未见文档泛化评测，也不宣称 AI 作者未见过题目**。已在首次运行前冻结；后续参数选择只用开发集，保留集用于固定对照，不根据本轮保留集结果改算法。
- 共 37 条证据跨度，保存 source、document_key、原文、LF 文本字符偏移、证据 SHA-256、primary/auxiliary 和相关性等级。primary=2（回答关键事实），auxiliary=1（相关补充背景），未标注结果在本次证据策略下计 0；不等于经过人工证明其完全无关。
- 全部问题和新证据均为 `pending_review`。补全了例如打车材料/缺失处理、P1 处理计划/同步频率等多事实证据，**尚无人工确认的正式金标指标**。确认需同时复核 case 与 evidence 的状态、支持性和完整性；不能仅因为原文存在就自动确认语义相关。

实际 Chunk 不写死成历史金标。`dataset.py` 先校验原文/偏移，再把证据映射到本轮真实切分后的所有当前版本 Chunk：文档身份/版本摘要相同，且 Chunk 在 NFKC/忽略空白后完整包含证据。Chunk key 含文档版本摘要、块序号和块文本摘要，不依赖临时 SQLite 自增 ID。边界跨块而无法完整映射时记为 `unmapped_evidence`，整题从指标分母排除并报错明细，不通过删除金标偷偷提高召回。

此为**精确证据跨度映射的 Chunk 相关集合**，不是人工穷举的全部语义相关块；替代证据、辅助证据是否必要、否定句和跨块语义仍需人工复核。重切分时重新映射，而不是保留旧 chunk-n 作为不变真值。

## 指标定义

对每题按稳定 Chunk key 保留首次结果去重，再取 K；K 支持 1/3/5。违规版本保留排名但计 0 增益，另输出 `version_filter_violations`，不通过过滤违规结果来掩盖缺陷。主要/辅助证据都进入正相关集合，另列主要和辅助证据缺失。

| 指标 | 定义 |
| --- | --- |
| Document Recall@K | 返回的不同相关文档版本数 / 全部标注正证据所属文档版本数；仍不能说明返回块支持答案 |
| Chunk Recall@K | 返回的不同正相关 Chunk 数 / 按上述完整跨度规则映射的全部正相关 Chunk 数 |
| MRR@K | Top-K 首个正相关块的排名倒数；未命中为 0，不是无限检索深度的 MRR |
| nDCG@K | DCG=Σ(2^grade−1)/log2(rank+1)，除以全部正相关块按等级排序的理想 DCG@K |

每个分集分别按题宏平均，不将开发集和保留集混为调参分数。空返回且有正证据计 0；没有正证据则为 N/A，不计入正证据平均，也不自动视为正确拒答。没有人工确认的题时，`human_confirmed_metrics` 的均值为 null、分母为 0。`--include-pending` 才将待复核题加入**实验性汇总**；逐题 scores/qrels 用于诊断，仍保留标注状态。

## 运行及安全边界

在仓库根目录使用已有虚拟环境，不安装依赖：

```powershell
# 默认只汇总人工确认标签，开发集优先；该新输出路径必须不存在。
.venv\Scripts\python.exe -m eval.r4.run_baseline --output eval/local/r4_reviewed_only.json

# 本次已执行一次；再次复现请选择其他新的输出路径，避免覆盖基线。
.venv\Scripts\python.exe -m eval.r4.run_baseline --split all --include-pending --output eval/r4/baseline_7677788_keyword.json

.venv\Scripts\python.exe -m unittest discover -s tests -p test_r4_evaluation.py -v
```

基线命令必须在全新进程执行：禁用 dotenv 加载模型凭据，将数据库和向量目录指向一次性目录；语料通过实际 `IndexManagementService(keyword)` 保存并发布 V1。审计钩子拒绝任何网络连接和非测试 SQLite；函数入口拒绝 Embedding/模型下载及 Chroma 客户端，**抛出异常而非返回 Fake**。运行结束清理临时数据，不连接业务数据库/Collection。只允许在 eval/r4 或 eval/local 新建 JSON，已存在文件不能覆盖。

问题只执行 strip，LLM Query Rewrite、模型路由、手工/自动分类过滤都关闭，避免把 Oracle 分类或模型调用混入基线。分别调用真实 `retrieve_keyword_chunks` 和 `retrieve_rag_chunks_with_mode` 的 keyword 路径。后者叫 `production_keyword_control`，仅验证生产分发器在显式 keyword 条件下的行为；不是实际部署 vector 模式的完整生产基线。

每题记录输入当前 Chunk 数、通过词面门槛的候选数（同一生产排序函数 K=全部枚举）、请求 K、最终返回数、实际模式、查询、证据映射、逐块文本/得分/版本有效性及缺失证据。候选数不是 Chroma ANN 内部候选数。计时受隔离审计影响，不作为生产延迟结论。

报告还记录 HEAD、生产实现/评测代码/数据的 LF 文本摘要及未提交评测文件、标注策略。配置观察仅读取三个非敏感 RAG 标志，不加载 Key，也未核验运行中服务已加载的配置。

## 本次结果

[基线 JSON](baseline_7677788_keyword.json)：38 题 × 3 个 K × 2 个真实 Keyword 路径，228 条明细；12 文档、79 当前 Chunk。37 条证据全部成功映射；没有通过删掉未映射题改变分母。以下均为 **pending_review 实验值**，不是人工确认的质量结论。

| 分集 / 正证据题数 | K | Document Recall | Chunk Recall | MRR@K | nDCG@K |
| --- | ---: | ---: | ---: | ---: | ---: |
| 开发 / 20 | 1 | 1.0000 | 0.8750 | 0.9500 | 0.9500 |
| 开发 / 20 | 3 | 1.0000 | 0.9250 | 0.9500 | 0.9413 |
| 开发 / 20 | 5 | 1.0000 | 0.9250 | 0.9500 | 0.9413 |
| 保留 / 6 | 1 | 0.8333 | 0.7500 | 1.0000 | 1.0000 |
| 保留 / 6 | 3 | 1.0000 | 1.0000 | 1.0000 | 0.9806 |
| 保留 / 6 | 5 | 1.0000 | 1.0000 | 1.0000 | 0.9806 |

两个 Keyword 路径的实际结果一致。7 个“无正证据”题均为空返回，仅是检索观察，不是 7/7 拒答正确率。5 个流程申请题保留原金标和诊断明细，但不计入 Recall/MRR/nDCG。

**Vector：阻塞。** 未提供与冻结语料/查询、当前 Embedding Profile 匹配的预计算向量；禁止调用真实 Embedding、下载权重或访问业务 Collection，所以没有生成真实向量质量数据。没有把 Fake/零向量写成向量基线，也未改变业务模型。

**当前生产配置路径：阻塞。** 配置文件显示 vector，运行中配置未单独查询；vector 无可靠结果才回退 keyword，不是 Hybrid。未运行 vector 分支，故也不能把 keyword 控制结果说成真实 vector→keyword 回退、LLM 改写或完整 Agent 的复现。

主要诊断：

- `product_01`：K=1/3/5 都命中正确文件，但只返回标题和产品定位两块；支持 Markdown/TXT/PDF 的证据块未进入两个词面候选。Document Recall=1 而 Chunk Recall=0，单纯扩大 K 不能证明能修复。
- `it_03`：主要审批证据找回，辅助“Maintainer 授予对象”未找回，K=3/5 的 Chunk Recall=0.5。辅助证据是否应纳入质量目标需要人工确认；不据此宣称实际答案错误。
- `finance_02`、`product_05`：Top-1 缺部分主要事实，K=3 后主要证据齐全；说明单文件多证据需要分别评价。
- 两道跨文档保留题 Top-1 证据不足，K=3 齐全；保留集不用于据此调整算法。

专项测试 20/20，既有第四周评测程序回归 11/11，共 31/31；包含指标公式、空分母、重复块、分级相关性、来源跨度完整性、证据跨版本拒绝及临时 SQLite 的候选/历史/停用/deleting 过滤。未重复完整项目测试。真实基线的网络、非测试 SQLite 和模型/Chroma 入口尝试计数均为 0。

下一步先人工复核 primary/auxiliary 与证据完整性，冻结评测版本；再用开发集定位标题信息与词面门槛问题。Vector 需另行授权可用的隔离向量输入/模型调用才能测；没有该基线前，不宣称 Hybrid 或 Rerank 有收益。本轮没有修改任何生产算法、Router、R1/R2/R3、旧金标或历史报告。
