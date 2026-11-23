import { ChevronLeft, ChevronRight } from 'lucide-react'
import { ScopeSelect } from './ScopeSelect'

export const DEFAULT_TABLE_PAGE_SIZE = 10
const PAGE_SIZE_OPTIONS = [10, 20, 50].map(size => ({ value: String(size), label: `${size} 条/页` }))

export function TablePagination({ label, total, page, pageSize, onPageChange, onPageSizeChange }: {
  label: string
  total: number
  page: number
  pageSize: number
  onPageChange: (page: number) => void
  onPageSizeChange: (size: number) => void
}) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  const firstMiddlePage = Math.max(2, Math.min(page - 1, pageCount - 3))
  const lastMiddlePage = Math.min(pageCount - 1, firstMiddlePage + 2)
  const middlePages = Array.from({ length: Math.max(0, lastMiddlePage - firstMiddlePage + 1) }, (_, i) => firstMiddlePage + i)

  function pageButton(number: number) {
    return <button key={number} type="button" aria-label={`第 ${number} 页`} aria-current={number === page ? 'page' : undefined}
      onClick={() => onPageChange(number)}>{number}</button>
  }

  return <nav className="table-pagination" aria-label={label}>
    <span className="table-pagination-total">共 {total} 条</span>
    <div className="table-pagination-size">
      <ScopeSelect label="每页条数" hideLabel value={String(pageSize)} options={PAGE_SIZE_OPTIONS} menuMinWidth={0}
        onChange={value => { if (Number(value) !== pageSize) onPageSizeChange(Number(value)) }} />
    </div>
    <div className="table-pagination-pages">
      <button type="button" aria-label="上一页" disabled={page <= 1} onClick={() => onPageChange(page - 1)}><ChevronLeft size={15} aria-hidden="true" /></button>
      {pageButton(1)}
      {firstMiddlePage > 2 && <span aria-hidden="true">…</span>}
      {middlePages.map(pageButton)}
      {lastMiddlePage < pageCount - 1 && <span aria-hidden="true">…</span>}
      {pageCount > 1 && pageButton(pageCount)}
      <button type="button" aria-label="下一页" disabled={page >= pageCount} onClick={() => onPageChange(page + 1)}><ChevronRight size={15} aria-hidden="true" /></button>
    </div>
  </nav>
}
