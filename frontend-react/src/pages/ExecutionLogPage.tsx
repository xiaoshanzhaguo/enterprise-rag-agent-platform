/*
 * 执行日志页面。
 *
 * 功能说明：
 * 1. 目前先提供日志入口和展示骨架。
 * 2. 后续用于展示 Agent 路由结果、RAG 检索记录、n8n 执行记录。
 * 3. 当前使用静态样例数据，等后端日志接口稳定后再替换为真实请求。
 */

// FileClock：文件 + 时间图标，用来表达“执行记录 / 日志”。
import { FileClock } from 'lucide-react'

// 执行日志静态样例数据。
// 后续可以替换成后端接口返回，例如 /execution_logs 或 /agent_logs。
const logRows = [
  {
    // time：这条日志发生的时间。当前只是样例，后续可换成 created_at。
    time: '09:30',
    // type：日志类型，用来区分 Agent Router、RAG Retrieval、Workflow 等步骤。
    type: 'Agent Router',
    // message：日志说明，展示这一步具体发生了什么。
    message: '识别为财务流程，自动限定检索分类为财务。',
  },
  {
    time: '09:31',
    type: 'RAG Retrieval',
    message: '命中 3 个 chunk，优先证据来自财务报销制度。',
  },
  {
    time: '09:32',
    type: 'Workflow',
    message: 'n8n 工单触发入口待接入。',
  },
]

// ExecutionLogPage：执行日志页面组件。
// 这个页面的定位是让面试官看到“系统过程可追踪”，不是只给最终回答。
export function ExecutionLogPage() {
  return (
    // page-stack：页面纵向布局容器。
    <section className="page-stack">
      {/* 页面标题区：说明日志页的业务价值。 */}
      <header className="page-header">
        <div>
          <p className="eyebrow">可观测性</p>
          <h2>执行日志</h2>
          <p>用于追踪一次员工请求从 Agent 判断、RAG 检索到流程触发的全过程。</p>
        </div>
      </header>

      {/* 日志内容面板。 */}
      <section className="panel">
        {/* 面板标题区：图标 + 标题 + 说明。 */}
        <div className="panel-heading">
          <FileClock aria-hidden="true" size={20} />
          <div>
            <h3>最近执行记录</h3>
            <p>当前为页面样例，后续接入 SQLite 中的执行记录。</p>
          </div>
        </div>

        {/* table-list：伪表格列表样式，用于展示多行日志。 */}
        <div className="table-list">
          {/* 遍历日志数组，把每条日志渲染成一行。 */}
          {logRows.map((item) => (
            // key 用 time + type 拼接，避免同一类型日志重复时 key 冲突
            // key 是 React 用来识别列表每一项的唯一标识。
            // 因为 map 返回的最外层元素是 div，所以 key 要写在这个 div 上。
            // 这里暂时用 time + type 拼接作为 key；如果后续有日志 id，优先使用 id。
            <div className="table-row" key={`${item.time}-${item.type}`}>
              {/* 第一列：日志时间。 */}
              <span>{item.time}</span>
              {/* 第二列：日志类型。 */}
              <strong>{item.type}</strong>
              {/* 第三列：日志内容。 */}
              <p>{item.message}</p>
            </div>
          ))}
        </div>
      </section>
    </section>
  )
}
