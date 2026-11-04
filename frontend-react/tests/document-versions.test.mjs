import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { afterEach, test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ts from 'typescript'

async function moduleUrl(path, rewrite = x => x) {
  const source = rewrite(await readFile(new URL(path, import.meta.url), 'utf8'))
  const { outputText } = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } })
  const code = outputText.replaceAll('"react/jsx-runtime"', JSON.stringify(new URL('../node_modules/react/jsx-runtime.js', import.meta.url).href))
    .replaceAll("'react'", JSON.stringify(new URL('../node_modules/react/index.js', import.meta.url).href))
  return `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`
}
const http = await moduleUrl('../src/api/httpClient.ts', s => s.replace('import.meta.env.VITE_API_BASE_URL?.trim()', 'undefined'))
const state = await moduleUrl('../src/api/documentVersionState.ts')
const api = await moduleUrl('../src/api/documentVersions.ts', s => s.replace("'./httpClient'", JSON.stringify(http)))
const { changeDocumentVersion } = await import(api)
const { groupDocumentVersions, versionPresentation, versionUploadMetadata, versionConfirmation } = await import(state)
const dialogStub = `data:text/javascript,export function SessionActionDialog(){return null}`
const { DocumentVersionControls } = await import(await moduleUrl('../src/components/DocumentVersionControls.tsx', s => s
  .replace("'../api/documentVersions'", JSON.stringify(api)).replace("'../api/documentVersionState'", JSON.stringify(state))
  .replace("'../api/httpClient'", JSON.stringify(http)).replace("'./SessionActionDialog'", JSON.stringify(dialogStub))))
const { KnowledgeIndexController } = await import(await moduleUrl('../src/api/knowledgeIndexState.ts',
  source => source.replace("'./httpClient'", JSON.stringify(http))))
const originalFetch = globalThis.fetch
const controllers = new Set()
afterEach(() => { globalThis.fetch = originalFetch; controllers.forEach(c => c.dispose()); controllers.clear() })
const doc = (extra = {}) => ({ logical_document_id: 'identity', document_id: 12, version_number: 2, logical_state_version: 7,
  lifecycle_state: 'active', logical_enabled: 1, vector_index: { status: 'ready', execution_claimed: false }, ...extra })

test('同名不同逻辑文档不合并，版本按递减顺序分组', () => {
  const groups = groupDocumentVersions([doc({ version_number: 1, file_name: 'same.md' }), doc(),
    doc({ logical_document_id: 'another', file_name: 'same.md' })])
  assert.equal(groups.length, 2)
  assert.deepEqual(groups[0].versions.map(d => d.version_number), [2, 1])
})
test('选择已有文档时保留部门和流程快照，显式改分类才传递新部门', () => {
  const documents = [doc({ knowledge_base_type: 'finance', department: '财务业务组', is_current_version: true })]
  assert.deepEqual(versionUploadMetadata(documents, 'identity', { knowledge_base_type: 'finance', department: '财务' }),
    { knowledge_base_type: 'finance', department: undefined, process_status: undefined })
  assert.equal(versionUploadMetadata(documents, 'identity', { knowledge_base_type: 'hr', department: 'HR' }).department, 'HR')
})
test('确认弹窗固定打开时的 CAS，列表更新不得悄悄采用较新版本提交', () => {
  const document = doc()
  const confirmation = versionConfirmation(document, 'publish')
  document.logical_state_version = 8
  assert.equal(confirmation.stateVersion, 7)
  assert.equal(confirmation.documentId, 12)
})
test('候选、当前、历史、停用显示及当前删除保护', () => {
  assert.equal(versionPresentation(doc()).label, '候选版本')
  assert.equal(versionPresentation(doc({ is_current_version: true })).deletable, false)
  assert.equal(versionPresentation(doc({ published_at: '2026-11-04' })).label, '历史版本')
  assert.equal(versionPresentation(doc({ logical_enabled: 0 })).label, '已停用')
})
test('pending、failed、deleting 不允许发布；ready 和 keyword 可发布', () => {
  for (const status of ['pending', 'failed']) assert.equal(versionPresentation(doc({ vector_index: { status } })).publishable, false)
  assert.equal(versionPresentation(doc({ lifecycle_state: 'deleting' })).publishable, false)
  assert.equal(versionPresentation(doc()).publishable, true)
  assert.equal(versionPresentation(doc({ vector_index: { status: 'not_requested' } })).publishable, true)
})
test('真实 React 组件展示当前停用、候选发布和历史回退入口', () => {
  const render = document => renderToStaticMarkup(createElement(DocumentVersionControls, { document, knowledgeBaseId: 'kb', onChanged() {} }))
  assert.match(render(doc()), /发布此版本/)
  assert.match(render(doc({ is_current_version: true })), /停用逻辑文档/)
  assert.match(render(doc({ published_at: '2026-11-04' })), /回退至此版本/)
  assert.match(render(doc({ vector_index: { status: 'pending' } })), /disabled/)
})
test('发布请求携带范围、角色、Document 和逻辑 CAS 版本，不暴露 token', async () => {
  let request
  globalThis.fetch = async (url, init) => { request = { url, init }; return new Response('{}', { status: 200 }) }
  await changeDocumentVersion('id', 'finance/a', 7, 12)
  assert.match(request.url, /logical_documents\/id\/publish\?knowledge_base_id=finance%2Fa&user_role=kb_admin/)
  assert.deepEqual(JSON.parse(request.init.body), { expected_state_version: 7, document_id: 12 })
})
test('停用请求不携带 Document；409 不自动重发', async () => {
  let count = 0
  globalThis.fetch = async (url, init) => {
    count++; assert.match(url, /\/disable\?/)
    assert.deepEqual(JSON.parse(init.body), { expected_state_version: 7 })
    return new Response(JSON.stringify({ detail: '版本已变化' }), { status: 409 })
  }
  await assert.rejects(changeDocumentVersion('id', 'kb', 7), e => e.status === 409)
  assert.equal(count, 1)
})

