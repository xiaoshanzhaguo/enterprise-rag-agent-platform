import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/api/chatDraft.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } })
const { loadChatDraft, saveChatDraft, clearChatDraft, resolveDraftScope, buildScopedHistory, latestMessageId } =
  await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

function memoryStorage() {
  const rows = new Map()
  return { rows, getItem: key => rows.get(key) ?? null, setItem: (key, value) => rows.set(key, value), removeItem: key => rows.delete(key) }
}
const draft = { question: '未发送的报销问题\n补充说明', selectedKnowledgeBaseId: 'finance-base', selectedCategoryId: 'finance', historyAfterId: 12 }
const empty = { question: '', selectedKnowledgeBaseId: '', selectedCategoryId: 'all', historyAfterId: 0 }

test('按会话恢复草稿与范围，不串到其他会话，不修改存入对象', () => {
  const store = memoryStorage()
  assert.equal(saveChatDraft('a', draft, store), true)
  assert.deepEqual(loadChatDraft('a', store), draft)
  assert.deepEqual(loadChatDraft('b', store), empty)
  const restored = loadChatDraft('a', store)
  restored.question = '修改后的副本'
  assert.equal(loadChatDraft('a', store).question, draft.question)
})

test('不同浏览器标签页的存储相互隔离，支持同一标签页刷新恢复', () => {
  const tab = memoryStorage(); const otherTab = memoryStorage()
  saveChatDraft('same-id', draft, tab)
  assert.deepEqual(loadChatDraft('same-id', tab), draft)
  assert.deepEqual(loadChatDraft('same-id', otherTab), empty)
})

test('损坏JSON、错误版本及字段类型安全回退，不阻断页面', () => {
  for (const value of ['{broken', 'null', '[]', JSON.stringify({ ...draft, version: 2 }),
    JSON.stringify({ ...draft, version: 1, question: 123 }), JSON.stringify({ ...draft, version: 1, historyAfterId: -1 })]) {
    const store = memoryStorage()
    store.getItem = () => value
    assert.deepEqual(loadChatDraft(`broken-${value}`, store), empty)
  }
})

test('存储被禁用或配额不足时降级内存，恢复可用后可重新持久化', () => {
  const blocked = { getItem() { throw Error('blocked') }, setItem() { throw Error('quota') }, removeItem() { throw Error('blocked') } }
  assert.deepEqual(loadChatDraft('blocked', blocked), empty)
  assert.equal(saveChatDraft('blocked', draft, blocked), false)
  assert.deepEqual(loadChatDraft('blocked', blocked), draft)
  const store = memoryStorage()
  assert.equal(saveChatDraft('blocked', { ...draft, question: '新草稿' }, store), true)
  assert.equal(loadChatDraft('blocked', store).question, '新草稿')
  clearChatDraft('blocked', store)
})

test('发送或清空只移除输入内容，保留知识库、分类和上下文边界', () => {
  const store = memoryStorage()
  saveChatDraft('sent', draft, store)
  saveChatDraft('sent', { ...loadChatDraft('sent', store), question: '' }, store)
  assert.deepEqual(loadChatDraft('sent', store), { ...draft, question: '' })
})

test('只记住当前会话中明确跳过的问题，兼容旧草稿和无效编号', () => {
  const store = memoryStorage()
  saveChatDraft('a', { ...draft, skippedQuestionId: 14 }, store)
  saveChatDraft('b', draft, store)
  saveChatDraft('c', { ...draft, skippedQuestionId: -1 }, store)
  assert.equal(loadChatDraft('a', store).skippedQuestionId, 14)
  assert.deepEqual(loadChatDraft('b', store), draft)
  assert.deepEqual(loadChatDraft('c', store), draft)
})

test('删除会话后同时清除其持久化及降级草稿，不影响其他会话', () => {
  const store = memoryStorage()
  saveChatDraft('delete', draft, store); saveChatDraft('keep', draft, store)
  clearChatDraft('delete', store)
  assert.deepEqual(loadChatDraft('delete', store), empty)
  assert.deepEqual(loadChatDraft('keep', store), draft)
  const blocked = { setItem() { throw Error() }, getItem() { throw Error() }, removeItem() { throw Error() } }
  saveChatDraft('fallback-delete', draft, blocked); clearChatDraft('fallback-delete', blocked)
  assert.deepEqual(loadChatDraft('fallback-delete', blocked), empty)
})

