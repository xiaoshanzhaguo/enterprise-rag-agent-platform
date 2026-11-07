# R4 Gold v1 冻结前校验报告（候选，2026-11-06）

**状态：校验通过、候选待最终确认；没有正式冻结或启用。** 本报告不是重新评测，也不把AI审核建议当作用户对整个数据集的确认。

## 第二轮复核结果

- 原36道PASS逐题对照问题、证据、相关段落和相邻上下文，复核支持性、主要证据完整性、等级和技术来源；全部通过，异常0。按要求不重复完整审核表。
- 其中5道流程申请仅确认检索N/A设置，7道无正证据题仅确认冻结12文档内无所求事实，不代表生成答案、拒答或现实世界事实已通过验证。
- 两个争议问题按本次用户确定结论落实，不再重开讨论。最新候选为38道PASS建议；第一轮36 PASS/2 REVISE记录原样保留在审核历史。

## 已落实的两项裁定

| 问题 | Gold v1候选处理 | 状态边界 |
| --- | --- | --- |
| finance_02 | 原题不改；e1/e2原文、来源和偏移保留，均primary/2；增加“仅考查打车费用专用附件及缺少行程单的处理。” | 不新增通用材料；这一范围裁定来自用户，不是自动确认整套数据 |
| it_03 | e1保留primary/2；e2移出计分evidence，完整保留为background_notes，included_in_qrels=false | 不将模块负责人认定为审批角色；不重复增加其他审批证据 |

## 冻结前检查

| 检查项 | 结果 |
| --- | --- |
| 题目/分集 | 38题，开发30/保留8；ID和规范化问题不重叠，顺序及原始题干不变 |
| 原始证据 | 37/37来源、版本身份、偏移、原文和SHA-256通过，含被移入背景的e2 |
| 计分证据 | 36/36通过；35条primary/2，1条auxiliary/1（holdout_finance_01:e2） |
| 背景备注 | 1条it_03:e2；原auxiliary/1仅作为历史记录，不在计分集合，不创造新的评分等级 |
| 主要证据完整性 | 26道有正证据题均保留必要主要证据；流程/无正证据题不伪造正证据 |
| 冻结Chunk映射 | 全部原始及候选计分跨度在原冻结目录有精确承载块；只做结构校验，不依据Keyword命中确认金标 |
| 版本与摘要 | 12份原文原始字节/LF文本SHA-256、document_key重建与V1一致 |
| 审核状态 | 原集38题/37证据及候选计分证据均未改confirmed，保持pending_review；AI结论另记review recommendation |
| 历史保护 | 原开发集/保留集、原文、第一轮Markdown报告、指标代码和历史基线摘要不变；审核JSON只追加本轮记录 |
| 正式冻结 | 未执行；未切换run_baseline输入，未重算旧基线，未访问业务库/Chroma/模型API，未提交Commit |

分集只保证问题/ID隔离，仍共享语料；不宣称未见文档泛化或独立人工盲审。V1指冻结语料清单版本，不是业务数据库当前指针；没有业务document_id，未以临时基线ID冒充业务身份。

## 候选与审核记录摘要

- [gold_v1_candidate.json](gold_v1_candidate.json) 文件字节SHA-256：

  `b7fc8be3b26fd0ddc7b0d438e5fb3da33bb86999cbbc9f8161f9ff26189bd1e5`

- 更新后的 [ai_review_candidates.json](ai_review_candidates.json) 文件字节SHA-256：

  `eaf1e5b1497f85d5395483724d5a47f9fbbc87cfaa912978cd808102f3c15e73`

- 更新前第一轮审核JSON文件SHA-256：`35f6e1af46ce059bb3d161490825c7d155119091decae31e6efe6acdbb50f794`；其原有字段、审核理由和原始证据仍完整保留，新内容仅在freeze_preparation_round2。
- 候选cases语义摘要（排序键、紧凑UTF-8 JSON；与文件字节摘要不同）：`509583434fdac3dc10e1c73501537ed577ce9e858b4c859ec5ca9c4674b3b5e3`。

## 保留输入文件的SHA-256

以下值按当前文件字节校验；它们不会因本轮候选生成而改变。审核JSON因追加记录有新旧两份摘要，已单独列出，不纳入本表的未修改断言。

