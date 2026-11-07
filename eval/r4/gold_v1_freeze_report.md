# R4 Gold v1 正式冻结及 Keyword 基线（2026-11-07）

## 冻结结论

经用户明确批准，正式冻结 Gold v1：38题，开发30/保留8；36条计分证据（primary/2共35条、auxiliary/1共1条），1条背景不计分。仅正式副本标记 confirmed；原始状态保存在 original_annotation_status，候选审核和历史结论保留。

finance_02 原题不变，保留e1/e2为primary/2，并限定为打车费用专用附件及缺少行程单的处理。it_03保留e1，e2仅为背景，不将模块负责人认定为审批角色。

原37条证据来源、偏移和摘要已校验；12份语料维持原V1，79个实际Chunk。V1属于冻结评测语料身份，不代表已经核验历史业务Collection的Embedding来源。

## 文件与不可变性

- 正式数据：gold_v1.json；文档版本、语料摘要、来源文件摘要：gold_v1_manifest.json。
- 完整文件字节摘要索引：gold_v1_checksums.json；索引不自包含，以避免循环摘要。
- gold_v1_candidate.json、原development.jsonl/holdout.jsonl、原始文档及历史实验基线均保留不变。
- 此前同名的候选冻结报告按原字节归档为gold_v1_candidate_freeze_report.md，原摘要为 3ea0d442629261d4dc711435921852b7a25a96981bd3a6ce24591b8959831669。
- Gold v1不得静默修改。加载时核验清单摘要、语料、原始数据、确认状态、题目身份及证据集合；需要修订时发布新版本。
- 清单自身及本报告、正式基线、评测实现和测试的摘要由gold_v1_checksums.json记录；交付时应一起保留并在提交中固定。

| 文件 | SHA-256 |
| --- | --- |
| gold_v1.json | `61865cc306cff7534abc0b7d96af7b633966e37d615c78c40312bd61500aadab` |
| gold_v1_manifest.json | `0d96da3dd772bc25f33356fb3d412347b43ce0afc8f201be5038565f14b74719` |
| baseline_gold_v1_keyword.json | `4023379f2ae692a084544f6ae4679bf89d08c9594faa28e35204cf27d87154e6` |
| gold_v1_candidate.json | `b7fc8be3b26fd0ddc7b0d438e5fb3da33bb86999cbbc9f8161f9ff26189bd1e5` |

## 正式 Keyword 基线

HEAD：`76777888a7970f951613582145bf731aa5ee47d8`。使用真实生产关键词检索和R3白名单，在临时SQLite创建12份文档并运行全部38题。没有修改生产排序。两条执行路径×38题×3个K，共228条明细。

指标仅对20道开发集、6道保留集的answerable问题宏平均。5道流程题、7道无正证据题不计入上述质量指标；不将检索无命中当作拒答正确。

| 分集 | K | Document Recall@K | Chunk Recall@K | MRR@K | nDCG@K |
| --- | --- | --- | --- | --- | --- |
| development | 1 | 1.00000000 | 0.90000000 | 0.95000000 | 0.95000000 |
| development | 3 | 1.00000000 | 0.95000000 | 0.95000000 | 0.95000000 |
| development | 5 | 1.00000000 | 0.95000000 | 0.95000000 | 0.95000000 |
| holdout | 1 | 0.83333333 | 0.75000000 | 1.00000000 | 1.00000000 |
| holdout | 3 | 1.00000000 | 1.00000000 | 1.00000000 | 0.98061020 |
| holdout | 5 | 1.00000000 | 1.00000000 | 1.00000000 | 0.98061020 |

指标定义：Document Recall按全部相关版本文档数计；Chunk Recall按包含标注跨度的实际Chunk数计；MRR为Top-K首个正证据排名倒数；nDCG使用(2^grade−1)/log2(rank+1)。重复Chunk先去重，错误版本零增益，过程与逐题证据映射见JSON。

## 与待审核实验的比较

比较对象：baseline_7677788_keyword.json（同一HEAD、原pending_review金标）。两次所有返回Chunk及排序完全一致。开发集Chunk Recall@1/3/5各增加0.025（2.5个百分点）；nDCG@3/5各增加0.00868827（约0.8688个百分点）；其余开发指标不变。保留集全部指标不变。变化仅来自it_03:e2退出正证据，不是算法提升。正式集26道适用题均已confirmed；旧报告confirmed样本为0，不能将其空正式指标当作原算法得分。

Top-1开发仍缺少finance_02、product_01、product_05的部分/全部主要证据；Top-3/5开发仍有product_01未命中主要证据。保留集Top-1缺少两道跨文档题的部分证据，Top-3/5证据召回完整。

## 可复现命令与测试

```powershell
$env:USE_TORCH='0'
$env:USE_TF='0'
$env:HF_HUB_OFFLINE='1'
.venv\Scripts\python.exe -m eval.r4.run_baseline --gold eval/r4/gold_v1.json --split all --output eval/r4/baseline_gold_v1_keyword_repeat.json
.venv\Scripts\python.exe -m unittest tests.test_r4_evaluation tests.test_r4_gold -q
```

输出必须使用新路径；原始/历史报告不会被基线脚本覆盖。初次专项测试26项通过、1项因本机c10.dll加载失败；关闭未使用的可选Torch/TF加载后27项通过。最终加入报告重算与完整摘要索引校验后，共29项专项测试。原测试夹具存在ResourceWarning，不影响断言结果。

独立校验已重算228条逐题指标与汇总，核对正式Gold/清单/报告摘要，以及候选、原始金标、原文和旧实验摘要。新Gold测试覆盖confirmed状态、背景排除、分集隔离、篡改数据/源文件拒绝、未获批准副本拒绝及路径越界。

安全审计：网络连接、非测试SQLite、Embedding/Chroma尝试均为0；真实Embedding调用0；没有Fake检索质量结果。没有访问业务Collection、重新生成Embedding或改动生产检索算法。

本轮不评估真实Vector、LLM改写、答案/引用语义正确性及拒答准确性。生产dispatcher仅被强制为Keyword的对照，不是Hybrid或已测Vector回退。保留集已公开运行，仍用于回归隔离，不能宣称独立盲测。
