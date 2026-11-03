import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { afterEach, test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ts from 'typescript'

// 沿用现有 Node test + TypeScript 加载方式，测试实际请求、状态控制器和 React 状态组件。
async function moduleUrl(relative, rewrite = source => source) {
  const source = rewrite(await readFile(new URL(relative, import.meta.url), 'utf8'))
  const { outputText } = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } })
  return `data:text/javascript;base64,${Buffer.from(outputText.replace('"react/jsx-runtime"',
    JSON.stringify(new URL('../node_modules/react/jsx-runtime.js', import.meta.url).href))).toString('base64')}`
}
const httpUrl = await moduleUrl('../src/api/httpClient.ts', source => source.replace('import.meta.env.VITE_API_BASE_URL?.trim()', 'undefined'))
const { ApiError } = await import(httpUrl)
const stateUrl = await moduleUrl('../src/api/knowledgeIndexState.ts', source => source.replace("'./httpClient'", JSON.stringify(httpUrl)))
const { KnowledgeIndexController, indexPresentation } = await import(stateUrl)
const api = await import(await moduleUrl('../src/api/knowledgeBaseApi.ts', source => source.replace("'./httpClient'", JSON.stringify(httpUrl))))
const { DocumentIndexStatus } = await import(await moduleUrl('../src/components/DocumentIndexStatus.tsx',
  source => source.replace("'../api/knowledgeIndexState'", JSON.stringify(stateUrl))))

