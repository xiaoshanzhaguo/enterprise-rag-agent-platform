import { Check, ChevronDown } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'

export function ScopeSelect({ label, value, options, disabled = false, hideLabel = false, placeholder = '请选择', icon, menuMinWidth = 200, onChange }: {
  label: string; value: string; options: { value: string; label: string; description?: string | null }[];
  disabled?: boolean; hideLabel?: boolean; placeholder?: string; icon?: ReactNode; menuMinWidth?: number; onChange: (value: string) => void
}) {
  const id = useId()
  const root = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const [menuStyle, setMenuStyle] = useState<CSSProperties>({})
  const selected = options.find(option => option.value === value)
  const expanded = open && !disabled && options.length > 0
  const activeIndex = Math.min(active, options.length - 1)
  useEffect(() => {
    if (!open) return
    function close(event: PointerEvent) { if (!root.current?.contains(event.target as Node)) setOpen(false) }
    document.addEventListener('pointerdown', close)
    function reposition() { setOpen(false) }
    function scroll(event: Event) {
      if (!(event.target instanceof Node) || !root.current?.contains(event.target)) setOpen(false)
    }
    window.addEventListener('resize', reposition)
    document.addEventListener('scroll', scroll, true)
    return () => {
      document.removeEventListener('pointerdown', close)
      window.removeEventListener('resize', reposition)
      document.removeEventListener('scroll', scroll, true)
    }
  }, [open])
  function show() {
    const rect = root.current?.querySelector('button')?.getBoundingClientRect()
    if (rect) {
      const below = window.innerHeight - rect.bottom - 12
      const above = rect.top - 12
      const down = below >= 240 || below >= above
      const width = Math.min(Math.max(rect.width, menuMinWidth), window.innerWidth - 16)
      setMenuStyle({ position: 'fixed', left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)), width,
        minWidth: 0, maxHeight: Math.max(40, Math.min(240, down ? below : above)),
        top: down ? rect.bottom + 6 : 'auto', bottom: down ? 'auto' : window.innerHeight - rect.top + 6 })
    }
    setActive(Math.max(0, options.findIndex(option => option.value === value))); setOpen(true)
  }
  return <div className="scope-select" ref={root}>
    <span id={`${id}-label`} className={hideLabel ? 'sr-only' : 'scope-label'}>{label}</span>
    <button type="button" className="scope-trigger" role="combobox" aria-labelledby={`${id}-label`}
      aria-expanded={expanded} aria-haspopup="listbox" aria-controls={expanded ? `${id}-list` : undefined}
      aria-activedescendant={expanded ? `${id}-option-${activeIndex}` : undefined} disabled={disabled || !options.length}
      onClick={() => open ? setOpen(false) : show()} onBlur={event => {
        if (!root.current?.contains(event.relatedTarget as Node | null)) setOpen(false)
      }} onKeyDown={event => {
        if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
          event.preventDefault()
          if (!expanded) { show(); return }
          setActive(index => event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1
            : (index + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length)
        } else if (event.key === 'Escape') { event.preventDefault(); setOpen(false) }
        else if ((event.key === 'Enter' || event.key === ' ') && expanded) {
          event.preventDefault(); onChange(options[activeIndex].value); setOpen(false)
        }
      }}>
      {icon}<span>{selected?.label || placeholder}</span><ChevronDown size={16} aria-hidden="true" />
    </button>
    {expanded ? <div className="scope-options" style={menuStyle} role="listbox" id={`${id}-list`} aria-labelledby={`${id}-label`}>
      {options.map((option, index) => <button type="button" role="option" tabIndex={-1}
        id={`${id}-option-${index}`} key={option.value} aria-selected={option.value === value}
        className={activeIndex === index ? 'is-highlighted' : ''} onPointerMove={() => setActive(index)}
        onMouseDown={event => event.preventDefault()} onClick={() => { onChange(option.value); setOpen(false) }}>
        <span><strong>{option.label}</strong>{option.description ? <small>{option.description}</small> : null}</span>
        {option.value === value ? <Check size={16} aria-hidden="true" /> : null}
      </button>)}
    </div> : null}
  </div>
}
