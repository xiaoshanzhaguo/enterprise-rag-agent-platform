# R4 复现兼容修复

Gold v1、原始问题/语料、缓存和历史评测报告不重写，历史摘要不替换。

## 换行兼容

Windows `core.autocrlf=true` 曾使工作区原始字节摘要与 Git blob 不同。
`gold_v1_compatibility.json` 记录修复前已验证的路径、历史摘要及对应 LF 摘要。
校验优先检查原始 SHA-256；只有明确登记的路径与历史摘要组合才允许
CRLF→LF 后的字节摘要匹配。不会去除空格、重新序列化 JSON 或规范 Unicode。
未知摘要、正文篡改、额外空格和未经批准的校验器修改仍然失败。

旧冻结清单也记录了校验器代码本身的摘要。因此本次校验器升级在 sidecar 的
`verifier_revisions` 单独记录旧摘要与新代码精确 LF 摘要；不伪装成换行差异，
也不放开任意代码版本。该配置需和代码一起审查、提交；未来修订同样需要明确记录。

`.gitattributes` 将 R4 文件、R4 测试及样例语料固定为 LF，以避免新文件继续出现
这种差异。没有批量重写现有文件。缓存原始文件摘要和输入文本摘要分别保留，
输入身份校验、Gold 证据文本、偏移及版本映射规则均不改变。

## 独立重跑

在已有相同 Embedding Profile 配置的原生环境中，使用已完成的真实缓存：

```powershell
$env:USE_TORCH='0'
$env:USE_TF='0'
$env:HF_HUB_OFFLINE='1'
.venv\Scripts\python.exe -m eval.r4.hybrid_experiments evaluate --output-dir eval/local/r4-replay-new-run
```

输出目录只允许位于 `eval/r4` 或 `eval/local`。新报告独立创建，已有同名报告会拒绝
覆盖；输出目录内的 Vector 报告摘要用于该目录的 Hybrid 报告关联。此入口不生成
Embedding，运行中阻止网络及模型调用，只建立临时 SQLite 和隔离 Chroma。
本机沙箱若阻止加载既有 Chroma Rust DLL，需要在具备本机 DLL 加载权限的进程执行。

## 验证

- 新增 10 项测试，覆盖 LF/CRLF、Git blob 检出后的新进程加载、正文/JSON格式篡改、
  未知摘要、校验器精确版本、输出路径范围、已有报告保护和 CLI 参数。
- 相关 65 项测试全部通过。
- 使用 `eval/local/r4_replay_20261108_compatibility` 实际重跑：Keyword、Vector、RRF
  三组汇总与历史报告一致，网络、真实 Embedding、非测试 SQLite 调用均为 0，临时目录已清理。
- 46 份登记的原始文件字节摘要保持不变；仅校验器代码是显式登记的新版本，待本次代码审查。
  Gold v1 SHA-256 仍为 `61865cc306cff7534abc0b7d96af7b633966e37d615c78c40312bd61500aadab`。

本次没有修改生产代码、调用模型 API、访问业务 Collection 或提交 Git Commit。