function versionHarness(status = 'failed') {
  const vector = (status, overrides = {}) => ({ operation_id: 'op2', status, state_version: 4, execution_claimed: false,
    resumable: status === 'pending', retryable: status === 'failed', ...overrides })
  let server = { documents: [doc({ document_id: 11, version_number: 1, is_current_version: true, logical_state_version: 2,
    published_at: 'yesterday', vector_index: { ...vector('ready'), operation_id: 'op1' } }),
  doc({ is_current_version: false, logical_state_version: 2, vector_index: vector(status) })] }
  let calls = 0
  const jobs = new Map()
  const scheduler = { set(fn) { const key = Symbol(); jobs.set(key, fn); return key }, clear(key) { jobs.delete(key) } }
  const receipt = () => ({ operation_id: 'op2', document_id: 12, state_confirmed: true, vector_index: structuredClone(server.documents[1].vector_index) })
  const fake = {
    async fetchRagStatus() { calls++; return structuredClone(server) },
    async fetchIndexOperation() { return receipt() },
    async retryIndexOperation() { publish(); return receipt() },
    async resumeIndexOperation() { publish(); return receipt() },
  }
  function publish(applied = true) {
    server.documents[0] = { ...server.documents[0], is_current_version: !applied, logical_state_version: 3 }
    server.documents[1] = { ...server.documents[1], is_current_version: applied, logical_state_version: 3,
      published_at: applied ? 'today' : undefined, vector_index: vector('ready', { state_version: 7 }) }
  }
  const controller = new KnowledgeIndexController({ session_id: 'admin', knowledge_base_id: 'kb' }, fake, scheduler)
  controllers.add(controller)
  return { controller, fake, jobs, publish, snapshot: () => structuredClone(server), lists: () => calls,
    async start() { controller.start(); await controller.refresh() } }
}

test('V2 重试成功后刷新整个逻辑文档，V1/V2 的当前指针和 CAS 一起更新', async () => {
  const h = versionHarness(); await h.start()
  await h.controller.execute('op2', 'retry')
  const versions = h.controller.getSnapshot().status.documents
  assert.deepEqual(versions.map(d => d.is_current_version), [false, true])
  assert.deepEqual(versions.map(d => d.logical_state_version), [3, 3])
  assert.equal(versions[1].published_at, 'today')
  assert.equal(h.lists(), 2)
})

test('pending 轮询到 ready 后刷新发布结果，然后停止轮询', async () => {
  const h = versionHarness('pending'); await h.start()
  h.publish()
  const job = h.jobs.values().next().value
  h.jobs.clear(); job()
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(h.controller.getSnapshot().status.documents[1].is_current_version, true)
  assert.equal(h.jobs.size, 0)
})

test('自动发布冲突时 ready 仍是候选，不在前端自行推断为当前版本', async () => {
  const h = versionHarness('pending'); await h.start()
  h.publish(false)
  await h.controller.query('op2')
  assert.deepEqual(h.controller.getSnapshot().status.documents.map(d => d.is_current_version), [true, false])
})

test('POST 成功后任务 GET 失败，仍然查询列表恢复自动发布状态', async () => {
  const h = versionHarness(); await h.start()
  h.fake.fetchIndexOperation = async () => { throw new Error('temporary query failure') }
  await h.controller.execute('op2', 'retry')
  assert.equal(h.controller.getSnapshot().status.documents[1].is_current_version, true)
})

test('ready 后的刷新使旧列表响应失效，旧指针不能覆盖新发布结果', async () => {
  const h = versionHarness('pending'); await h.start()
  const stale = h.snapshot(), fetchCurrent = h.fake.fetchRagStatus
  let release
  h.fake.fetchRagStatus = async () => {
    h.fake.fetchRagStatus = fetchCurrent
    return new Promise(resolve => { release = resolve })
  }
  const oldRead = h.controller.refresh()
  h.publish()
  const query = h.controller.query('op2')
  await new Promise(resolve => setImmediate(resolve))
  release(stale)
  await Promise.all([oldRead, query])
  assert.equal(h.controller.getSnapshot().status.documents[1].is_current_version, true)
})
