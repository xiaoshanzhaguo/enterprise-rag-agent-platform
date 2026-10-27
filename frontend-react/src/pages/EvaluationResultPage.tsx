import { BarChart3, CheckCheck, FlaskConical } from 'lucide-react'
import { metrics, model, executed_at_utc } from '../../../eval/week4_after_fix_report.json'
import { FeedbackReviewPanel } from '../components/FeedbackReviewPanel'

const metricItems = [
  ['检索命中', metrics.retrieval_hit, '预期来源进入检索结果'],
  ['答案引用命中', metrics.citation_hit, '引用与实际召回片段匹配'],
  ['关键词覆盖', metrics.keyword_coverage, '回答覆盖预设事实组'],
  ['无依据拒答', metrics.refusal_correct, '逐条阅读复核'],
  ['流程意图识别', metrics.process_intent, '申请与非申请正确区分'],
  ['六类路由匹配', metrics.route_correct, '业务分类完全匹配'],
] as const

export function EvaluationResultPage() {
  return <section className="page-stack">
    <header className="page-header"><div><p className="eyebrow">QUALITY / 回答质量</p><h2>用证据，衡量回答。</h2><p>查看最近一次验收快照，了解系统的表现与边界。</p></div></header>
    <section className="panel"><div className="panel-heading"><BarChart3 size={21} aria-hidden="true" /><div><h3>第四周 · 修复后验收快照</h3><p>来自已保存的真实模型评测报告，不代表当前线上实时指标。</p></div></div>
      <div className="eval-snapshot"><span>{new Date(executed_at_utc).toLocaleDateString('zh-CN')}</span><span>{model}</span><span>30 个问题 / 12 份文档</span><span>关键词检索 · Top 3</span></div>
    </section>
    <div className="metric-grid evaluation-grid">{metricItems.map(([label, value, note]) => <article className="metric-card" key={label}>
      <span>{label}</span><strong>{value.rate == null ? '—' : `${(value.rate * 100).toFixed(1)}%`}</strong><small>{value.passed} / {value.total} · {note}</small>
    </article>)}</div>
    <div className="workspace-grid"><section className="panel"><div className="panel-heading"><CheckCheck size={20} /><div><h3>本轮已验证</h3><p>财务咨询与实际申请分别处理。</p></div></div><p className="process-notice">财务制度问题生成带来源的答案；实际申请只提取用户提供的字段，确认提交后才触发模拟流程。</p></section>
      <section className="panel"><div className="panel-heading"><FlaskConical size={20} /><div><h3>持续关注</h3><p>有限样例不代表所有场景。</p></div></div><p className="process-notice">部分业务路由标签仍不匹配；申请类型同义表达仍需核对。命中引用或关键词，不等于每句话都准确。</p></section></div>
    <p className="page-footnote">该快照随前端构建更新；拒答由 Codex 逐条阅读复核。评测未执行真实审批或授权操作。</p>
    <FeedbackReviewPanel />
  </section>
}
