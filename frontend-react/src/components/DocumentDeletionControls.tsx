import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { SessionActionDialog } from './SessionActionDialog'
import { DocumentDeletionController } from '../api/documentDeletionState'
import * as deletionApi from '../api/documentDeletionApi'
import { fetchRagStatus } from '../api/knowledgeBaseApi'
import type { RagStatusDocument } from '../types/knowledgeBase'

const api = { ...deletionApi, fetchRagStatus }

export function DocumentDeletionControls({ document, knowledgeBaseId, onChanged }: {
  document: RagStatusDocument; knowledgeBaseId: string; onChanged: () => void
}) {
  const [confirm, setConfirm] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  const controller = useMemo(() => new DocumentDeletionController(document.document_id || 0,
    document.lifecycle_version || 1, { session_id: 'react-admin-knowledge-upload', knowledge_base_id: knowledgeBaseId, user_role: 'kb_admin' },
    api, onChanged), [document.document_id, document.lifecycle_version, knowledgeBaseId, onChanged])
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot)
  useEffect(() => { controller.start(); return () => controller.dispose() }, [controller])
  useEffect(() => { controller.receive(document.deletion) }, [controller, document.deletion])
  const task = state.receipt
  const deleting = document.lifecycle_state === 'deleting' || Boolean(task)
  const blocked = document.vector_index?.execution_claimed || !document.document_id
  return <div className="document-deletion-controls">
    {deleting || state.uncertain ? <>
      <span className={`status-badge ${task?.status === 'failed' ? 'status-failed' : 'status-running'}`}>
        {state.uncertain ? '删除结果未确认' : task?.status === 'completed' ? '已删除' : task?.status === 'failed' ? '删除失败' : '删除中'}
      </span>
      <button type="button" className="ghost-button" disabled={state.busy} onClick={() => { void controller.query() }}>查询删除状态</button>
      {!state.uncertain && task?.retryable ? <button type="button" className="ghost-button" disabled={state.busy}
        onClick={() => { void controller.retry() }}>{task.status === 'pending' ? '继续删除' : '重试删除'}</button> : null}
    </> : <button ref={trigger} type="button" className="ghost-button" disabled={blocked || state.busy}
      onClick={() => setConfirm(true)}>{blocked ? '索引中，暂不可删除' : '删除文档'}</button>}
    {state.error ? <p className="index-status-detail" role="alert">{state.error}</p> : null}
    {deleting ? <p className="index-status-detail">文档已退出检索范围，完成后不可撤销。</p> : null}
    {confirm ? <SessionActionDialog title="确认删除文档" busy={state.busy} returnFocusTo={trigger} onClose={() => setConfirm(false)}>
      <p>确定删除“{document.file_name || '未命名文档'}”？确认后立即停止检索，删除完成后不可撤销。</p>
      <div className="session-dialog-actions">
        <button type="button" className="ghost-button" disabled={state.busy} onClick={() => setConfirm(false)}>取消</button>
        <button type="button" className="primary-button" disabled={state.busy}
          onClick={async () => { await controller.remove(); setConfirm(false) }}>{state.busy ? '正在提交…' : '确认删除'}</button>
      </div>
    </SessionActionDialog> : null}
  </div>
}
