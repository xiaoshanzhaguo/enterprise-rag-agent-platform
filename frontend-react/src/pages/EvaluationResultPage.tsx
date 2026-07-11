/*
 * 评测结果页面。
 *
 * 功能说明：
 * 1. 当前先提供评测结果入口和指标展示骨架。
 * 2. 后续可以展示 eval 报告中的检索命中、引用命中、关键词覆盖、拒答准确性等指标。
 * 3. 当前使用静态指标，方便先完成企业系统布局闭环。
 */

// BarChart3：柱状图图标，用于表达“评测指标 / 数据结果”。
import { BarChart3 } from 'lucide-react'

// 评测指标静态数据。
// 当前数据来自前面 eval 的样例结果；后续可以从后端接口或 docs/eval_report_sample.md 读取。
const evalMetrics = [
  // label：指标名称；value：指标值。
  { label: '检索命中', value: '14 / 14' },
  { label: '引用命中', value: '14 / 14' },
  { label: '关键词覆盖', value: '14 / 14' },
  { label: '无依据拒答', value: '4 / 4' },
]

// EvaluationResultPage：评测结果页面组件。
// 这个页面的作用是把 RAG 效果用指标展示出来，方便项目演示和面试讲解。
export function EvaluationResultPage() {
  return (
    // page-stack：页面纵向布局容器。
    <section className="page-stack">
      {/* 页面标题区：说明评测结果页解决什么问题。 */}
      <header className="page-header">
        <div>
          <p className="eyebrow">质量评测</p>
          <h2>评测结果</h2>
          <p>用于把 RAG 效果从“能回答”变成“有指标、有证据、可复现”。</p>
        </div>
      </header>

      {/* 指标展示面板。 */}
      <section className="panel">
        {/* 面板标题区：图标 + 标题 + 数据来源说明。 */}
        <div className="panel-heading">
          <BarChart3 aria-hidden="true" size={20} />
          <div>
            <h3>当前样例指标</h3>
            <p>后续可以从 docs/eval_report_sample.md 或后端接口读取真实评测结果。</p>
          </div>
        </div>

        {/* metric-grid：指标卡片网格。 */}
        <div className="metric-grid">
          {/* 遍历指标数组，每个指标渲染成一张卡片。 */}
          {evalMetrics.map((item) => (
            // key 使用 label，因为每个指标名称唯一。
            <div className="metric-card" key={item.label}>
              {/* 指标名称。 */}
              <span>{item.label}</span>
              {/* 指标值。 */}
              <strong>{item.value}</strong>
            </div>
          ))}
        </div>
      </section>
    </section>
  )
}
