import type { ProcessStatus } from '../types/process'

export const statusLabels: Record<ProcessStatus, string> = {
  simulated: '模拟完成 · 已记录', running: '执行中', failed: '执行失败', timeout: '超时 · 待核对',
}

export function formatTime(value?: string | null) {
  if (!value) return '—'
  // SQLite datetime('now') 是 UTC，显式标记后再转换为浏览器本地时间。
  const date = new Date(/Z$|[+-]\d\d:\d\d$/.test(value) ? value : value.replace(' ', 'T') + 'Z')
  return Number.isNaN(date.valueOf()) ? '时间不可用' : new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date)
}
