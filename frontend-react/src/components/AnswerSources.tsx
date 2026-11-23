import { FileText } from 'lucide-react'
import type { RagPreviewChunk } from '../types/chat'

export function AnswerSources({ sources }: { sources: RagPreviewChunk[] }) {
  if (!sources.length) return null
  return <details className="answer-sources">
    <summary><FileText size={15} aria-hidden="true" />参考资料 · {sources.length} 个片段</summary>
    <p className="source-note">以下是本次检索返回的资料，可展开核对原文。</p>
    {sources.map((source, index) => <details className="answer-source" key={`${source.source}-${source.chunk_id}-${index}`}>
      <summary>{source.file_name || source.source || '来源文档'}<span>片段 {source.chunk_id ?? index + 1}</span></summary>
      <p>{source.text || source.text_preview || '暂未保存原文片段。'}</p>
    </details>)}
  </details>
}
