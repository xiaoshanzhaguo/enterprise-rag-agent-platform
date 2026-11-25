import { Plus } from 'lucide-react'
import { useRef, useState } from 'react'
import { formatApiError } from '../api/httpClient'
import { createDocumentCategory, deleteDocumentCategory, updateDocumentCategory } from '../api/knowledgeBaseApi'
import type { KnowledgeBaseCategory } from '../types/knowledgeBase'
import { SessionActionDialog } from './SessionActionDialog'

type CategoryAction = { kind: 'create' } | { kind: 'edit' | 'delete'; category: KnowledgeBaseCategory }

export function DocumentCategoryManager({ categories, loading, onSaved, onDeleted }: {
  categories: KnowledgeBaseCategory[]
  loading: boolean
  onSaved: (category: KnowledgeBaseCategory) => void
  onDeleted: (id: string) => void
}) {
  const [action, setAction] = useState<CategoryAction | null>(null)
  const [label, setLabel] = useState('')
  const [description, setDescription] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const trigger = useRef<HTMLButtonElement>(null)
  const saving = useRef(false)

  function open(button: HTMLButtonElement, next: CategoryAction) {
    trigger.current = button
    setLabel(next.kind === 'create' ? '' : next.category.label)
    setDescription(next.kind === 'create' ? '' : next.category.description ?? '')
    setError('')
    setAction(next)
  }

  async function submit() {
    if (!action || saving.current || (action.kind !== 'delete' && !label.trim())) return
    saving.current = true
    setBusy(true)
    setError('')
    try {
      if (action.kind === 'delete') {
        await deleteDocumentCategory(action.category.category_id)
        onDeleted(action.category.category_id)
      } else {
        const category = action.kind === 'create'
          ? await createDocumentCategory(label.trim(), description.trim())
          : await updateDocumentCategory(action.category.category_id, label.trim(), description.trim())
        onSaved(category)
      }
      setAction(null)
    } catch (error) {
      setError(formatApiError(error, '操作未完成，请核对分类列表后重试。'))
    } finally {
      saving.current = false
      setBusy(false)
    }
  }

  return <>
    <div className="kb-toolbar">
      <div className="workspace-panel-actions">
        <button type="button" className="primary-button" disabled={loading}
          onClick={event => open(event.currentTarget, { kind: 'create' })}><Plus size={17} aria-hidden="true" />新建分类</button>
      </div>
    </div>
    {categories.length ? <div className="admin-table-scroll kb-table-scroll kb-registry-table" tabIndex={0} role="region" aria-label="文档分类列表">
      <table className="admin-table kb-table">
        <caption className="sr-only">文档分类</caption>
        <thead><tr><th scope="col">分类名称</th><th scope="col">说明</th><th scope="col">操作</th></tr></thead>
        <tbody>{categories.map(category => <tr key={category.category_id}>
          <th scope="row">{category.label}</th>
          <td>{category.description || '—'}</td>
          <td><div className="kb-row-actions">
            <button type="button" className="text-button" onClick={event => open(event.currentTarget, { kind: 'edit', category })}>编辑</button>
            {category.category_id !== 'general' && <button type="button" className="text-button kb-delete-action"
              onClick={event => open(event.currentTarget, { kind: 'delete', category })}>删除</button>}
          </div></td>
        </tr>)}</tbody>
      </table>
    </div> : <p className="kb-empty" role="status">{loading ? '正在加载分类…' : '暂无可用分类'}</p>}
    {action && <SessionActionDialog title={action.kind === 'create' ? '新建分类' : action.kind === 'edit' ? '编辑分类' : '删除分类'}
      busy={busy} returnFocusTo={trigger} onClose={() => setAction(null)}>
      <form onSubmit={event => { event.preventDefault(); void submit() }}>
        {error && <p className="alert error" role="alert">{error}</p>}
        {action.kind === 'delete' ? <p>确定删除「{action.category.label}」？文档或历史版本使用的分类无法删除。</p> : <>
          <label className="field"><span>分类名称</span><input required maxLength={48} value={label} disabled={busy}
            onChange={event => setLabel(event.target.value)} /></label>
          <label className="field"><span>说明（选填）</span><textarea rows={3} maxLength={200} value={description} disabled={busy}
            onChange={event => setDescription(event.target.value)} /></label>
        </>}
        <div className="session-dialog-actions">
          <button type="button" className="ghost-button" disabled={busy} onClick={() => setAction(null)}>取消</button>
          <button type="submit" className={action.kind === 'delete' ? 'primary-button danger-solid-button' : 'primary-button'}
            disabled={busy || (action.kind !== 'delete' && !label.trim())}>
            {busy ? '处理中…' : action.kind === 'delete' ? '确认删除' : '保存'}
          </button>
        </div>
      </form>
    </SessionActionDialog>}
  </>
}
