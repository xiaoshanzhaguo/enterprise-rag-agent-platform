# R4-04B 实验中断记录

**状态：未完成，未生成Vector/Hybrid效果结论。已停止真实调用，没有自动重试。**

## 首批调用

- 实际HTTP请求尝试1次，输入10个文档Chunk；服务返回HTTP 200。
- 返回模型为qwen3.7-text-embedding-flash，与当前配置一致。服务usage记录prompt_tokens=669、total_tokens=669。
- SDK重试0次，应用重试0次；其余7个文档批次和3个查询批次未发送。
- 本地HTTP响应检查位于SDK解码前，将base64字符串长度5464误标为维度，触发校验异常，SDK外层包装成APIConnectionError。不能把它描述成服务拒绝请求或已确认5464维。
- 当前SDK未指定encoding_format时默认请求base64，并在响应钩子之后解码为float32。离线测试证明1024维float32的base64长度正好可为5464。此测试解释编码机制，不冒充首批真实向量校验。
- 首批原始响应向量未持久化，因此实际维度、数值有效性仍未完成确认；当前有效向量缓存0条，status=partial。

## 本地修复与保护

实验脚本已修正base64/float两种格式的校验，并新增8项纯离线测试，全部通过。没有修改生产Embedding Client、Chunker、检索或Router。失败清单保留原始请求及异常记录，追加解释；不会把预期1024写成已验证事实。现有partial缓存会阻止自动进入评测或重复付费调用。

Gold及40份冻结文件摘要校验通过。没有启动后续隔离Chroma评测，没有访问业务SQLite或Collection，没有保留集调参，也没有LLM调用。

## 交付状态

- embedding_cache_manifest.json：失败审计、模型目标、Chunk/Gold映射、请求与编码检查说明。
- embedding_cache.json：空的partial缓存，不可用于质量评测。
- hybrid_experiments.py：真实预计算/隔离评测入口；仅完成首批尝试和本地修正，后续真实集成尚未验证。
- tests/test_r4_hybrid_experiments.py：8项离线单元测试。
- vector_baseline_gold_v1.json、hybrid_rrf_gold_v1.json：未生成，避免用缺失真实向量或Fake数据冒充效果。

## 六项结论

1. Vector是否优于Keyword：未评估。
2. Hybrid是否优于两者：未评估。
3. 哪些问题改善：无法确认。
4. 哪些问题仍失败：未完成真实检索，无法确认。
5. 是否值得改造生产：目前没有新增实验依据。
6. 是否值得进入Rerank：当前证据不足，先完成真实Vector/Hybrid基线。

后续需要明确继续指令；本轮不再自动请求，不提交Commit。

## 文件摘要

- embedding_cache_manifest.json SHA-256：`23ea290975ba6de6d0137ce3770cdad4f1a1336b5092099143c245d552cb5bbb`
- embedding_cache.json SHA-256：`06ce321aff54784bb89576d312b96ee20e37f140daedf466b45e13c7cd85d205`
- hybrid_experiments.py SHA-256：`50ae2bea03e4778904b73a01db99e1faa85541e98f74e0a245b41b2592e41e1b`
- tests/test_r4_hybrid_experiments.py SHA-256：`7b35bb7d54f53afc3cddcb945ac319cf48c30a97c6ffdd9cc8dd0787971b821b`