| 输入 | SHA-256 |
| --- | --- |
| eval/r4/ai_review_report.md | `cff22e5a7732954eea3d3bb1062e0608d9cde5869a88b94883cc8c96f9326b14` |
| eval/r4/baseline_7677788_keyword.json | `e1145cd50336bf7d58684d3852cdc18b5f7ca9441d9cd5c81380eeeb18238775` |
| eval/r4/corpus.json | `4e136b4bf7163190b4f2337b7e646ebf97448dcbf8d9489faf546c69ffa8faee` |
| eval/r4/dataset.py | `0219b492e10c51c66832096ce2b177b76877711424155d64d82721e27be6c50b` |
| eval/r4/development.jsonl | `73953a60d40dc71e91e18eed0b1f8a2f99a34fd9be9d69350e5d52e7da89a39b` |
| eval/r4/holdout.jsonl | `bd8725d0caeee6fcbcdad82d3f681e6ae3eeb10c33e7a3ef06c9217ebf2fd57b` |
| eval/r4/metrics.py | `42cb2bcdbec39d435ae0653d7b3bf1b832a7eca6acfa785eac54902729c3e0df` |
| eval/r4/README.md | `1b0e384f2e6fd947f9d255ec163dbb115e0f4aaa570d4c54895ec0865da31c44` |
| eval/r4/run_baseline.py | `5d5b79ac736fb753578efccfae2fdf5045ffb23801f4fcb256daef1f67698412` |
| docs/sample_knowledge_base/finance/payment_approval_policy.md | `b1b0493604386f61cb9d670cb9abe5892d4eb72f9a4076121161aa20cee96428` |
| docs/sample_knowledge_base/finance/reimbursement_policy.md | `cf77f4d2373b6d6ecf68f195bfe92d204c941519eb9cf76e92b8e239fc178845` |
| docs/sample_knowledge_base/finance/travel_expense_policy.md | `9e7216c0923f54f5b9135dd081ca78f9733967606d5e0f85b7a7eb5320f64a3e` |
| docs/sample_knowledge_base/hr/attendance_policy.md | `19582bae2a915b0d6bcd7af430002d42b92e5c4760072b791a1c2d352a44a08a` |
| docs/sample_knowledge_base/hr/leave_policy.md | `095ed54674430283e1abad07f5c98988e7ecd3d383f926c074d70dba48b20de2` |
| docs/sample_knowledge_base/hr/onboarding_checklist.md | `8f3df30fcfe2cd051f4240c57d660df693ae9fd38e98966beaf4d5328e404480` |
| docs/sample_knowledge_base/it/device_security_policy.md | `358dc68f620cc8f650d7f70b7c36e339d7e9f5be2311b77a40f7b8f14f01ff42` |
| docs/sample_knowledge_base/it/gitlab_access_request.md | `dcc98edece007c5ba98b2dffbbe40b5c96f894729df8635a0fc84b86446ac4ca` |
| docs/sample_knowledge_base/it/vpn_troubleshooting.md | `43eda78c225d198d8367262312394cc5355afb616ebf12c66d6c864b6e27d714` |
| docs/sample_knowledge_base/product/customer_feedback_process.md | `b92db5b7ea454041cb1a0a39a3390d0091dc2e89481a8fcca08c396145c3e261` |
| docs/sample_knowledge_base/product/product_faq.md | `dae00ea5a790dc221db09f65a6ff6f274c6a4f2326ee4a078d36b898566bcc2a` |
| docs/sample_knowledge_base/product/release_process.md | `dc4acf7f742896614846868e6ef361f2c83807b3b9a4798567f575c49485c8c6` |

## 待最终确认

没有未解决的数据异常。唯一待办是用户对整套Gold v1候选的最终确认；确认前该文件不生效，原评测集仍是评测程序输入，不能把旧pending实验值改称正式Gold v1结果。
本次没有采用第一轮的其他可选扩大跨度建议，避免未经授权改变金标范围。第一轮详细理由继续见 [ai_review_report.md](ai_review_report.md)；两项确定裁定及36题第二轮记录已追加到结构化建议。
