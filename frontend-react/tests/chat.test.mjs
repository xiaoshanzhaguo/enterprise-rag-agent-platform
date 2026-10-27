import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test, afterEach } from 'node:test'
import ts from 'typescript'

// 使用项目现有 TypeScript 编译器加载纯客户端模块，不增加测试框架依赖。
async function load(relative, replace = text => text) {
  const source = replace(await readFile(new URL(relative, import.meta.url), 'utf8'))
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}
const { streamAgentAnswer } = await load('../src/api/chatApi.ts', source => source.replace(
  "import { API_BASE_URL, ApiError } from './httpClient'",
  "const API_BASE_URL = '/api'; class ApiError extends Error { constructor(message, status, payload) { super(message); this.status = status; this.payload = payload } }"))
const { restoreMessages } = await load('../src/api/sessionMessages.ts')
const originalFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = originalFetch })
const request = { session_id: 'a', input_text: '年假？', task_type: 'agent' }
function mockStream(text, fragment = 1) {
  const bytes = new TextEncoder().encode(text)
  globalThis.fetch = async () => new Response(new ReadableStream({ start(controller) {
    for (let index = 0; index < bytes.length; index += fragment) controller.enqueue(bytes.slice(index, index + fragment))
    controller.close()
  } }), { headers: { 'content-type': 'text/event-stream' } })
}
test('中文 UTF-8 和 CRLF 分隔跨字节分块，final 尾帧无空行仍可恢复', async () => {
  mockStream('data: {"event_type":"delta","session_id":"a","content":"年假"}\r\n\r\ndata: {"event_type":"final","session_id":"a","content":"提前申请"}')
  const events = []
  await streamAgentAnswer(request, event => events.push(event))
  assert.deepEqual(events.map(event => event.content), ['年假', '提前申请'])
})
test('跨会话事件在进入 UI 回调之前拒绝', async () => {
  mockStream('data: {"event_type":"final","session_id":"b","content":"其他会话"}\n\n')
  const events = []
  await assert.rejects(streamAgentAnswer(request, event => events.push(event)), /会话编号不匹配/)
  assert.equal(events.length, 0)
})
test('断流不误报成功，也不会自动重发', async () => {
  mockStream('data: {"event_type":"delta","content":"部分回答"}\n\n')
  await assert.rejects(streamAgentAnswer(request, () => {}), /连接已中断/)
})
test('已取消的旧流不会更新消息', async () => {
  mockStream('data: {"event_type":"final","content":"迟到回答"}\n\n')
  const controller = new AbortController(); controller.abort()
  let calls = 0
  await assert.rejects(streamAgentAnswer(request, () => { calls++ }, controller.signal), { name: 'AbortError' })
  assert.equal(calls, 0)
})
test('502 保留状态码，非 SSE 响应明确拒绝', async () => {
  globalThis.fetch = async () => new Response('{"detail":"上游不可用"}', { status: 502 })
  await assert.rejects(streamAgentAnswer(request, () => {}), error => error.status === 502 && error.payload.detail === '上游不可用')
  globalThis.fetch = async () => new Response('<html>代理页面</html>')
  await assert.rejects(streamAgentAnswer(request, () => {}), /有效流式响应/)
})
test('恢复历史正文及每条引用，过滤系统消息和步骤 JSON 外壳', () => {
  const result = restoreMessages({ session_id: 'a', messages: [
    { role: 'system', content: '内部提示' },
    { role: 'user', content: '年假？' },
    { role: 'assistant', content: '旧展示', raw_content: '{"generate_answer":"提前申请"}', rag_preview_chunks: [{ source: 'leave.md#chunk-1' }] },
  ] })
  assert.equal(result.length, 2)
  assert.equal(result[1].content, '提前申请')
  assert.equal(result[1].metadata.rag_preview_chunks[0].source, 'leave.md#chunk-1')
})
test('历史反馈绑定数据库消息编号，不用前端列表下标代替', () => {
  const feedback = { rating: 'unhelpful', reason: 'incomplete', comment: '缺少时限', updated_at: '2026-10-27' }
  const restored = restoreMessages({ session_id: 'a', messages: [
    { message_id: 52, role: 'assistant', content: '回答', feedback },
  ] })
  assert.equal(restored[0].messageId, 52)
  assert.deepEqual(restored[0].feedback, feedback)
})
