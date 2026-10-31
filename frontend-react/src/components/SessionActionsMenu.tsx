import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { MoreHorizontal, Pencil, Trash2 } from 'lucide-react'

type Props = {
  title: string
  disabled: boolean
  onAction: (action: 'rename' | 'delete', trigger: HTMLButtonElement | null) => void
}

export function SessionActionsMenu({ title, disabled, onAction }: Props) {
  const menuId = useId()
  const trigger = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState({ top: 0, left: 0 })

  function toggleMenu() {
    if (disabled) return
    if (open) { setOpen(false); return }
    const rect = trigger.current?.getBoundingClientRect()
    if (!rect) return
    const width = 176
    const height = 100
    setPosition({
      left: Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8)),
      top: rect.bottom + height + 6 <= window.innerHeight
        ? rect.bottom + 6 : Math.max(8, rect.top - height - 6),
    })
    setOpen(true)
  }

  useEffect(() => {
    if (!open) return
    menu.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus()
    function outside(event: PointerEvent) {
      if (event.target instanceof Node && !menu.current?.contains(event.target) && !trigger.current?.contains(event.target)) setOpen(false)
    }
    function dismiss() { setOpen(false) }
    document.addEventListener('pointerdown', outside)
    window.addEventListener('resize', dismiss)
    window.addEventListener('scroll', dismiss, true)
    return () => {
      document.removeEventListener('pointerdown', outside)
      window.removeEventListener('resize', dismiss)
      window.removeEventListener('scroll', dismiss, true)
    }
  }, [open])

  return <>
    <button ref={trigger} type="button" className="session-more-button" disabled={disabled}
      title="更多操作" aria-label={`${title}的更多操作`} aria-haspopup="menu" aria-expanded={open} aria-controls={open ? menuId : undefined}
      onClick={toggleMenu} onKeyDown={event => {
        if (event.key === 'ArrowDown' && !open) { event.preventDefault(); toggleMenu() }
      }}><MoreHorizontal size={18} aria-hidden="true" /></button>
    {open && createPortal(<div ref={menu} id={menuId} role="menu" aria-label={`${title}的会话操作`}
      className="session-action-menu" style={position} onKeyDown={event => {
        if (event.key === 'Escape' || event.key === 'Tab') {
          if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation() }
          trigger.current?.focus()
          setOpen(false)
          return
        }
        const items = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'))
        const index = items.indexOf(document.activeElement as HTMLButtonElement)
        const next = event.key === 'ArrowDown' ? (index + 1) % items.length
          : event.key === 'ArrowUp' ? (index - 1 + items.length) % items.length
            : event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : -1
        if (next >= 0) { event.preventDefault(); items[next]?.focus() }
      }}>
      <button type="button" role="menuitem" disabled={disabled} onClick={() => { setOpen(false); onAction('rename', trigger.current) }}><Pencil size={16} aria-hidden="true" />重命名</button>
      <button type="button" role="menuitem" className="danger-menu-item" disabled={disabled} onClick={() => { setOpen(false); onAction('delete', trigger.current) }}><Trash2 size={16} aria-hidden="true" />删除</button>
    </div>, document.body)}
  </>
}
