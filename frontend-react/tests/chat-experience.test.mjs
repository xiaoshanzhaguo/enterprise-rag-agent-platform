import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

async function moduleUrl(path, rewrite = source => source) {
  const source = rewrite(await readFile(new URL(path, import.meta.url), 'utf8'))
  const { outputText } = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } })
  const compiled = outputText.replaceAll('"react/jsx-runtime"', JSON.stringify(new URL('../node_modules/react/jsx-runtime.js', import.meta.url).href))
    .replace(/(['"])react\1/g, JSON.stringify(new URL('../node_modules/react/index.js', import.meta.url).href))
  return `data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`
}
const messages = await moduleUrl('../src/api/sessionMessages.ts')
const { shouldSendOnEnter, applyAnswerEvent, visibleAnswer, interruptAnswer, findRecoveredAnswer } = await import(await moduleUrl('../src/api/chatPresentation.ts',
  source => source.replace("'./sessionMessages'", JSON.stringify(messages))))
const http = await moduleUrl('../src/api/httpClient.ts', source => source.replace('import.meta.env.VITE_API_BASE_URL?.trim()', 'undefined'))
const { fetchSessionPage, mergeSessionSummaries, createSession, generateSessionTitle } = await import(await moduleUrl('../src/api/sessionApi.ts',
  source => source.replace("'./httpClient'", JSON.stringify(http))))
const { AnswerSources } = await import(await moduleUrl('../src/components/AnswerSources.tsx',
  source => source.replace("'lucide-react'", JSON.stringify('data:text/javascript,export const FileText=()=>null'))))
const icons = 'data:text/javascript,export const Check=()=>null;export const ChevronDown=()=>null;export const ThumbsUp=()=>null;export const ThumbsDown=()=>null;export const MessageSquare=()=>null'
const dialog = await moduleUrl('../src/components/SessionActionDialog.tsx',
  source => source.replace("'lucide-react'", JSON.stringify('data:text/javascript,export const X=()=>null')))
const feedbackApi = await moduleUrl('../src/api/feedbackApi.ts', source => source.replace("'./httpClient'", JSON.stringify(http)))
const { AnswerFeedbackForm } = await import(await moduleUrl('../src/components/AnswerFeedbackForm.tsx', source => source
  .replace("'lucide-react'", JSON.stringify(icons)).replace("'../api/feedbackApi'", JSON.stringify(feedbackApi))
  .replace("'../api/httpClient'", JSON.stringify(http)).replace("'./SessionActionDialog'", JSON.stringify(dialog))))

test('仅非空内容的普通Enter发送，Shift+Enter换行', () => {
  assert.equal(shouldSendOnEnter({ key: 'Enter' }, '怎么报销？', false), true)
  assert.equal(shouldSendOnEnter({ key: 'Enter', shiftKey: true }, '怎么报销？', false), false)
  assert.equal(shouldSendOnEnter({ key: 'Enter' }, ' \n　', false), false)
  assert.equal(shouldSendOnEnter({ key: 'Enter' }, '内容', true), false)
  assert.equal(shouldSendOnEnter({ key: 'x' }, '内容', false), false)
})

test('中文输入法确认、长按及组合键不会误发', () => {
  for (const extra of [{ isComposing: true }, { keyCode: 229 }, { repeat: true }, { ctrlKey: true }, { metaKey: true }, { altKey: true }]) {
    assert.equal(shouldSendOnEnter({ key: 'Enter', ...extra }, '内容', false), false)
  }
})

test('隐藏诊断不丢失流式正文、引用、执行记录或持久消息编号', () => {
  let message = { id: 'a', role: 'assistant', content: '', status: 'streaming' }
  message = applyAnswerEvent(message, { event_type: 'delta', content: '正文' })
  const metadata = { rag_preview_chunks: [{ source: 'policy.md#chunk-1', text: '证据' }], execution_trace: { trace_id: 'trace' } }
  message = applyAnswerEvent(message, { event_type: 'final', message_id: 42, metadata })
  assert.equal(message.content, '正文')
  assert.equal(message.messageId, 42)
  assert.deepEqual(message.metadata, metadata)
  assert.equal(message.status, 'complete')
})

test('服务端错误保持失败状态且不会自动重试', () => {
  const message = applyAnswerEvent({ id: 'a', role: 'assistant', content: '', status: 'streaming' },
    { event_type: 'error', error_message: '暂不可用' })
  assert.equal(message.status, 'error')
  assert.equal(message.content, '')
  assert.equal(message.notice, '暂不可用')
})

test('分页合并去重，迟到的历史页不覆盖更新后的标题', () => {
  const current = [{ session_id: 'a', title: '新标题' }]
  assert.deepEqual(mergeSessionSummaries(current, [{ session_id: 'a', title: '旧标题' }, { session_id: 'b' }, { session_id: 'b' }]),
    [{ session_id: 'a', title: '新标题' }, { session_id: 'b' }])
  assert.equal(current.length, 1)
})

test('分页请求携带原来的模式、空会话设置与游标，不触发写请求', async () => {
  const original = globalThis.fetch
  let actual
  globalThis.fetch = async (url, options) => { actual = { url, options }; return new Response(JSON.stringify({ sessions: [], next_cursor: null }), { headers: { 'content-type': 'application/json' } }) }
  try {
    await fetchSessionPage(undefined, 'cursor+/=')
    const url = new URL(actual.url, 'http://localhost')
    assert.equal(url.pathname, '/api/chat_session_pages')
    assert.equal(url.searchParams.get('cursor'), 'cursor+/=')
    assert.equal(url.searchParams.get('mode'), '企业知识库问答')
    assert.equal(url.searchParams.get('include_empty'), 'true')
    assert.equal(actual.options.method, undefined)
  } finally { globalThis.fetch = original }
})

test('员工参考资料默认折叠，保留原文但不展示检索分数或Agent诊断', () => {
  const markup = renderToStaticMarkup(createElement(AnswerSources, { sources: [{ file_name: '财务制度.md', chunk_id: 2,
    source: '财务制度.md#chunk-2', text: '提交发票和行程单', score: 0.9473, retrieval_mode: 'vector' }] }))
  assert.match(markup, /参考资料/)
  assert.match(markup, /财务制度/)
  assert.match(markup, /提交发票和行程单/)
  assert.doesNotMatch(markup, /\bopen=|0\.9473|Agent|vector/)
})

test('没有参考片段时不生成空资料面板', () => {
  assert.equal(renderToStaticMarkup(createElement(AnswerSources, { sources: [] })), '')
})

test('仅展示时隐藏来源标记，保留普通方括号与原始答案', () => {
  const raw = '提供发票和行程单。[来源: 财务制度.md#chunk-6]\n通用材料。[来源：reimbursement_policy.md#chunk-3]'
  assert.equal(visibleAnswer(raw), '提供发票和行程单。\n通用材料。')
  assert.match(raw, /来源/)
  assert.equal(visibleAnswer('说明[注意]【来源：制度.md#chunk-1】'), '说明[注意]')
  assert.equal(visibleAnswer('材料。[来源: 财务', true), '材料。')
  assert.equal(visibleAnswer('普通内容'), '普通内容')
})

test('创建会话使用后端返回的复用草稿ID', async () => {
  const original = globalThis.fetch
  let request
  globalThis.fetch = async (url, options) => {
    request = { url, options }
    return new Response(JSON.stringify({ session_id: 'existing-draft' }), { headers: { 'content-type': 'application/json' } })
  }
  try {
    assert.equal((await createSession('candidate')).session_id, 'existing-draft')
    assert.equal(request.url, '/api/chat_session_draft')
    assert.equal(JSON.parse(request.options.body).session_id, 'candidate')
  } finally { globalThis.fetch = original }
})

test('历史反馈恢复后两个选择均锁定，不能再次编辑', () => {
  for (const rating of ['helpful', 'unhelpful']) {
    const markup = renderToStaticMarkup(createElement(AnswerFeedbackForm, { sessionId: 'a', messageId: 2,
      initialFeedback: { rating, reason: rating === 'unhelpful' ? 'incomplete' : '', comment: '', updated_at: 'now' } }))
    assert.equal((markup.match(/disabled=""/g) || []).length, 2)
    assert.match(markup, /反馈已记录/)
    assert.doesNotMatch(markup, /<form/)
  }
})

test('未反馈回答仍允许选择，未选择时不显示多余表单', () => {
  const markup = renderToStaticMarkup(createElement(AnswerFeedbackForm, { sessionId: 'a', messageId: 2 }))
  assert.doesNotMatch(markup, /disabled=""|<form|缺少审批/)
  assert.match(markup, /回复优秀/)
  assert.match(markup, /回复不佳/)
})

test('补全标题仅传会话ID，由后端读取首条问题', async () => {
  const original = globalThis.fetch
  let request
  globalThis.fetch = async (url, options) => {
    request = { url, options }
    return new Response(JSON.stringify({ title: '打车报销材料' }), { headers: { 'content-type': 'application/json' } })
  }
  try {
    const controller = new AbortController()
    assert.equal((await generateSessionTitle('chat/a', controller.signal)).title, '打车报销材料')
    assert.equal(request.url, '/api/chat_session/chat%2Fa/auto_title')
    assert.deepEqual(JSON.parse(request.options.body), { session_id: 'chat/a' })
    assert.equal(request.options.signal, controller.signal)
  } finally { globalThis.fetch = original }
})

test('断线、手动停止与服务端错误均保留已经生成的正文', () => {
  const partial = { id: 'a', role: 'assistant', content: '请准备发票', status: 'streaming' }
  for (const message of [interruptAnswer(partial, '已停止接收'),
    applyAnswerEvent(partial, { event_type: 'error', error_message: '上游中断' })]) {
    assert.equal(message.content, '请准备发票')
    assert.equal(message.status, 'error')
    assert.ok(message.notice)
  }
  const complete = { ...partial, status: 'complete', messageId: 12 }
  assert.equal(interruptAnswer(complete, '迟到的错误'), complete)
})

test('恢复必须匹配本轮问题和数据库消息ID，不能误用旧答案或其他问题', () => {
  const messages = [
    { role: 'user', content: '报销？', messageId: 1, status: 'complete' },
    { role: 'assistant', content: '旧答案', messageId: 2, status: 'complete' },
    { role: 'user', content: '报销？', messageId: 3, status: 'complete' },
  ]
  assert.equal(findRecoveredAnswer(messages, '报销？', 2), undefined)
  const answer = { role: 'assistant', content: '新答案', messageId: 4, status: 'complete' }
  assert.equal(findRecoveredAnswer([...messages, answer], '报销？', 2), answer)
  assert.equal(findRecoveredAnswer([...messages, answer], '别的问题', 2), undefined)
  assert.equal(findRecoveredAnswer([...messages, answer, { role: 'user', content: '报销？', messageId: 5 }], '报销？', 2), undefined)
  assert.equal(findRecoveredAnswer([...messages, { role: 'user', content: '新问题', messageId: 4 }, answer], '报销？', 2), undefined)
})
