import { AlertCircle, Inbox } from 'lucide-react'
import type { ReactNode } from 'react'
import type { ProcessStatus } from '../types/process'
import { statusLabels } from '../api/processDisplay'

export function StatusBadge({ status }: { status: ProcessStatus }) {
  return <span className={`status-badge status-${status}`}><i aria-hidden="true" />{statusLabels[status]}</span>
}

export function EmptyState({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return <div className="workspace-empty"><div className="empty-icon"><Inbox size={24} aria-hidden="true" /></div>
    <h3>{title}</h3><p>{children}</p>{action}</div>
}

export function ErrorNotice({ message, retry }: { message: string; retry?: () => void }) {
  return <div className="alert error error-notice" role="alert"><AlertCircle size={18} aria-hidden="true" />
    <span>{message}</span>{retry && <button type="button" className="text-button" onClick={retry}>重试</button>}</div>
}
