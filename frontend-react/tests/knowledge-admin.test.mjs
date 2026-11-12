import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

async function moduleUrl(path, rewrite = source => source) {
  const source = rewrite(await readFile(new URL(path, import.meta.url), 'utf8'))
  const { outputText } = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
  } })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}
const versions = await moduleUrl('../src/api/documentVersionState.ts')
const { knowledgeAdminView, documentFileError, MAX_DOCUMENT_BYTES } = await import(
  await moduleUrl('../src/api/knowledgeAdminView.ts', source => source.replace("'./documentVersionState'", JSON.stringify(versions))))

const document = extra => ({ document_id: 1, logical_document_id: 'one', version_number: 1,
  file_name: 'policy.md', display_name: '差旅制度', lifecycle_state: 'active', logical_enabled: 1, ...extra })

test('文档统计按身份归并，全部版本与当前生效分开计算', () => {
  const documents = [document({ is_current_version: true }), document({ document_id: 2, version_number: 2 }),
    document({ document_id: 3, logical_document_id: 'two', is_current_version: true })]
  const view = knowledgeAdminView(documents, '')
  assert.equal(view.groups.length, 2)
  assert.equal(view.versionCount, 3)
  assert.equal(view.currentCount, 2)
  assert.deepEqual(documents.map(d => d.document_id), [1, 2, 3])
})

test('停用或删除中版本不计为当前生效', () => {
  const view = knowledgeAdminView([document({ is_current_version: true, logical_enabled: 0 }),
    document({ document_id: 2, logical_document_id: 'two', is_current_version: true, lifecycle_state: 'deleting' })], '')
  assert.equal(view.currentCount, 0)
  assert.equal(view.versionCount, 2)
})

test('搜索名称或历史文件名，保留整组版本和全局统计', () => {
  const documents = [document({ is_current_version: true }), document({ document_id: 2, version_number: 2, file_name: 'TRAVEL-v2.MD' }),
    document({ document_id: 3, logical_document_id: 'two', display_name: '请假制度', file_name: 'leave.md' })]
  const view = knowledgeAdminView(documents, '  travel-V2  ')
  assert.equal(view.filteredGroups.length, 1)
  assert.equal(view.filteredGroups[0].versions.length, 2)
  assert.equal(view.groups.length, 2)
  assert.equal(view.versionCount, 3)
  assert.equal(knowledgeAdminView(documents, '差旅').filteredGroups.length, 1)
})

test('无匹配搜索与知识库为空是不同结果', () => {
  const view = knowledgeAdminView([document({})], 'missing')
  assert.equal(view.groups.length, 1)
  assert.equal(view.filteredGroups.length, 0)
  assert.equal(knowledgeAdminView([], '').groups.length, 0)
})

test('文件格式按扩展名校验，仅支持当前后端流程可读取的文本类型', () => {
  assert.equal(documentFileError({ name: 'policy.MD', size: 10 }), '')
  assert.equal(documentFileError({ name: 'policy.txt', size: 10 }), '')
  assert.match(documentFileError({ name: 'policy.pdf', size: 10 }), /Markdown/)
  assert.match(documentFileError({ name: 'policy', size: 10 }), /Markdown/)
})

test('空文件及超限文件在提交前被拒绝，2MB边界可以上传', () => {
  assert.match(documentFileError({ name: 'policy.md', size: 0 }), /为空/)
  assert.equal(documentFileError({ name: 'policy.md', size: MAX_DOCUMENT_BYTES }), '')
  assert.match(documentFileError({ name: 'policy.md', size: MAX_DOCUMENT_BYTES + 1 }), /超过/)
})
