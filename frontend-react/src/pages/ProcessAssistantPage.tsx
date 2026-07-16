/*
 * 流程助手页面。
 *
 * 功能说明：
 * 1. 目前先作为企业系统布局中的业务入口。
 * 2. 后续会承接“流程类问题提取表单字段 -> n8n 触发通知/工单”的闭环。
 * 3. 当前只展示流程助手的页面骨架，不接真实提交接口。
 * 4. 先实现轻量字段提取：用户输入 GitLab 权限申请后，页面展示结构化申请字段。
 * 5. 当前字段提取使用前端规则完成，后续可把 extractProcessFields 替换为真实 Agent 接口。
 */

// ClipboardList：流程/清单图标，用在“流程申请草稿”标题处。
// SendHorizontal：发送图标，用在“提交流程”按钮里。
// WandSparkles：字段提取图标，用在右侧“AI 提取结果”标题处。
import { ClipboardList, SendHorizontal, WandSparkles } from 'lucide-react'
// useState：保存员工输入、提取结果和输入校验错误。
import { useState } from 'react'

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

// ProcessFieldExtraction：流程助手从员工自然语言中提取出的结构化申请字段。
// 这些字段先服务于前端展示，后续也可以直接作为后端流程接口的请求参数。
interface ProcessFieldExtraction {
  // processType：申请属于哪一种企业流程，例如权限申请或普通流程申请。
  processType: string
  // targetSystem：员工需要申请权限或支持的目标系统，例如 GitLab、VPN。
  targetSystem: string
  // permissionScope：本次申请希望获得的权限范围。
  permissionScope: string
  // projectName：权限对应的项目名称。
  projectName: string
  // reason：员工提出申请的原因或用途。
  reason: string
  // urgency：申请的紧急程度，当前默认使用“普通”。
  urgency: string
  // applicant：申请人，登录用户体系接入前暂时标记为“待补充”。
  applicant: string
  // status：流程草稿当前所处状态。
  status: string
}

// extractProjectName：从“项目是 xxx”或“项目名称为 xxx”一类表达中提取项目名。
function extractProjectName(text: string) {
  // 正则允许项目名包含英文、数字、下划线、点、斜杠和短横线，覆盖常见仓库命名方式。
  const projectMatch = text.match(/项目(?:名称)?(?:是|为|:|：)\s*([A-Za-z0-9_.\-/]+)/i)

  // 找到项目名时返回第一个捕获组；没有找到时明确提示用户后续补充。
  // 为什么取[1]，不是取[0]？因为 text.match(...) 返回的是一个数组。这个数组里，下标 0：表示整个正则匹配到的完整内容。、下标 1：表示第一个捕获组匹配到的内容。
  return projectMatch?.[1] ?? '待补充'
}

// extractReason：从“因为、用于、方便、为了”后面的文本中提取申请原因。
function extractReason(text: string) {
  // 原因遇到句号或分号时结束，避免把后续其他字段一起放进申请原因。
  const reasonMatch = text.match(/(?:因为|用于|方便|为了)\s*([^。；;]+)/)

  if (!reasonMatch) {
    if (text.includes('参与') && text.includes('项目开发')) {
      return '参与项目开发'
    }

    if (text.includes('排查') && text.includes('线上问题')) {
      return '排查线上问题'
    }
  }

  // 当前没有识别到原因时返回“待补充”，让结果面板清楚暴露缺失字段。
  // trim 的作用：去掉字符串开头和结尾的空格、换行、Tab。
  // 为什么要 trim()? 因为原因捕获组 ([^。；;]+) 会匹配比较宽泛的内容，可能把原因前后的空格、换行也一起提取出来。
  return reasonMatch?.[1]?.trim() ?? '待补充'
}

// extractProcessFields：把员工输入转换成页面需要展示的结构化申请字段。
function extractProcessFields(inputText: string): ProcessFieldExtraction {
  // 转成小写后再判断英文系统名，兼容 GitLab、gitlab 等不同写法。
  const normalizedInput = inputText.toLowerCase()
  // 优先识别 GitLab，其次识别 VPN；都没有命中时等待员工补充目标系统。
  const targetSystem = normalizedInput.includes('gitlab')
    ? 'GitLab'
    : normalizedInput.includes('vpn')
      ? 'VPN'
      : '待补充'
  // 识别到目标系统时，本次需求通常属于权限申请。
  const processType = targetSystem !== '待补充' ? '权限申请' : '流程申请'
  // GitLab 权限通常绑定具体项目；其他系统的权限范围暂时等待补充。
  const permissionScope = targetSystem === 'GitLab' ? '项目权限' : '待补充'

  // 返回统一结构，右侧结果面板只负责展示，不需要再次判断业务规则。
  return {
    processType,
    targetSystem,
    permissionScope,
    projectName: extractProjectName(inputText),
    reason: extractReason(inputText),
    urgency: '普通',
    applicant: '待补充',
    status: '草稿',
  }
}