const originalFetch = globalThis.fetch
const active = new Set()
afterEach(() => { globalThis.fetch = originalFetch; active.forEach(controller => controller.dispose()); active.clear() })
const scope = { session_id: 'admin session', knowledge_base_id: 'finance/kb', user_role: 'kb_admin' }
const flush = () => new Promise(resolve => setImmediate(resolve))
function deferred() { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
function vector(status = 'pending', overrides = {}) {
  return { operation_id: 'operation-a', status, state_version: 1, attempt_count: 0, retry_count: 0,
    execution_claimed: false, current_config_compatible: true, runtime_validation_required: status === 'pending',
    resumable: status === 'pending', retryable: status === 'failed', error_code: null, error_message: null,
    updated_at: '2026-11-03', status_url: '/unused', resume_url: '/unused', retry_url: '/unused', ...overrides }
}
function receipt(index = vector('ready'), overrides = {}) {
  return { operation_id: 'operation-a', document_id: 7, file_name: 'policy.md', knowledge_base_id: scope.knowledge_base_id,
    text_status: 'persisted', state_confirmed: true, vector_index: index, status_url: '/unused',
    reason: 'ready', resume_applied: false, retry_applied: false, ...overrides }
}
function uploadReceipt(index = vector('ready'), overrides = {}) {
  return { ...receipt(index), session_id: scope.session_id, chunk_count: 2, knowledge_base_type: 'finance',
    process_status: 'active', effective_retrieval_mode: 'vector', document_reused: false, ...overrides }
}
function list(index = vector('pending')) {
  return { session_id: scope.session_id, knowledge_base_id: scope.knowledge_base_id, has_document: true,
    document_count: 1, chunk_count: 2, file_names: ['policy.md'], expires_in_seconds: 0,
    documents: [{ document_id: 7, file_name: 'policy.md', chunk_count: 2, knowledge_base_type: 'finance', vector_index: index }] }
}
function render(index, overrides = {}) {
  return renderToStaticMarkup(createElement(DocumentIndexStatus, { index, onQuery() {}, onExecute() {}, ...overrides }))
}
function harness(initial = vector('pending')) {
  let server = list(initial), sequence = 0
  const jobs = new Map(), calls = []
  const scheduler = { set(callback) { jobs.set(++sequence, callback); return sequence }, clear(id) { jobs.delete(id) } }
  const fake = {
    async fetchRagStatus(session, kb) { calls.push(['list', session, kb]); return structuredClone(server) },
    async fetchIndexOperation(id, context) { calls.push(['get', id, context]); return receipt(structuredClone(server.documents[0].vector_index)) },
    async indexKnowledgeBaseDocument(request) { calls.push(['upload', request]); return uploadReceipt(structuredClone(server.documents[0].vector_index)) },
    async resumeIndexOperation(id, version, context) {
      calls.push(['resume', id, version, context]); server = list(vector('ready', { state_version: version + 2, resumable: false }))
      return receipt(server.documents[0].vector_index, { resume_applied: true })
    },
    async retryIndexOperation(id, version, context) {
      calls.push(['retry', id, version, context]); server = list(vector('ready', { state_version: version + 3 }))
      return receipt(server.documents[0].vector_index, { retry_applied: true })
    },
  }
  const controller = new KnowledgeIndexController(scope, fake, scheduler)
  active.add(controller)
  return { controller, fake, calls, jobs, setServer(value) { server = value },
    async start() { controller.start(); await controller.refresh() },
    async tick() { const job = jobs.entries().next().value; if (!job) return false; jobs.delete(job[0]); job[1](); await flush(); return true } }
}

test('React 状态组件覆盖 ready、未启用、未领取、已领取、失败与历史未知状态', () => {
  assert.match(render(vector('ready')), /索引成功/)
  assert.doesNotMatch(render(vector('ready')), /继续执行|重试索引/)
  assert.match(render(vector('not_requested')), /未启用向量索引/)
  assert.match(render(vector('pending')), /待执行.*继续执行/s)
  const claimed = render(vector('pending', { execution_claimed: true }))
  assert.match(claimed, /索引中.*查询状态/s)
  assert.doesNotMatch(claimed, /继续执行|重试索引/)
  assert.match(render(vector('failed', { error_message: '向量生成超时。' })), /索引失败.*重试索引.*向量生成超时/s)
  assert.match(render(undefined), /历史文档未记录/)
  assert.doesNotMatch(render(vector('failed', { retryable: false })), /重试索引/)
  assert.doesNotMatch(render(vector('pending', { resumable: false })), />继续执行<\/button>/)
})

test('状态未确认仍可查询，不提供执行或人工 recover 按钮', () => {
  const markup = render(null, { uncertain: true, operationId: 'operation-a' })
  assert.match(markup, /状态未确认.*查询状态/s)
  assert.doesNotMatch(markup, /继续执行|重试索引|人工恢复|attempt_token/)
})

test('实际 API 封装处理 202、作用域、版本与 409 回执，不注入覆盖字段', async () => {
  const requests = []
  globalThis.fetch = async (path, options) => {
    requests.push([path, options])
    return Response.json(receipt(vector('pending', { execution_claimed: true })), { status: 202 })
  }
  const result = await api.resumeIndexOperation('id/with space', 4, scope)
  assert.equal(result.vector_index.execution_claimed, true)
  const [path, options] = requests[0]
  const url = new URL(path, 'http://test')
  assert.equal(url.pathname, '/api/rag_index_operations/id%2Fwith%20space/resume')
  assert.equal(url.searchParams.get('knowledge_base_id'), scope.knowledge_base_id)
  assert.equal(url.searchParams.get('user_role'), 'kb_admin')
  assert.deepEqual(JSON.parse(options.body), { expected_state_version: 4 })
  assert.ok(options.signal)
  await api.retryIndexOperation('operation-a', 6, scope)
  await api.fetchIndexOperation('operation-a', scope)
  await api.fetchRagStatus(scope.session_id, scope.knowledge_base_id)
  await api.indexKnowledgeBaseDocument({ session_id: scope.session_id, document_text: 'text' })
  assert.match(requests[1][0], /\/retry\?/)
  assert.equal(requests[2][1].method, undefined)
  assert.match(requests[3][0], /rag_status\/admin%20session/)
  assert.equal(requests[4][0], '/api/index_document')
  globalThis.fetch = async () => Response.json({ detail: '版本已变化', receipt: receipt(vector('ready')) }, { status: 409 })
  await assert.rejects(api.retryIndexOperation('operation-a', 1, scope), error => error.status === 409 && error.payload.receipt.vector_index.status === 'ready')
})

test('页面刷新从 rag_status 恢复状态，上传 ready 显示成功，查询失败保留回执', async () => {
  const h = harness(vector('ready')); await h.start()
  assert.match(render(h.controller.getSnapshot().status.documents[0].vector_index), /索引成功/)
  await h.controller.upload({ session_id: scope.session_id, document_text: 'text' })
  h.fake.fetchRagStatus = async () => { throw new Error('offline') }
  await h.controller.refresh()
  assert.equal(h.controller.getSnapshot().upload.text_status, 'persisted')
  assert.equal(h.controller.getSnapshot().upload.vector_index.status, 'ready')
  assert.equal(h.controller.getSnapshot().status.document_count, 1)
  assert.match(h.controller.getSnapshot().queryError, /保留上次/)
})

test('202 pending 自动轮询仅 GET，完成后停止，不自动 resume', async () => {
  const h = harness(); await h.start()
  await h.controller.upload({ session_id: scope.session_id, document_text: 'text' })
  assert.equal(h.controller.getSnapshot().upload.vector_index.status, 'pending')
  assert.equal(h.jobs.size, 1)
  h.setServer(list(vector('ready', { state_version: 4 })))
  await h.tick()
  assert.equal(h.controller.getSnapshot().upload.vector_index.status, 'ready')
  assert.equal(h.jobs.size, 0)
  assert.equal(h.calls.filter(call => ['resume', 'retry'].includes(call[0])).length, 0)
})

test('未领取任务显式 resume，双击只发一次，提交当前 state_version', async () => {
  const h = harness(); await h.start()
  const pending = deferred()
  h.fake.resumeIndexOperation = async (id, version) => { h.calls.push(['resume', id, version]); return pending.promise }
  const running = h.controller.execute('operation-a', 'resume')
  await h.controller.execute('operation-a', 'resume')
  assert.deepEqual(h.controller.getSnapshot().busy, ['operation-a'])
  assert.equal(h.calls.filter(call => call[0] === 'resume').length, 1)
  h.setServer(list(vector('ready', { state_version: 4 })))
  pending.resolve(receipt(vector('ready', { state_version: 4 })))
  await running
  assert.equal(h.calls.find(call => call[0] === 'resume')[2], 1)
  assert.equal(h.controller.getSnapshot().status.documents[0].vector_index.status, 'ready')
  assert.deepEqual(h.controller.getSnapshot().busy, [])
})

test('pending 已领取只可查询，即使后端动作字段矛盾也不会重复执行', async () => {
  const h = harness(vector('pending', { execution_claimed: true })); await h.start()
  await h.controller.execute('operation-a', 'resume')
  await h.controller.execute('operation-a', 'retry')
  await h.controller.query('operation-a')
  assert.equal(h.calls.filter(call => ['resume', 'retry'].includes(call[0])).length, 0)
})

test('failed 显示原因，显式重试成功后刷新状态且停止轮询', async () => {
  const h = harness(vector('failed', { state_version: 5, error_message: '向量生成失败。' })); await h.start()
  assert.match(render(h.controller.getSnapshot().status.documents[0].vector_index), /向量生成失败/)
  await h.controller.execute('operation-a', 'retry')
  assert.equal(h.calls.find(call => call[0] === 'retry')[2], 5)
  assert.equal(h.controller.getSnapshot().status.documents[0].vector_index.status, 'ready')
  assert.equal(h.jobs.size, 0)
})

test('409 使用回执并 GET 对账，不使用新版本自动重试', async () => {
  const h = harness(); await h.start()
  h.fake.resumeIndexOperation = async () => {
    h.calls.push(['resume'])
    h.setServer(list(vector('ready', { state_version: 8 })))
    throw new ApiError('conflict', 409, { detail: '版本变化', receipt: receipt(vector('pending', { state_version: 6, execution_claimed: true })) })
  }
  await h.controller.execute('operation-a', 'resume')
  assert.equal(h.controller.getSnapshot().status.documents[0].vector_index.status, 'ready')
  assert.match(h.controller.getSnapshot().error, /状态或版本已变化/)
  assert.equal(h.calls.filter(call => call[0] === 'resume').length, 1)
  assert.equal(h.calls.filter(call => call[0] === 'retry').length, 0)
})

test('502 上传错误保留已保存文档与失败回执，重试后上传卡片同步更新', async () => {
  const h = harness(vector('failed')); await h.start()
  h.fake.indexKnowledgeBaseDocument = async () => { throw new ApiError('upstream', 502,
    { detail: '文本已保存，向量索引失败。', receipt: uploadReceipt(vector('failed')) }) }
  await h.controller.upload({ session_id: scope.session_id, document_text: 'text' })
  assert.equal(h.controller.getSnapshot().upload.document_id, 7)
  assert.equal(h.controller.getSnapshot().upload.vector_index.status, 'failed')
  await h.controller.execute('operation-a', 'retry')
  assert.equal(h.controller.getSnapshot().upload.vector_index.status, 'ready')
})

test('503 状态未确认只查询，查询恢复前禁止二次提交', async () => {
  const h = harness(); await h.start()
  h.fake.resumeIndexOperation = async () => { h.calls.push(['resume']); throw new ApiError('unconfirmed', 503,
    { receipt: receipt(null, { state_confirmed: false }) }) }
  h.fake.fetchIndexOperation = async () => { throw new Error('offline') }
  await h.controller.execute('operation-a', 'resume')
  assert.deepEqual(h.controller.getSnapshot().uncertain, ['operation-a'])
  assert.equal(h.controller.getSnapshot().uncertainDocuments[7], 'operation-a')
  await h.controller.execute('operation-a', 'resume')
  assert.equal(h.calls.filter(call => call[0] === 'resume').length, 1)
  h.fake.fetchIndexOperation = async () => receipt(vector('ready', { state_version: 5 }))
  await h.tick()
  assert.deepEqual(h.controller.getSnapshot().uncertain, [])
  assert.equal(h.controller.getSnapshot().status.documents[0].vector_index.status, 'ready')
  assert.equal(h.controller.getSnapshot().error, '')
})

test('上传网络超时不自动或重复上传，允许先刷新核对列表', async () => {
  const h = harness(); await h.start()
  h.fake.indexKnowledgeBaseDocument = async () => { h.calls.push(['upload']); throw new DOMException('late', 'TimeoutError') }
  const request = { session_id: scope.session_id, document_text: 'text' }
  await h.controller.upload(request)
  await h.controller.upload(request)
  assert.equal(h.calls.filter(call => call[0] === 'upload').length, 1)
  assert.equal(h.controller.getSnapshot().uploadUncertain, true)
  assert.match(h.controller.getSnapshot().error, /勿重复提交/)
  await h.controller.refresh()
  assert.equal(h.controller.getSnapshot().uploadUncertain, true)
})

test('切换知识库或卸载取消轮询，迟到的 GET 不更新旧页面', async () => {
  const h = harness(); await h.start()
  const pending = deferred()
  h.fake.fetchIndexOperation = async () => pending.promise
  const querying = h.controller.query('operation-a')
  const previous = h.controller.getSnapshot()
  h.controller.dispose()
  pending.resolve(receipt(vector('ready')))
  await querying
  assert.equal(h.controller.getSnapshot(), previous)
  assert.equal(h.jobs.size, 0)
})

test('较早 GET 和低版本响应不会覆盖操作后的 ready 状态', async () => {
  const h = harness(); await h.start()
  const pending = deferred()
  h.fake.fetchRagStatus = async () => pending.promise
  const reading = h.controller.refresh()
  await h.controller.execute('operation-a', 'resume')
  pending.resolve(list(vector('pending')))
  await reading
  assert.equal(h.controller.getSnapshot().status.documents[0].vector_index.status, 'ready')
  h.fake.fetchIndexOperation = async () => receipt(vector('pending'))
  await h.controller.query('operation-a')
  assert.equal(h.controller.getSnapshot().status.documents[0].vector_index.status, 'ready')
})

test('轮询请求未结束前不创建下一轮定时器，避免重叠请求', async () => {
  const h = harness(); await h.start()
  const pending = deferred()
  h.fake.fetchIndexOperation = async () => pending.promise
  await h.tick()
  assert.equal(h.jobs.size, 0)
  pending.resolve(receipt(vector('pending', { state_version: 2 })))
  await flush()
  assert.equal(h.jobs.size, 1)
})

test('keyword 上传和旧文档没有执行按钮或无意义轮询', async () => {
  const h = harness(vector('not_requested')); await h.start()
  await h.controller.upload({ session_id: scope.session_id, document_text: 'text' })
  assert.equal(h.jobs.size, 0)
  assert.equal(indexPresentation(h.controller.getSnapshot().upload.vector_index).label, '未启用向量索引')
  assert.equal(indexPresentation(undefined).action, null)
  assert.equal(h.controller.getSnapshot().status.chunk_count, 2)
})

test('轮询短暂失败后恢复，清除过期查询错误并保留终态', async () => {
  const h = harness(); await h.start()
  h.fake.fetchIndexOperation = async () => { throw new Error('offline') }
  await h.tick()
  assert.ok(h.controller.getSnapshot().queryError)
  h.fake.fetchIndexOperation = async () => receipt(vector('ready', { state_version: 5 }))
  await h.tick()
  assert.equal(h.controller.getSnapshot().queryError, '')
  assert.equal(h.controller.getSnapshot().status.documents[0].vector_index.status, 'ready')
  assert.equal(h.jobs.size, 0)
})
