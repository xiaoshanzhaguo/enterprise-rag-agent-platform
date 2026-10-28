import type { ExecutionTraceSummary } from '../types/chat'

const routes: Record<string, string> = { knowledge_qa: '知识问答', process_apply: '流程申请', it_support: 'IT 支持', finance_process: '财务流程', product_doc: '产品资料', irrelevant: '普通对话' }
const retrievals: Record<string, string> = { vector: '向量检索', keyword: '关键词检索', no_hit: '未命中', skipped: '跳过检索' }
const stages: Record<string, string> = { routing: '意图判断与问题改写', retrieval: '知识检索', answer: '答案生成 / 字段提取' }
const strategies = { model: '模型回答', process_extraction: '结构化字段提取', evidence_refusal: '缺少依据，明确拒答' }
const duration = (ms: number) => ms >= 1000 ? `${(ms/1000).toFixed(2)} 秒` : ms < 1 ? '不足 1 毫秒' : `${Math.round(ms)} 毫秒`

export function AnswerExecutionDetails({ trace }: { trace: ExecutionTraceSummary }) {
  return <details className="answer-execution">
    <summary>执行详情 · {trace.status === 'failed' ? '执行失败' : '生成完成'} · {duration(trace.total_ms)}</summary>
    <dl className="execution-summary"><div><dt>追踪编号</dt><dd><code>{trace.trace_id}</code></dd></div>
      <div><dt>业务路由</dt><dd>{routes[trace.route_type || ''] || trace.route_type || '尚未完成判断'}</dd></div>
      <div><dt>实际检索</dt><dd>{retrievals[trace.retrieval_mode || ''] || trace.retrieval_mode || '尚未执行'}{trace.hit_count != null ? ` · ${trace.hit_count} 个片段` : ''}</dd></div>
      {trace.answer_strategy && <div><dt>回答方式</dt><dd>{strategies[trace.answer_strategy]}</dd></div>}</dl>
    <ol className="execution-stages">{trace.stages.map(stage => <li key={stage.name}>
      <span>{stages[stage.name] || stage.name}</span><strong>{stage.status === 'skipped' ? '已跳过' : `${stage.status === 'failed' ? '失败 · ' : ''}${duration(stage.duration_ms)}`}</strong>
    </li>)}</ol><p className="page-footnote">{trace.status === 'failed' ? '截至失败时的服务端观测耗时。' : '统计至生成与元数据整理完成，不等同于浏览器端到端延迟。'}</p>
  </details>
}