// ProcessAssistantPage：流程助手页面组件。
// 当前页面只负责展示入口，真正的字段提取和 n8n 触发后续再接。
export function ProcessAssistantPage() {
  // processInput：保存 textarea 中当前输入的员工需求。
  const [processInput, setProcessInput] = useState('')
  // extractedFields：保存最近一次字段提取结果；null 表示还没有执行提取。
  const [extractedFields, setExtractedFields] = useState<ProcessFieldExtraction | null>(null)
  // errorMessage：保存输入校验错误，避免空内容进入提取逻辑。
  const [errorMessage, setErrorMessage] = useState('')

  // handleExtractFields：点击按钮后校验输入，并生成结构化申请字段。
  function handleExtractFields() {
    // trim 去掉首尾空格，避免只输入空格时仍然执行字段提取。
    const trimmedInput = processInput.trim()

    // 空输入不执行提取，并向用户展示可理解的错误提示。
    if (!trimmedInput) {
      setErrorMessage('请先输入需要办理的流程，例如：我要申请 GitLab 权限。')
      setExtractedFields(null)
      return
    }

    // 输入合法后清除旧错误，再保存本次提取结果。
    setErrorMessage('')
    setExtractedFields(extractProcessFields(trimmedInput))
  }

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
              {/* 当前先在前端完成轻量字段提取，暂不提交真实流程。 */}
              <p>输入自然语言需求，系统会先整理成可确认的申请字段。</p>
            </div>
          </div>

          {/* 员工需求输入框：后续可以把这里的内容提交给 Agent 做字段提取。 */}
          <label className="field">
            <span>员工需求</span>
            {/* 当前 textarea 还不是受控组件，后续接提交逻辑时可以加 useState 保存输入内容。 */}
            {/* textarea 已改为受控组件，输入内容保存在 processInput 状态中。 */}
            <textarea
              placeholder="例如：我要申请 GitLab 项目权限，项目是 enterprise-rag-agent-platform。"
              rows={7}
              value={processInput}
              onChange={(event) => setProcessInput(event.target.value)}
            />
          </label>

          {/* 输入为空时显示错误提示；没有错误时不占用页面空间。 */}
          {errorMessage ? <div className="alert error">{errorMessage}</div> : null}

          {/* 当前按钮先禁用，等接入字段提取和提交流程接口后再启用。 */}
          {/* 扩展：按钮现在只触发字段提取，不会真正创建或提交工单。 */}
          <button className="primary-button" type="button" disabled={!processInput.trim()} onClick={handleExtractFields}>
            <SendHorizontal aria-hidden="true" size={18} />
            提取申请字段
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

          {/* 扩展：字段提取后优先展示结构化结果，便于员工确认缺失信息。 */}
          {/* aria-live 是一个无障碍属性。它的作用是告诉屏幕阅读器：这个区域的内容可能会动态变化；如果内容变了，请在合适的时候提醒用户；但不要打断用户当前正在听的内容。 */}
          {/* 放到代码里，意思是：AI 提取结果这个区域是一个动态更新区域。当 extractedFields 从无到有，页面展示出提取结果时，屏幕阅读器可以温和地提醒用户这里有新内容。 */}
          {/* aria-live="polite"：字段提取结果更新时，允许屏幕阅读器在不打断用户的情况下播报变化。 */}
          <section className="process-extraction-panel" aria-live="polite">
            {/* 提取结果标题区：图标只用于辅助识别，不重复朗读。 */}
            <div className="process-extraction-heading">
              <WandSparkles aria-hidden="true" size={18} />
              <div>
                <strong>AI 提取结果</strong>
                <span>{extractedFields ? '字段已整理，请确认待补充内容。' : '等待提取申请字段。'}</span>
              </div>
            </div>

            {/* 有提取结果时展示字段列表；首次进入页面时展示简洁空状态。 */}
            {extractedFields ? (
              <dl className="process-field-list">
                {/* 每一个 div 表示一个“字段名 + 字段值”的结构化申请字段。 */}
                <div>
                  <dt>流程类型</dt>
                  <dd>{extractedFields.processType}</dd>
                </div>
                <div>
                  <dt>目标系统</dt>
                  <dd>{extractedFields.targetSystem}</dd>
                </div>
                <div>
                  <dt>权限范围</dt>
                  <dd>{extractedFields.permissionScope}</dd>
                </div>
                <div>
                  <dt>项目名称</dt>
                  <dd>{extractedFields.projectName}</dd>
                </div>
                <div>
                  <dt>申请原因</dt>
                  <dd>{extractedFields.reason}</dd>
                </div>
                <div>
                  <dt>紧急程度</dt>
                  <dd>{extractedFields.urgency}</dd>
                </div>
                <div>
                  <dt>申请人</dt>
                  <dd>{extractedFields.applicant}</dd>
                </div>
                <div>
                  <dt>当前状态</dt>
                  <dd>{extractedFields.status}</dd>
                </div>
              </dl>
            ) : (
              <p className="muted-text">输入“我要申请 GitLab 权限”，点击提取后可查看结构化字段。</p>
            )}
          </section>

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
