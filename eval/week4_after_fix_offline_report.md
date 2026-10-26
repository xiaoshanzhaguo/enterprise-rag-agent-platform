# 第四周评测报告

- 模式：offline；模型：None；检索：keyword；top_k=3
- 时间：2026-10-26T10:52:03.097414+00:00；错误：0；待复核拒答：0
- 范围：真实 Agent 服务或离线规则/检索，不包含 HTTP、浏览器与 n8n 提交。

| 指标 | 通过/适用数 | 比率 |
| --- | ---: | ---: |
| 检索命中 | 20/20 | 100.0% |
| 答案引用命中 | 0/0 | N/A |
| 答案关键词覆盖 | 0/0 | N/A |
| 无依据拒答（逐条复核） | 0/0 | N/A |
| 流程意图二分类 | 30/30 | 100.0% |
| 六类路由完全匹配 | 26/30 | 86.7% |

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
| it_05 | knowledge_qa | True | None | N/A | None | N/A |  |
| product_01 | knowledge_qa | True | None | N/A | None | N/A |  |
| product_02 | knowledge_qa | True | None | N/A | None | N/A |  |
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
| refusal_05 | it_support | None | None | N/A | None | N/A |  |
