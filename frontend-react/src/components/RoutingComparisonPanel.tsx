import { useState } from 'react'
import { GitCompareArrows } from 'lucide-react'
import productBefore from '../../../eval/routing_before_report.json'
import productAfter from '../../../eval/routing_after_report.json'
import securityBefore from '../../../eval/security_routing_before_report.json'
import securityAfter from '../../../eval/security_routing_after_report.json'

interface Score { correct: number; total: number; process_intent_correct: number; model_errors: number }
interface Report {
  model: string; executed_at_utc: string; summary: Record<string, Score>
  changes?: Array<{ change: string }>
}
const experiments: Record<string, { title: string; before: Report; after: Report; scope: string; notes: string[] }> = {
  security: {
    title: '安全规范路由专项', before: securityBefore, after: securityAfter, scope: '原 30 题 + 产品 8 题 + 安全 8 题',
    notes: ['公司源码、客户业务数据与设备使用限制按企业 IT 安全制度分类。',
      '发布前的安全检查仍按产品发布治理处理；普通源码讲解不因代码关键词被归为企业安全制度。',
      '仍保留财务系统凭据问题的分类交叉，不修改金标提高指标。'],
  },
  product: {
    title: '产品路由专项', before: productBefore, after: productAfter, scope: '原 30 题 + 产品相似表达 8 题',
    notes: ['需求冻结、灰度放量、客户问题分级和处理进度按产品治理分类。',
      '发布规范提及登录或权限时按业务主题分类；实际账号或 VPN 故障仍进入 IT 支持。',
      '模型不可用的规则分支同步补充边界，并修正 FAQ 大小写匹配。'],
  },
}

export function RoutingComparisonPanel() {
  const [selected, setSelected] = useState('security')
  const experiment = experiments[selected]
  const { before, after } = experiment
  const rows = [
    ['原评测集 · 路由匹配', before.summary.original.correct, after.summary.original.correct, after.summary.original.total],
    ['产品相似表达 · 路由匹配', before.summary.holdout.correct, after.summary.holdout.correct, after.summary.holdout.total],
    ...(after.summary.security ? [['安全规范回归 · 路由匹配', before.summary.security.correct, after.summary.security.correct, after.summary.security.total]] : []),
    ['合计 · 路由匹配', before.summary.all.correct, after.summary.all.correct, after.summary.all.total],
    ['流程意图二分类', before.summary.all.process_intent_correct, after.summary.all.process_intent_correct, after.summary.all.total],
  ]
  return <section className="panel routing-comparison" aria-label="路由专项对照">
    <div className="panel-heading"><GitCompareArrows size={20} /><div><h3>{experiment.title} · 同题对照</h3><p>使用固定问题检查改进与回归，可切换查看历轮实测。</p></div></div>
    <label className="filter-field"><span className="sr-only">路由专项版本</span><select value={selected} onChange={event => setSelected(event.target.value)}>
      <option value="security">安全规范路由专项</option><option value="product">产品路由专项</option></select></label>
    <div className="eval-snapshot"><span>{new Date(after.executed_at_utc).toLocaleDateString('zh-CN', { timeZone: 'Asia/Shanghai' })}</span><span>{after.model} · temperature 0</span><span>{experiment.scope}</span></div>
    <div className="table-scroll"><table className="execution-table"><thead><tr><th>专项指标</th><th>修复前</th><th>修复后</th></tr></thead><tbody>
      {rows.map(([label, oldValue, newValue, total]) => <tr key={label}><td>{label}</td><td>{oldValue} / {total}</td><td><strong>{newValue} / {total}</strong></td></tr>)}
    </tbody></table></div>
    <p className="page-footnote">本次改善 {after.changes?.filter(item => item.change === 'improved').length || 0} 题，回归 {after.changes?.filter(item => item.change === 'regressed').length || 0} 题，模型调用失败 {after.summary.all.model_errors} 题。原评测集仍有 {after.summary.original.total-after.summary.original.correct} 个路由不匹配。</p>
    <details key={selected}><summary>查看本轮修复的分类问题</summary><ul className="routing-changes">{experiment.notes.map(note => <li key={note}>{note}</li>)}</ul></details>
    <p className="page-footnote">真实模型路由专项，不生成完整答案，不更新上方问答质量快照。额外问题为开发前固定的回归样例，不是第三方盲测。</p>
  </section>
}