test('有效范围恢复原选择，新会话优先使用最近回答的知识库', () => {
  const resolved = resolveDraftScope(draft, ['a', 'finance-base'], ['hr', 'finance'])
  assert.equal(resolved.selectedKnowledgeBaseId, 'finance-base')
  assert.equal(resolved.selectedCategoryId, 'finance')
  assert.equal(resolved.resetHistory, false)
  assert.equal(resolveDraftScope(empty, ['a', 'b'], [], 'b').selectedKnowledgeBaseId, 'b')
  assert.equal(resolveDraftScope(empty, ['a', 'b'], []).selectedKnowledgeBaseId, 'a')
  assert.equal(resolveDraftScope(empty, ['a', 'enterprise_default'], []).selectedKnowledgeBaseId, 'enterprise_default')
})

test('失效知识库不静默切到其他库，刷新后仍要求用户重新选择', () => {
  const resolved = resolveDraftScope(draft, ['other-base'], ['finance'])
  assert.equal(resolved.selectedKnowledgeBaseId, draft.selectedKnowledgeBaseId)
  assert.equal(resolved.resetHistory, true)
  assert.match(resolved.notice, /重新选择/)
  assert.match(resolveDraftScope({ ...draft, ...resolved }, ['other-base'], ['finance']).notice, /重新选择/)
  assert.match(resolveDraftScope(empty, ['other-base'], [], 'removed-base').notice, /重新选择/)
})

test('失效分类恢复自动判断并隔离旧上下文，空列表给出明确提示', () => {
  const resolved = resolveDraftScope(draft, ['finance-base'], ['hr'])
  assert.equal(resolved.selectedCategoryId, 'all')
  assert.equal(resolved.resetHistory, true)
  assert.match(resolved.notice, /已切换为自动判断/)
  assert.match(resolveDraftScope(empty, [], []).notice, /暂无可用知识库/)
})

const turn = (base, start, text = base) => [
  { role: 'user', content: `${text}问题`, status: 'complete', messageId: start },
  { role: 'assistant', content: `${text}回答`, status: 'complete', messageId: start + 1,
    metadata: { agent_route: { knowledge_base_id: base } } },
]

test('只复用当前知识库连续完整问答，其他库和未知范围均构成边界', () => {
  const messages = [...turn('a', 1), ...turn('b', 3), ...turn('a', 5, '新A')]
  assert.deepEqual(buildScopedHistory(messages, 'a', 0), [
    { role: 'user', content: '新A问题' }, { role: 'assistant', content: '新A回答' },
  ])
  assert.deepEqual(buildScopedHistory(messages, 'b', 0), [])
  const unknown = turn('a', 1); delete unknown[1].metadata
  assert.deepEqual(buildScopedHistory(unknown, 'a', 0), [])
})

test('切换知识库或分类后以持久化消息ID截断，刷新后也不复用旧范围', () => {
  const before = turn('a', 1)
  const boundary = latestMessageId(before)
  assert.deepEqual(buildScopedHistory(before, 'a', boundary), [])
  assert.equal(buildScopedHistory([...before, ...turn('a', 3)], 'a', boundary).length, 2)
})

test('已跳过问题随后收到迟到回答时，仍排除该轮作为提问上下文', () => {
  const delayedTurn = turn('a', 9)
  assert.deepEqual(buildScopedHistory(delayedTurn, 'a', 9), [])
  assert.equal(buildScopedHistory([...delayedTurn, ...turn('a', 11)], 'a', 9).length, 2)
})

test('本地已知范围可用于追问，不复用失败、未完成、无编号或孤立消息', () => {
  const messages = turn('a', 1)
  delete messages[1].metadata; messages[1].knowledgeBaseId = 'a'
  assert.equal(buildScopedHistory(messages, 'a', 0).length, 2)
  for (const status of ['streaming', 'error']) assert.deepEqual(buildScopedHistory([messages[0], { ...messages[1], status }], 'a', 0), [])
  assert.deepEqual(buildScopedHistory([messages[0], { ...messages[1], messageId: undefined }], 'a', 0), [])
  assert.deepEqual(buildScopedHistory([...messages, { role: 'user', content: '未完成的新问题', status: 'complete' }], 'a', 0), [])
})

test('最多发送6轮12条消息，不改变原消息及引用元数据', () => {
  const messages = Array.from({ length: 10 }, (_, index) => turn('a', index * 2 + 1)).flat()
  const before = JSON.stringify(messages)
  assert.equal(buildScopedHistory(messages, 'a', 0).length, 12)
  assert.equal(JSON.stringify(messages), before)
})
