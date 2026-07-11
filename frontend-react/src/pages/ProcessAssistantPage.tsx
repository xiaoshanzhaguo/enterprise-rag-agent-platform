/*
 * 流程助手页面。
 *
 * 功能说明：
 * 1. 目前先作为企业系统布局中的业务入口。
 * 2. 后续会承接“流程类问题提取表单字段 -> n8n 触发通知/工单”的闭环。
 * 3. 当前只展示流程助手的页面骨架，不接真实提交接口。
 */

// ClipboardList：流程/清单图标，用在“流程申请草稿”标题处。
// SendHorizontal：发送图标，用在“提交流程”按钮里。
import { ClipboardList, SendHorizontal } from 'lucide-react'

// 流程卡片静态数据。
// 当前先写在前端，目的是让页面有可视化内容；后续如果后端提供流程类型列表，可以替换成接口返回。
const processCards = [
  {
    // title：流程名称，显示在右侧“常见流程”卡片中。
    title: '报销申请',
    // description：流程说明，提示这个流程大概会抽取哪些字段。
    description: '提取金额、发票、事由、审批人等字段。',
  },
  {
    title: 'IT 支持',
    description: '记录故障类型、影响范围、联系方式。',
  },
  {
    title: '权限申请',
    description: '收集系统名称、权限范围、到期时间。',
  },
]

// ProcessAssistantPage：流程助手页面组件。
// 当前页面只负责展示入口，真正的字段提取和 n8n 触发后续再接。
export function ProcessAssistantPage() {
  return (
    // page-stack：页面纵向布局容器，让标题区和内容区保持统一间距。
    <section className="page-stack">
      {/* 页面标题区：告诉用户当前页面属于“流程侧”能力。 */}
      <header className="page-header">
        <div>
          <p className="eyebrow">流程侧</p>
          <h2>流程助手</h2>
          <p>用于把员工的流程类问题转换成结构化申请，后续再触发 n8n 通知或工单。</p>
        </div>
      </header>

      {/* workspace-grid：左右两列布局。左侧写流程需求，右侧展示常见流程。 */}
      <div className="workspace-grid">
        {/* 左侧主面板：流程申请草稿。 */}
        <section className="panel">
          {/* 面板标题区：图标 + 标题 + 简短说明。 */}
          <div className="panel-heading">
            {/* aria-hidden 表示图标只是装饰，屏幕阅读器不用读它。 */}
            <ClipboardList aria-hidden="true" size={20} />
            <div>
              <h3>流程申请草稿</h3>
              <p>当前先搭建入口，下一步再接 Agent 字段提取和提交接口。</p>
            </div>
          </div>

          {/* 员工需求输入框：后续可以把这里的内容提交给 Agent 做字段提取。 */}
          <label className="field">
            <span>员工需求</span>
            {/* 当前 textarea 还不是受控组件，后续接提交逻辑时可以加 useState 保存输入内容。 */}
            <textarea placeholder="例如：我要申请 GitLab 项目权限，项目是 enterprise-rag-agent-platform。" rows={7} />
          </label>

          {/* 当前按钮先禁用，等接入字段提取和提交流程接口后再启用。 */}
          <button className="primary-button" type="button" disabled>
            <SendHorizontal aria-hidden="true" size={18} />
            提交流程
          </button>
        </section>

        {/* 右侧辅助面板：展示常见流程类型，帮助用户理解这个页面能处理什么。 */}
        <aside className="panel">
          {/* 面板标题区。 */}
          <div className="panel-heading">
            {/* WF 是 workflow 的缩写，这里作为文字图标使用。 */}
            <div className="small-icon">WF</div>
            <div>
              <h3>常见流程</h3>
              <p>这些入口后续可以和 Agent Router 的流程类型对应。</p>
            </div>
          </div>

          {/* config-list：复用知识库管理页里的列表样式。 */}
          <div className="config-list">
            {/* 遍历静态流程卡片数组，每一项渲染成一行说明。 */}
            {processCards.map((item) => (
              // key 使用 title，因为当前静态数据里 title 唯一。
              <div className="config-row" key={item.title}>
                <strong>{item.title}</strong>
                <span>{item.description}</span>
              </div>
            ))}
          </div>
        </aside>
      </div>
    </section>
  )
}
