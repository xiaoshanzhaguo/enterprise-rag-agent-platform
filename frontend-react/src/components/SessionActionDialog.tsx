import { useEffect, useId, useRef } from 'react'
import type { ReactNode, RefObject } from 'react'
import { X } from 'lucide-react'

type Props = {
  title: string
  busy: boolean
  onClose: () => void
  returnFocusTo: RefObject<HTMLButtonElement | null>
  children: ReactNode
}

export function SessionActionDialog({ title, busy, onClose, returnFocusTo, children }: Props) {
  const dialog = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  useEffect(() => {
    const element = dialog.current
    const trigger = returnFocusTo.current
    element?.showModal()
    const input = element?.querySelector<HTMLInputElement>('input')
    input?.focus()
    input?.select()
    return () => {
      element?.close()
      if (trigger?.isConnected) trigger.focus()
    }
  }, [returnFocusTo])

  return <dialog ref={dialog} className="session-action-dialog" aria-labelledby={titleId} aria-modal="true"
    onCancel={event => { event.preventDefault(); if (!busy) onClose() }}
    onClick={event => {
      if (event.target !== event.currentTarget || busy) return
      const rect = event.currentTarget.getBoundingClientRect()
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) onClose()
    }}>
    <div className="session-dialog-heading"><h3 id={titleId}>{title}</h3><button type="button" className="session-dialog-close" aria-label="关闭弹窗" disabled={busy} onClick={onClose}><X size={20} aria-hidden="true" /></button></div>
    <div className="session-dialog-body">{children}</div>
  </dialog>
}
