import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { afterEach, test } from 'node:test'
import ts from 'typescript'

async function moduleUrl(path, rewrite = s => s) {
  const { outputText } = ts.transpileModule(rewrite(await readFile(new URL(path, import.meta.url), 'utf8')),
    { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}
const http = await moduleUrl('../src/api/httpClient.ts', s => s.replace('import.meta.env.VITE_API_BASE_URL?.trim()', 'undefined'))
const { ApiError } = await import(http)
const { DocumentDeletionController } = await import(await moduleUrl('../src/api/documentDeletionState.ts', s => s.replace("'./httpClient'", JSON.stringify(http))))
const { KnowledgeIndexController } = await import(await moduleUrl('../src/api/knowledgeIndexState.ts', s => s.replace("'./httpClient'", JSON.stringify(http))))
const api = await import(await moduleUrl('../src/api/documentDeletionApi.ts', s => s.replace("'./httpClient'", JSON.stringify(http))))
const originalFetch = globalThis.fetch
const controllers = []
afterEach(() => { globalThis.fetch = originalFetch; controllers.forEach(c => c.dispose()); controllers.length = 0 })
const scope = { session_id: 'upload', knowledge_base_id: 'kb/a', user_role: 'kb_admin' }
const receipt = (status = 'completed', changes = {}) => ({ operation_id: 'op', document_id: 1, knowledge_base_id: scope.knowledge_base_id,
  status, state_version: 3, execution_claimed: false, retryable: status === 'failed', state_confirmed: true, error_message: null, ...changes })
function harness() {
  const calls = [], jobs = new Map()
  let seq = 0
  const fake = {
    async deleteDocument(id, version) { calls.push(['delete', id, version]); return receipt() },
    async fetchDocumentDeletion(id) { calls.push(['get', id]); return receipt() },
    async retryDocumentDeletion(id, version) { calls.push(['retry', id, version]); return receipt() },
    async fetchRagStatus() { calls.push(['list']); return { documents: [] } },
  }
  const controller = new DocumentDeletionController(1, 7, scope, fake, () => calls.push(['changed']), {
    set(fn) { jobs.set(++seq, fn); return seq }, clear(id) { jobs.delete(id) },
  })
  controllers.push(controller); controller.start()
  return { controller, fake, calls, jobs }
}
const flush = () => new Promise(resolve => setImmediate(resolve))

test('实际 DELETE、查询、retry 请求携带范围与版本，兼容 HTTP 202', async () => {
  const requests = []
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), ...options })
    return new Response(JSON.stringify(receipt('pending', { execution_claimed: true })), { status: 202, headers: { 'Content-Type': 'application/json' } })
  }
  assert.equal((await api.deleteDocument(1, 7, scope)).status, 'pending')
  await api.fetchDocumentDeletion('op/a', scope)
  await api.retryDocumentDeletion('op/a', 3, scope)
  assert.equal(requests[0].method, 'DELETE')
  assert.deepEqual(JSON.parse(requests[0].body), { expected_document_version: 7 })
  assert.match(requests[0].url, /knowledge_base_id=kb%2Fa/)
  assert.match(requests[1].url, /op%2Fa/)
  assert.deepEqual(JSON.parse(requests[2].body), { expected_state_version: 3 })
})

test('确认删除执行一次，重复点击被阻止，成功后通知刷新', async () => {
  const { controller, fake, calls } = harness()
  let resolve
  fake.deleteDocument = async () => { calls.push(['delete']); return new Promise(done => { resolve = done }) }
  const pending = controller.remove()
  await controller.remove()
  assert.equal(calls.filter(c => c[0] === 'delete').length, 1)
  resolve(receipt()); await pending
  assert.equal(controller.getSnapshot().receipt.status, 'completed')
  assert.ok(calls.some(c => c[0] === 'changed'))
  await controller.remove()
  assert.equal(calls.filter(c => c[0] === 'delete').length, 1)
})

test('刷新恢复 claimed pending 仅轮询 GET，不自动 DELETE 或 retry', async () => {
  const { controller, calls, jobs } = harness()
  controller.receive(receipt('pending', { execution_claimed: true }))
  assert.equal(jobs.size, 1)
  const [id, fn] = jobs.entries().next().value; jobs.delete(id); fn(); await flush()
  assert.ok(calls.some(c => c[0] === 'get'))
  assert.equal(calls.some(c => c[0] === 'delete' || c[0] === 'retry'), false)
  assert.equal(controller.getSnapshot().receipt.status, 'completed')
  assert.equal(jobs.size, 0)
})

