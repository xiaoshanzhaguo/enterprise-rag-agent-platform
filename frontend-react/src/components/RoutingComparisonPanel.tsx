import { GitCompareArrows } from 'lucide-react'
import before from '../../../eval/routing_before_report.json'
import after from '../../../eval/routing_after_report.json'

export function RoutingComparisonPanel() {
  const rows = [
    ['原评测集 · 路由匹配', before.summary.original.correct, after.summary.original.correct, after.summary.original.total],
    ['相似表达回归集 · 路由匹配', before.summary.holdout.correct, after.summary.holdout.correct, after.summary.holdout.total],
    ['合计 · 路由匹配', before.summary.all.correct, after.summary.all.correct, after.summary.all.total],
    ['流程意图二分类', before.summary.all.process_intent_correct, after.summary.all.process_intent_correct, after.summary.all.total],
  ]
  return <section className="panel routing-comparison" aria-label="路由专项对照">
    <div className="panel-heading"><GitCompareArrows size={20} /><div><h3>产品路由专项 · 同题对照</h3><p>修复发布治理与产品资料分类边界，检查是否影响 IT 故障和流程申请。</p></div></div>
    <div className="eval-snapshot"><span>{new Date(after.executed_at_utc).toLocaleDateString('zh-CN', { timeZone: 'Asia/Shanghai' })}</span><span>{after.model} · temperature 0</span><span>同一批 38 条虚拟问题</span></div>
    <div className="table-scroll"><table className="execution-table"><thead><tr><th>专项指标</th><th>修复前</th><th>修复后</th></tr></thead><tbody>
      {rows.map(([label, oldValue, newValue, total]) => <tr key={label}><td>{label}</td><td>{oldValue} / {total}</td><td><strong>{newValue} / {total}</strong></td></tr>)}
    </tbody></table></div>
    <p className="page-footnote">本次改善 {after.changes.filter(item => item.change === 'improved').length} 题，回归 {after.changes.filter(item => item.change === 'regressed').length} 题，模型调用失败 {after.summary.all.model_errors} 题。原评测集仍有 {after.summary.original.total-after.summary.original.correct} 个路由不匹配。</p>
    <details><summary>查看本轮修复的分类问题</summary><ul className="routing-changes">
      <li>需求冻结、灰度放量、客户问题分级和处理进度按产品治理分类。</li>
      <li>发布规范提及登录或权限时，按规范的业务主题判断；实际账号或 VPN 故障仍进入 IT 支持。</li>
      <li>模型不可用的规则分支同步补充边界，并修正 FAQ 大小写匹配。</li>
    </ul></details>
    <p className="page-footnote">这是一次真实模型路由专项，不生成完整答案，不更新上方问答质量快照。8 条相似表达为开发前固定的回归样例，不是第三方盲测。</p>
  </section>
}
