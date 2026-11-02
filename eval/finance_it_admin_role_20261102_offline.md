# Task 03.1：管理员职业角色误判修复（2026-11-02）

基于 HEAD `b56917a1c3429014c9eb2d30cd33f042aa7c8d95` 的未提交 Task03 / Task03.1 修改，不代表该 Commit 原样行为。原始基线、Task03 原报告和项目现状报告均按修改前 SHA-256 核验，未覆盖；本次不提交 Commit。

根因：`_resolve_system_access_route` 将“管理员”单独作为访问意图，使“服务器/数据库 + 管理员职业 + 报销需求”提前返回 IT，抢占财务规则。

最小修复：仅从访问意图正则删除“管理员”，并增加一条解释注释；保留账号、账户、凭据、密码、口令、权限、登录、认证。系统对象、24字邻近窗口、对象关联顺序及流程申请/产品治理/企业安全优先级不变。不修改 RAG、Agent Service、Prompt、语料和金标。

新增测试位于 `tests/test_finance_it_route_boundaries.py` 的 ROLE_CASES 和 AdminRoleRouteBoundaryTests，原24条 CASES保持原样。新增8条表达（4财务、4真实IT访问），两种RAG开关共16种降级调用组合；修复前4/8，修复后8/8。

| 新增表达 | Task03修复前 | Task03.1修复后 |
| --- | --- | --- |
| Linux服务器管理员出差的费用如何报销？ | it_support | finance_process |
| 数据库管理员的差旅报销标准是什么？ | it_support | finance_process |
| 服务器管理员报销时需要提交哪些发票？ | it_support | finance_process |
| 操作系统管理员出差的住宿费用标准是多少？ | it_support | finance_process |
| Linux服务器管理员账号如何申请？ | it_support | it_support |
| 数据库管理员忘记登录密码怎么办？ | it_support | it_support |
| 如何申请服务器管理员权限？ | it_support | it_support |
| 服务器管理员的登录认证失败如何排查？ | it_support | it_support |

| 回归范围 | 修改前 | 修改后 |
| --- | --- | --- |
| 原30题规则路由 | 30/30 | 30/30 |
| 文件级检索命中 | 20/20 | 20/20 |
| 流程意图 | 30/30 | 30/30 |
| 原24条财务与IT边界 | 24/24 | 24/24 |
| 新8条角色与访问边界 | 4/8 | 8/8 |
| 既有附加规则样例 | 14/16 | 14/16 |
| 原30题执行异常 | 0 | 0 |

测试按要求依次执行：先运行新测试，4条财务表达在直接规则及两种RAG开关中复现12个失败子项；修复后新增2个测试方法通过；再运行原有Router相关15项，全部通过；运行30题offline及边界对照；最后完整后端87项（原85+新增2）全部通过，无新增回归。完整测试进程允许asyncio所需本机回环，禁止外部网络，外部尝试0。

评测始终为 offline / keyword / top_k=3，独立临时SQLite，禁止网络和模型入口，未调用真实模型、消耗真实Token或下载权重。答案、引用、拒答输出正确性和流程字段提取未评估，不能用历史结果代替。

遗留：附加产品表达 holdout_product_01（需求何时锁定）、holdout_product_02（隐含灰度）仍归 knowledge_qa，预期 product_doc，前后相同，未扩大本次范围。规则仍依赖有限词表及邻近窗口，多独立任务、长距离指代及包含原有电脑/网络/GitLab强词的职业描述不在本次最小修复保证范围；真实LLM路由仍需另行评估。

本次报告元数据首次源码反转校验因换行不匹配失败，随后改用修改前保存且SHA-256校验一致的源码快照，重新离线执行成功；未改金标或生产逻辑绕过失败。详细源码指纹、保护文件哈希、逐题原/新分类及RAG开关结果均在同名JSON。

以下为原执行器输出，N/A/None表示未评估。

# 第四周评测报告

- 模式：offline；模型：None；检索：keyword；top_k=3
- 时间：2026-11-02T10:34:00.842281+00:00；错误：0；待复核拒答：0
- 范围：真实 Agent 服务或离线规则/检索，不包含 HTTP、浏览器与 n8n 提交。

| 指标 | 通过/适用数 | 比率 |
| --- | ---: | ---: |
| 检索命中 | 20/20 | 100.0% |
| 答案引用命中 | 0/0 | N/A |
| 答案关键词覆盖 | 0/0 | N/A |
| 无依据拒答（逐条复核） | 0/0 | N/A |
| 流程意图二分类 | 30/30 | 100.0% |
| 六类路由完全匹配 | 30/30 | 100.0% |

流程意图混淆统计：{'passed': 30, 'total': 30, 'rate': 1.0, 'tp': 5, 'fp': 0, 'fn': 0, 'tn': 25}

引用需匹配实际召回 chunk；关键词覆盖为事实组初筛，不代表语义正确率。拒答复核未完成时，汇总仅覆盖已复核或运行出错的题，不能解释为全部拒答题的准确率。

| 用例 | 实际路由 | 检索命中 | 引用命中 | 关键词组 | 拒答复核 | 字段严格匹配 | 错误 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| hr_01 | knowledge_qa | True | None | N/A | None | N/A |  |
| hr_02 | knowledge_qa | True | None | N/A | None | N/A |  |
| hr_03 | knowledge_qa | True | None | N/A | None | N/A |  |
| hr_04 | knowledge_qa | True | None | N/A | None | N/A |  |
| hr_05 | knowledge_qa | True | None | N/A | None | N/A |  |
| finance_01 | finance_process | True | None | N/A | None | N/A |  |
| finance_02 | finance_process | True | None | N/A | None | N/A |  |
| finance_03 | finance_process | True | None | N/A | None | N/A |  |
| finance_04 | finance_process | True | None | N/A | None | N/A |  |
| finance_05 | finance_process | True | None | N/A | None | N/A |  |
| it_01 | it_support | True | None | N/A | None | N/A |  |
| it_02 | it_support | True | None | N/A | None | N/A |  |
| it_03 | it_support | True | None | N/A | None | N/A |  |
| it_04 | it_support | True | None | N/A | None | N/A |  |
| it_05 | it_support | True | None | N/A | None | N/A |  |
| product_01 | product_doc | True | None | N/A | None | N/A |  |
| product_02 | product_doc | True | None | N/A | None | N/A |  |
| product_03 | product_doc | True | None | N/A | None | N/A |  |
| product_04 | product_doc | True | None | N/A | None | N/A |  |
| product_05 | product_doc | True | None | N/A | None | N/A |  |
| process_01 | process_apply | None | None | N/A | None | N/A |  |
| process_02 | process_apply | None | None | N/A | None | N/A |  |
| process_03 | process_apply | None | None | N/A | None | N/A |  |
| process_04 | process_apply | None | None | N/A | None | N/A |  |
| process_05 | process_apply | None | None | N/A | None | N/A |  |
| refusal_01 | knowledge_qa | None | None | N/A | None | N/A |  |
| refusal_02 | finance_process | None | None | N/A | None | N/A |  |
| refusal_03 | it_support | None | None | N/A | None | N/A |  |
| refusal_04 | knowledge_qa | None | None | N/A | None | N/A |  |
| refusal_05 | finance_process | None | None | N/A | None | N/A |  |