test('失败回执保留原因，只有显式 retry 才提交最新版本', async () => {
  const { controller, fake, calls } = harness()
  fake.deleteDocument = async () => { throw new ApiError('502', 502, receipt('failed', { error_message: '向量删除失败' })) }
  await controller.remove()
  assert.equal(controller.getSnapshot().error, '向量删除失败')
  assert.equal(calls.some(c => c[0] === 'retry'), false)
  await controller.retry()
  assert.deepEqual(calls.find(c => c[0] === 'retry'), ['retry', 'op', 3])
  assert.equal(controller.getSnapshot().receipt.status, 'completed')
})

test('网络超时仅查询列表对账，禁止重复删除', async () => {
  const { controller, fake, calls } = harness()
  fake.deleteDocument = async () => { calls.push(['delete']); throw new Error('timeout') }
  await controller.remove(); await flush()
  assert.equal(controller.getSnapshot().uncertain, true)
  await controller.remove()
  assert.equal(calls.filter(c => c[0] === 'delete').length, 1)
  assert.ok(calls.some(c => c[0] === 'list'))
})

test('409 明确提示并刷新，不自动重新提交', async () => {
  const { controller, fake, calls } = harness()
  fake.deleteDocument = async () => { calls.push(['delete']); throw new ApiError('409', 409, { detail: '文档正在索引' }) }
  await controller.remove()
  assert.equal(controller.getSnapshot().error, '文档正在索引')
  assert.ok(calls.some(c => c[0] === 'changed'))
  assert.equal(calls.filter(c => c[0] === 'delete').length, 1)
})

test('卸载取消轮询，迟到响应不能覆盖状态', async () => {
  const { controller, fake, jobs } = harness()
  let resolve
  fake.fetchDocumentDeletion = async () => new Promise(done => { resolve = done })
  controller.receive(receipt('pending'))
  const pending = controller.query()
  controller.dispose(); resolve(receipt()); await pending
  assert.equal(jobs.size, 0)
  assert.equal(controller.getSnapshot().receipt.status, 'pending')
})

test('低版本 GET 不覆盖已完成删除', async () => {
  const { controller } = harness()
  controller.receive(receipt('completed', { state_version: 5 }))
  controller.receive(receipt('pending', { state_version: 2 }))
  assert.equal(controller.getSnapshot().receipt.status, 'completed')
})

test('未领取 pending 可以显式继续，已领取 pending 不可执行', async () => {
  const { controller, calls } = harness()
  controller.receive(receipt('pending', { retryable: true, execution_claimed: true }))
  await controller.retry()
  assert.equal(calls.some(c => c[0] === 'retry'), false)
  controller.receive(receipt('pending', { retryable: true, execution_claimed: false }))
  await controller.retry()
  assert.equal(calls.filter(c => c[0] === 'retry').length, 1)
})

for (const retry of [false, true]) {
  test(`${retry ? '重试' : '删除'}完成使旧列表 GET 失效，并在旧请求结束后获取最终列表`, async () => {
    const initial = { documents: [{ document_id: 1, lifecycle_state: retry ? 'deleting' : 'active',
      lifecycle_version: retry ? 2 : 1, ...(retry ? { deletion: receipt('failed') } : {}) }], document_count: 1 }
    let server = structuredClone(initial), reads = 0, release, writes = 0
    const parent = new KnowledgeIndexController(scope, {
      async fetchRagStatus() {
        if (++reads === 2) return new Promise(resolve => { release = resolve })
        return structuredClone(server)
      },
    })
    controllers.push(parent); parent.start(); await parent.refresh()
    const oldRead = parent.refresh()
    const complete = async () => { writes++; server = { documents: [], document_count: 0 }; return receipt('completed', { state_version: 7 }) }
    const child = new DocumentDeletionController(1, 1, scope, { deleteDocument: complete, retryDocumentDeletion: complete }, parent.refreshAfterDocumentChange)
    controllers.push(child); child.start()
    if (retry) child.receive(receipt('failed'))
    await (retry ? child.retry() : child.remove())
    assert.equal(child.getSnapshot().receipt.status, 'completed')
    release(structuredClone(initial)); await oldRead; await flush()
    assert.equal(parent.getSnapshot().status.documents.length, 0)
    assert.equal(reads, 3, '多个完成通知应合并为一次变更后的 GET')
    assert.equal(writes, 1, '刷新不得重复发送删除或 retry')
  })
}
