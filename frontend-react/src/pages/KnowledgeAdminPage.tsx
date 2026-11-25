import { AlertCircle, ChevronDown, ChevronRight, FileText, FolderOpen, Layers3, Plus, RefreshCw, Search, UploadCloud, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import type { ChangeEvent } from 'react'
import { formatApiError } from '../api/httpClient'
import { versionPresentation, versionUploadMetadata } from '../api/documentVersionState'
import { renameDocument } from '../api/documentVersions'
import { DOCUMENT_ACCEPT, documentFileError, isPlainTextDocument, knowledgeAdminView, searchKnowledgeBases } from '../api/knowledgeAdminView'
import * as knowledgeBaseApi from '../api/knowledgeBaseApi'
import { indexPresentation, KnowledgeIndexController } from '../api/knowledgeIndexState'
import { DocumentIndexStatus } from '../components/DocumentIndexStatus'
import { DocumentVersionControls } from '../components/DocumentVersionControls'
import { DocumentDeletionControls } from '../components/DocumentDeletionControls'
import { DocumentCategoryManager } from '../components/DocumentCategoryManager'
import { SessionActionDialog } from '../components/SessionActionDialog'
import { DEFAULT_TABLE_PAGE_SIZE, TablePagination } from '../components/TablePagination'
import { ScopeSelect } from '../components/ScopeSelect'
import type { KnowledgeBaseCategory, KnowledgeBaseItem, RagStatusDocument } from '../types/knowledgeBase'
import '../styles/knowledge-admin.css'

const ADMIN_UPLOAD_SESSION_ID = 'react-admin-knowledge-upload'
const ADMIN_USER_ROLE = 'kb_admin'
const DEFAULT_KNOWLEDGE_BASE_ID = 'enterprise_default'
const KNOWLEDGE_TABS = [
  { id: 'libraries', label: '知识库管理' },
  { id: 'documents', label: '文档管理' },
  { id: 'categories', label: '分类管理' },
] as const
type NameMode = 'create' | 'rename' | 'edit-base' | 'delete-base'

function createIndexController(knowledgeBaseId: string) {
  return new KnowledgeIndexController({
    session_id: ADMIN_UPLOAD_SESSION_ID, knowledge_base_id: knowledgeBaseId, user_role: ADMIN_USER_ROLE,
  }, knowledgeBaseApi)
}

export const KnowledgeAdminPage = () => {
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseItem[]>([])
  const [categories, setCategories] = useState<KnowledgeBaseCategory[]>([])
  const [selectedCategoryId, setSelectedCategoryId] = useState('')
  const [selectedKnowledgeBaseId, setSelectedKnowledgeBaseId] = useState('')
  const [uploadKnowledgeBaseId, setUploadKnowledgeBaseId] = useState('')
  const [versionTarget, setVersionTarget] = useState<string | null>(null)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [search, setSearch] = useState('')
  const [tab, setTab] = useState<(typeof KNOWLEDGE_TABS)[number]['id']>('libraries')
  const [librarySearch, setLibrarySearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(DEFAULT_TABLE_PAGE_SIZE)
  const [isLoading, setIsLoading] = useState(true)
  const [configError, setConfigError] = useState('')
  const [configAttempt, setConfigAttempt] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [isReadingFile, setIsReadingFile] = useState(false)
  const [detailId, setDetailId] = useState<string | null>(null)
  const [nameMode, setNameMode] = useState<NameMode | null>(null)
  const [nameBase, setNameBase] = useState<KnowledgeBaseItem | null>(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [nameError, setNameError] = useState('')
  const [savingName, setSavingName] = useState(false)
  const [indexingDocument, setIndexingDocument] = useState<number | null>(null)
  const [detailError, setDetailError] = useState('')
  const nameLock = useRef(false)
  const indexLock = useRef(false)
  const detailTrigger = useRef<HTMLButtonElement>(null)
  const nameTrigger = useRef<HTMLButtonElement>(null)
  const readingFile = useRef(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const uploadTrigger = useRef<HTMLButtonElement>(null)
  const indexController = useMemo(() => createIndexController(selectedKnowledgeBaseId), [selectedKnowledgeBaseId])
  const indexState = useSyncExternalStore(indexController.subscribe, indexController.getSnapshot)
  const uploadBaseId = versionTarget === '' ? uploadKnowledgeBaseId : selectedKnowledgeBaseId
  const uploadController = useMemo(() => uploadBaseId === selectedKnowledgeBaseId
    ? indexController : createIndexController(uploadBaseId), [uploadBaseId, selectedKnowledgeBaseId, indexController])
  const uploadState = useSyncExternalStore(uploadController.subscribe, uploadController.getSnapshot)
  const ragStatus = indexState.status
  const uploadResult = uploadState.upload
  const isUploading = uploadState.uploading || isReadingFile
  const visibleError = errorMessage || indexState.error || indexState.queryError
  const uploadError = errorMessage || uploadState.error || uploadState.queryError
  const selectedCategory = categories.find(item => item.category_id === selectedCategoryId)
  const view = knowledgeAdminView(ragStatus?.documents ?? [])
  const activeBase = knowledgeBases.find(item => item.knowledge_base_id === selectedKnowledgeBaseId)
  const searchingBases = Boolean(search.trim())
  const matchingBases = searchKnowledgeBases(knowledgeBases, search)
  const libraryRows = searchKnowledgeBases(knowledgeBases, librarySearch)
  const documentTotal = view.groups.length
  const currentPage = Math.min(page, Math.max(1, Math.ceil(documentTotal / pageSize)))
  const visibleGroups = view.groups.slice((currentPage - 1) * pageSize, currentPage * pageSize)
  const hasStatus = ragStatus !== null
  const targetGroup = view.groups.find(group => group.id === versionTarget)
  const detailGroup = view.groups.find(group => group.id === detailId)
  const canUpload = !isLoading && uploadState.status !== null && Boolean(uploadBaseId) && !isUploading
  const canOpenUpload = !isLoading && knowledgeBases.length > 0 && !isUploading

  useEffect(() => {
    let ignore = false
    async function loadConfig() {
      setIsLoading(true)
      setConfigError('')
      try {
        const [knowledgeBaseResponse, categoryResponse] = await Promise.all([
          knowledgeBaseApi.fetchKnowledgeBases(), knowledgeBaseApi.fetchKnowledgeBaseCategories(),
        ])
        if (ignore) return
        setKnowledgeBases(knowledgeBaseResponse.knowledge_bases)
        setCategories(categoryResponse.categories)
        setSelectedKnowledgeBaseId(previous => knowledgeBaseResponse.knowledge_bases.some(kb => kb.knowledge_base_id === previous)
          ? previous : knowledgeBaseResponse.knowledge_bases[0]?.knowledge_base_id ?? '')
        setSelectedCategoryId(previous => categoryResponse.categories.some(category => category.category_id === previous)
          ? previous : categoryResponse.categories[0]?.category_id ?? '')
      } catch (error) {
        if (!ignore) setConfigError(formatApiError(error, '知识库配置加载失败，请重试。'))
      } finally {
        if (!ignore) setIsLoading(false)
      }
    }
    void loadConfig()
    return () => { ignore = true }
  }, [configAttempt])

  useEffect(() => {
    indexController.start()
    return () => indexController.dispose()
  }, [indexController])

  useEffect(() => {
    if (uploadController === indexController) return
    uploadController.start()
    return () => uploadController.dispose()
  }, [uploadController, indexController])

  function handleKnowledgeBaseChange(value: string) {
    setSearch('')
    if (value === selectedKnowledgeBaseId) return
    setErrorMessage('')
    setVersionTarget(null)
    setDetailId(null)
    setPage(1)
    setSelectedKnowledgeBaseId(value)
  }

  function changeUploadBase(value: string) {
    if (versionTarget !== '' || isUploading || value === uploadBaseId) return
    setUploadKnowledgeBaseId(value)
    setErrorMessage('')
  }

  function saveCategory(category: KnowledgeBaseCategory) {
    setCategories(items => items.some(item => item.category_id === category.category_id)
      ? items.map(item => item.category_id === category.category_id ? category : item)
      : [...items, category])
    setSelectedCategoryId(current => current || category.category_id)
  }

  function removeCategory(id: string) {
    const remaining = categories.filter(category => category.category_id !== id)
    setCategories(remaining)
    setSelectedCategoryId(current => current === id ? remaining[0]?.category_id ?? '' : current)
  }

  function openName(trigger: HTMLButtonElement, mode: NameMode, base?: KnowledgeBaseItem) {
    nameTrigger.current = trigger
    setName(mode === 'rename' ? detailGroup?.name ?? '' : base?.name ?? '')
    setDescription(base?.description ?? '')
    setNameBase(base ?? null)
    setNameError('')
    setNameMode(mode)
  }

  async function saveName() {
    if (nameLock.current || !nameMode || (nameMode !== 'delete-base' && !name.trim())) return
    nameLock.current = true
    setSavingName(true)
    setNameError('')
    try {
      if (nameMode === 'create') {
        const created = await knowledgeBaseApi.createKnowledgeBase(name.trim(), description.trim())
        setKnowledgeBases(items => items.some(item => item.knowledge_base_id === created.knowledge_base_id) ? items : [...items, created])
        handleKnowledgeBaseChange(created.knowledge_base_id)
      } else if (nameMode === 'edit-base' && nameBase) {
        const updated = await knowledgeBaseApi.updateKnowledgeBase(nameBase.knowledge_base_id, name.trim(), description.trim())
        setKnowledgeBases(items => items.map(item => item.knowledge_base_id === updated.knowledge_base_id ? updated : item))
      } else if (nameMode === 'delete-base' && nameBase) {
        const id = nameBase.knowledge_base_id
        await knowledgeBaseApi.deleteKnowledgeBase(id)
        const remaining = knowledgeBases.filter(item => item.knowledge_base_id !== id)
        setKnowledgeBases(remaining)
        if (id === selectedKnowledgeBaseId) handleKnowledgeBaseChange(remaining[0]?.knowledge_base_id ?? '')
      } else if (nameMode === 'rename') {
        const version = detailGroup?.versions[0]
        if (!version?.logical_document_id || !version.logical_state_version) return
        await renameDocument(version.logical_document_id, selectedKnowledgeBaseId, version.logical_state_version, name.trim())
        await indexController.refreshAfterDocumentChange()
      }
      setNameMode(null)
    } catch (error) {
      setNameError(formatApiError(error, '保存失败，请核对后重试。'))
    } finally {
      nameLock.current = false
      setSavingName(false)
    }
  }

  async function buildIndex(documentId: number) {
    if (indexLock.current) return
    indexLock.current = true
    setIndexingDocument(documentId)
    setDetailError('')
    try {
      await knowledgeBaseApi.indexExistingDocument(documentId, {
        session_id: ADMIN_UPLOAD_SESSION_ID, knowledge_base_id: selectedKnowledgeBaseId, user_role: ADMIN_USER_ROLE,
      })
    } catch (error) {
      setDetailError(formatApiError(error, '索引结果尚未确认，请核对文档状态。'))
    } finally {
      await indexController.refreshAfterDocumentChange()
      indexLock.current = false
      setIndexingDocument(null)
    }
  }

  function openUpload(trigger: HTMLButtonElement, identity = '') {
    uploadTrigger.current = trigger
    setSelectedFile(null)
    setErrorMessage('')
    if (!indexState.uploadUncertain) indexController.clearUpload()
    setUploadKnowledgeBaseId(selectedKnowledgeBaseId)
    setVersionTarget(identity)
    if (!identity) return
    const versions = (ragStatus?.documents ?? []).filter(document => document.logical_document_id === identity)
    const current = versions.find(document => document.is_current_version)
      || versions.sort((a, b) => (b.version_number || 1) - (a.version_number || 1))[0]
    const category = categories.find(item => item.knowledge_base_type === current?.knowledge_base_type)
    if (category) setSelectedCategoryId(category.category_id)
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null
    if (!file) return
    const error = documentFileError(file)
    setErrorMessage(error)
    setSelectedFile(error ? null : file)
    if (error) event.target.value = ''
    else uploadController.clearUpload()
  }

  async function handleUploadDocument() {
    if (readingFile.current || !canUpload || uploadState.uploadUncertain || uploadResult || versionTarget === null) return
    if (!selectedFile || !uploadBaseId || !selectedCategory) {
      setErrorMessage('请选择知识库、文档分类和文件后再上传。')
      return
    }
    if (versionTarget && !targetGroup) {
      setErrorMessage('找不到要更新的文档，请关闭窗口后重新选择。')
      return
    }
    const error = documentFileError(selectedFile)
    if (error) { setErrorMessage(error); return }
    try {
      readingFile.current = true
      setIsReadingFile(true)
      setErrorMessage('')
      const documentText = await (isPlainTextDocument(selectedFile.name)
        ? selectedFile.text() : knowledgeBaseApi.extractKnowledgeDocument(selectedFile))
      if (!documentText.trim()) { setErrorMessage('文档正文为空，请重新选择文件。'); return }
      await uploadController.upload({
        session_id: ADMIN_UPLOAD_SESSION_ID, document_text: documentText,
        logical_document_id: versionTarget || undefined, file_name: selectedFile.name,
        knowledge_base_id: uploadBaseId, user_role: ADMIN_USER_ROLE,
        ...versionUploadMetadata(ragStatus?.documents ?? [], versionTarget, selectedCategory),
      })
    } catch (error) {
      setErrorMessage(error instanceof DOMException && error.name === 'TimeoutError'
        ? '提取正文超时，文件尚未入库。请尝试更小的文件。'
        : formatApiError(error, '读取文档失败，请重新选择文件。'))
    } finally {
      readingFile.current = false
      setIsReadingFile(false)
    }
  }

  function renderDocument(document: RagStatusDocument, position: number) {
    const operationId = document.vector_index?.operation_id || indexState.uncertainDocuments[document.document_id ?? 0]
    const category = categories.find(item => item.knowledge_base_type === document.knowledge_base_type)
    const canBuildIndex = ragStatus?.effective_retrieval_mode === 'vector' && document.document_id
      && ['unknown', 'not_requested'].includes(document.vector_index?.status ?? 'unknown')
    return <details className="kb-version-card" key={document.document_id ?? document.file_name} open={position === 0 || undefined}>
      <summary>
        <span className="kb-version-number">V{document.version_number || 1}</span>
        <strong>{document.file_name ?? '未命名文档'}</strong>
        <span className={`status-badge ${document.lifecycle_state === 'deleting' ? 'status-running' : document.is_current_version ? 'status-success' : ''}`}>
          {document.lifecycle_state === 'deleting' ? '删除中' : versionPresentation(document).label}
        </span>
        <ChevronDown size={16} aria-hidden="true" />
      </summary>
      <div className="kb-version-content">
        <dl className="kb-version-facts">
          <div><dt>分类</dt><dd>{category?.label || document.knowledge_base_type || '未分类'}</dd></div>
          <div><dt>文本片段</dt><dd>{document.chunk_count ?? 0}</dd></div>
          {document.department ? <div><dt>所属部门</dt><dd>{document.department}</dd></div> : null}
        </dl>
        {document.lifecycle_state !== 'deleting' ? <DocumentIndexStatus index={document.vector_index} operationId={operationId}
          uncertain={Boolean(operationId && indexState.uncertain.includes(operationId))}
          busy={Boolean(operationId && indexState.busy.includes(operationId))}
          onQuery={operationId ? () => { void indexController.query(operationId) } : undefined}
          onExecute={operationId ? action => { void indexController.execute(operationId, action) } : undefined} /> : null}
        <div className="kb-document-actions">
          {document.lifecycle_state !== 'deleting' && canBuildIndex ? <button type="button" className="ghost-button"
            disabled={indexingDocument !== null} onClick={() => void buildIndex(document.document_id!)}
            >{indexingDocument === document.document_id ? '正在建立索引…' : '建立向量索引'}</button> : null}
          {document.lifecycle_state !== 'deleting' ? <DocumentVersionControls document={document} knowledgeBaseId={selectedKnowledgeBaseId} onChanged={indexController.refreshAfterDocumentChange} /> : null}
          <DocumentDeletionControls document={document} knowledgeBaseId={selectedKnowledgeBaseId} onChanged={indexController.refreshAfterDocumentChange} />
        </div>
      </div>
    </details>
  }

  return <section className="page-stack knowledge-admin-page">
    <header className="page-header">
      <h2>知识库管理</h2>
    </header>

    <div className="workspace-tabs" role="tablist" aria-label="知识库模块"
      onKeyDown={event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
        event.preventDefault()
        const current = KNOWLEDGE_TABS.findIndex(item => item.id === tab)
        const last = KNOWLEDGE_TABS.length - 1
        const step = event.key === 'ArrowRight' ? 1 : last
        const next = KNOWLEDGE_TABS[event.key === 'Home' ? 0 : event.key === 'End' ? last : (current + step) % KNOWLEDGE_TABS.length].id
        setTab(next)
        event.currentTarget.querySelector<HTMLButtonElement>('#kb-tab-' + next)?.focus()
      }}>
      {KNOWLEDGE_TABS.map(item => <button type="button" key={item.id} id={'kb-tab-' + item.id}
        role="tab" aria-selected={tab === item.id} aria-controls={'kb-panel-' + item.id}
        tabIndex={tab === item.id ? 0 : -1} onClick={() => setTab(item.id)}>{item.label}</button>)}
    </div>

    {configError ? <div className="alert error kb-alert" role="alert"><span>{configError}</span>
      <button className="ghost-button" disabled={isLoading} onClick={() => setConfigAttempt(value => value + 1)}>重新加载</button></div> : null}
    {tab === 'documents' && versionTarget === null && visibleError && hasStatus ? <div className="alert error kb-alert" role="alert">
      <span>{visibleError}</span><button type="button" className="ghost-button" disabled={indexState.loading || isUploading}
        onClick={() => { setErrorMessage(''); void indexController.refresh() }}>刷新列表</button>
    </div> : null}

    <section className="panel kb-library" id="kb-panel-libraries" role="tabpanel"
      aria-labelledby="kb-tab-libraries" hidden={tab !== 'libraries'}>
      <div className="kb-toolbar">
        <label className="kb-search"><Search size={17} aria-hidden="true" />
          <input aria-label="搜索知识库" type="search" value={librarySearch} onChange={event => setLibrarySearch(event.target.value)}
            placeholder="输入知识库名称" disabled={isLoading} />
        </label>
        <div className="workspace-panel-actions">
          <button type="button" className="primary-button" disabled={isLoading || isUploading}
            onClick={event => openName(event.currentTarget, 'create')}><Plus size={17} aria-hidden="true" />新建知识库</button>
        </div>
      </div>
      {isLoading ? <div className="kb-empty" role="status"><h3>正在加载知识库</h3></div>
        : libraryRows.length === 0 ? <div className="kb-empty" role="status">
          <h3>{configError ? '知识库列表暂不可用' : librarySearch.trim() ? '没有找到匹配的知识库' : '暂无知识库'}</h3>
        </div> : <div className="admin-table-scroll kb-table-scroll kb-registry-table" tabIndex={0} role="region" aria-label="知识库列表">
          <table className="admin-table kb-table">
            <caption className="sr-only">知识库列表</caption>
            <thead><tr><th scope="col">知识库名称</th><th scope="col">简介</th><th scope="col">操作</th></tr></thead>
            <tbody>{libraryRows.map(base => <tr key={base.knowledge_base_id}>
              <th scope="row"><span className="kb-table-name"><FolderOpen size={17} aria-hidden="true" />{base.name}</span></th>
              <td>{base.description || '—'}</td>
              <td><div className="kb-row-actions">
                <button type="button" className="text-button" onClick={() => {
                  handleKnowledgeBaseChange(base.knowledge_base_id)
                  setTab('documents')
                }}>查看文档</button>
                <button type="button" className="text-button" onClick={event => openName(event.currentTarget, 'edit-base', base)}>编辑</button>
                {base.knowledge_base_id !== DEFAULT_KNOWLEDGE_BASE_ID ? <button type="button" className="text-button kb-delete-action"
                  onClick={event => openName(event.currentTarget, 'delete-base', base)}>删除</button> : null}
              </div></td>
            </tr>)}</tbody>
          </table>
        </div>}
    </section>
    <section className="panel kb-library" id="kb-panel-documents" role="tabpanel"
      aria-labelledby="kb-tab-documents" hidden={tab !== 'documents'}>
      <form className="kb-toolbar" onSubmit={event => {
        event.preventDefault()
        if (searchingBases && matchingBases.length === 1) handleKnowledgeBaseChange(matchingBases[0].knowledge_base_id)
      }}>
        <ScopeSelect label="当前知识库" hideLabel icon={<FolderOpen size={17} aria-hidden="true" />}
          value={selectedKnowledgeBaseId} disabled={isLoading || isUploading} onChange={handleKnowledgeBaseChange}
          placeholder={isLoading ? '正在加载…' : '暂无可用知识库'}
          options={knowledgeBases.map(item => ({ value: item.knowledge_base_id, label: item.name, description: item.description }))} />
        <label className="kb-search"><Search size={17} aria-hidden="true" />
          <input aria-label="搜索知识库" type="search" value={search} onChange={event => setSearch(event.target.value)}
            placeholder="输入知识库名称" disabled={isLoading || isUploading} />
        </label>
        <div className="workspace-panel-actions">
          <button type="button" className="primary-button" disabled={!canOpenUpload} onClick={event => openUpload(event.currentTarget)}>
            <UploadCloud size={17} aria-hidden="true" />上传文档
          </button>
        </div>
      </form>
      {searchingBases ? (matchingBases.length ? <ul className="kb-search-results" aria-label="匹配的知识库">
        {matchingBases.map(base => <li key={base.knowledge_base_id}><button type="button" disabled={isUploading}
          onClick={() => handleKnowledgeBaseChange(base.knowledge_base_id)}>
          <FolderOpen size={18} aria-hidden="true" /><span><strong>{base.name}</strong>
            {base.description ? <small>{base.description}</small> : null}</span><ChevronRight size={16} aria-hidden="true" />
        </button></li>)}
      </ul> : <div className="kb-empty" role="status"><Search size={25} aria-hidden="true" /><h3>没有找到匹配的知识库</h3></div>) : <>
      {activeBase ? <div className="kb-library-heading"><h3>{activeBase.name}</h3>
        {hasStatus ? <p className="kb-count" aria-live="polite">{documentTotal} 份文档 · {view.currentCount} 份生效</p> : null}
      </div> : null}
      {!hasStatus ? <div className="kb-empty" role="status">
        {isLoading || indexState.loading ? <><RefreshCw className="kb-spinning" size={25} aria-hidden="true" /><h3>正在加载文档</h3></>
          : <><AlertCircle size={26} aria-hidden="true" /><h3>{selectedKnowledgeBaseId ? '文档列表暂不可用' : '尚未选择知识库'}</h3>
            {activeBase ? <button type="button" className="text-button" onClick={() => void indexController.refresh()}>重新加载文档</button>
              : <p>新建知识库后即可上传文档。</p>}</>}
      </div> : view.groups.length === 0 ? <div className="kb-empty">
        <span className="kb-empty-icon"><FolderOpen size={27} aria-hidden="true" /></span><h3>这个知识库还没有文档</h3>
        <p>点击「上传文档」添加资料。</p>
      </div> : <>
      <div className="admin-table-scroll kb-table-scroll" tabIndex={0} role="region" aria-label="文档列表"><table className="admin-table kb-table">
        <caption className="sr-only">当前知识库的文档</caption>
        <thead><tr><th scope="col">文档名称</th><th scope="col">分类</th><th scope="col">版本</th><th scope="col">索引状态</th><th scope="col">操作</th></tr></thead>
        <tbody>{visibleGroups.map(group => {
          const primary = group.versions.find(document => document.is_current_version) || group.versions[0]
          const operationId = primary.vector_index?.operation_id || indexState.uncertainDocuments[primary.document_id ?? 0]
          const status = indexPresentation(primary.vector_index, Boolean(operationId && indexState.uncertain.includes(operationId)))
          const category = categories.find(item => item.knowledge_base_type === primary.knowledge_base_type)
          return <tr key={group.id}>
            <th scope="row"><span className="kb-table-name"><FileText size={17} aria-hidden="true" />{group.name}</span></th>
            <td>{category?.label || primary.knowledge_base_type || '未分类'}</td>
            <td><span>V{primary.version_number || 1}</span><small>{versionPresentation(primary).label}</small></td>
            <td>{primary.lifecycle_state === 'deleting'
              ? <span className="status-badge status-running">删除中</span>
              : <span className={'status-badge ' + status.tone}>{status.label}</span>}</td>
            <td><button type="button" className="text-button" aria-label={'管理 ' + group.name} onClick={event => {
              detailTrigger.current = event.currentTarget
              setDetailError('')
              setDetailId(group.id)
            }}>查看与管理</button></td>
          </tr>
        })}</tbody>
      </table></div>
      <div className="kb-pagination">
        <TablePagination label="文档分页" total={documentTotal} page={currentPage} pageSize={pageSize}
          onPageChange={setPage} onPageSizeChange={size => { setPageSize(size); setPage(1) }} />
      </div></>}
      </>}
    </section>

    <section className="panel kb-library" id="kb-panel-categories" role="tabpanel"
      aria-labelledby="kb-tab-categories" hidden={tab !== 'categories'}>
      <DocumentCategoryManager categories={categories} loading={isLoading} onSaved={saveCategory} onDeleted={removeCategory} />
    </section>

    {detailGroup ? <SessionActionDialog title="文档详情" busy={indexingDocument !== null}
      returnFocusTo={detailTrigger} onClose={() => setDetailId(null)}>
      <div className="kb-detail-heading">
        <h4>{detailGroup.name}</h4>
        {detailGroup.versions[0].logical_document_id ? <div className="kb-header-actions">
          <button type="button" className="ghost-button" disabled={indexingDocument !== null}
            onClick={event => openName(event.currentTarget, 'rename')}>编辑名称</button>
          <button type="button" className="primary-button kb-version-upload" disabled={!canUpload || indexingDocument !== null}
            onClick={event => openUpload(event.currentTarget, detailGroup.id)}>
            <UploadCloud size={16} aria-hidden="true" />上传新版本
          </button>
        </div> : null}
      </div>
      {detailError ? <p className="alert error" role="alert">{detailError}</p> : null}
      <div className="kb-version-list">
        <h5>版本记录 <small>{detailGroup.versions.length}</small></h5>
        {[...detailGroup.versions]
        .sort((a, b) => Number(Boolean(b.is_current_version)) - Number(Boolean(a.is_current_version)))
        .map(renderDocument)}</div>
    </SessionActionDialog> : null}

    {nameMode ? <SessionActionDialog title={nameMode === 'create' ? '新建知识库' : nameMode === 'edit-base' ? '编辑知识库'
      : nameMode === 'delete-base' ? '删除知识库' : '编辑文档名称'} busy={savingName}
      returnFocusTo={nameTrigger} onClose={() => setNameMode(null)}>
      <form onSubmit={event => { event.preventDefault(); void saveName() }}>
        {nameError ? <p className="alert error" role="alert">{nameError}</p> : null}
        {nameMode === 'delete-base' ? <p>确定删除「{nameBase?.name}」？只允许删除空知识库。</p>
          : <label className="field"><span>{nameMode === 'rename' ? '文档名称' : '知识库名称'}</span>
            <input required maxLength={nameMode === 'rename' ? 255 : 80} value={name} disabled={savingName}
              onChange={event => setName(event.target.value)} /></label>}
        {nameMode === 'create' || nameMode === 'edit-base' ? <label className="field"><span>简介（选填）</span>
          <textarea rows={3} maxLength={300} value={description} disabled={savingName}
            onChange={event => setDescription(event.target.value)} /></label> : null}
        <div className="session-dialog-actions">
          <button type="button" className="ghost-button" disabled={savingName} onClick={() => setNameMode(null)}>取消</button>
          <button type="submit" className={nameMode === 'delete-base' ? 'primary-button danger-solid-button' : 'primary-button'}
            disabled={savingName || (nameMode !== 'delete-base' && !name.trim())}>
            {savingName ? '处理中…' : nameMode === 'delete-base' ? '确认删除' : nameMode === 'create' ? '创建' : '保存'}
          </button>
        </div>
      </form>
    </SessionActionDialog> : null}

    {versionTarget !== null ? <SessionActionDialog title={versionTarget ? '上传新版本' : '上传文档'} busy={isUploading}
      returnFocusTo={uploadTrigger} onClose={() => { setVersionTarget(null); setErrorMessage('') }}>
      <div className="kb-upload">
        {uploadError ? <div className="alert error" role="alert">{uploadError}
          {uploadState.uploadUncertain || uploadState.queryError ? <button type="button" className="text-button" disabled={uploadState.loading}
            onClick={() => void uploadController.refresh()}>刷新文档列表</button> : null}
        </div> : null}
        {uploadResult ? <div className="upload-result-card" role="status"><strong>{uploadResult.document_reused ? '已复用已有文档' : '文本已保存'}</strong>
          <span>{uploadResult.file_name || selectedFile?.name || '未命名文档'} · {uploadResult.chunk_count} 个片段</span>
          <DocumentIndexStatus index={uploadResult.vector_index} operationId={uploadResult.operation_id}
            uncertain={!uploadResult.state_confirmed || uploadState.uncertain.includes(uploadResult.operation_id || '')}
            busy={uploadState.busy.includes(uploadResult.operation_id || '')}
            onQuery={() => { if (uploadResult.operation_id) void uploadController.query(uploadResult.operation_id) }}
            onExecute={action => { if (uploadResult.operation_id) void uploadController.execute(uploadResult.operation_id, action) }} />
        </div> : <>
          {versionTarget ? <p className="kb-upload-target">{targetGroup?.name || '文档已不可用'} · {activeBase?.name}</p>
            : <ScopeSelect label="上传到知识库" icon={<FolderOpen size={17} aria-hidden="true" />}
              value={uploadBaseId} disabled={isUploading || uploadState.uploadUncertain} onChange={changeUploadBase}
              options={knowledgeBases.map(item => ({ value: item.knowledge_base_id, label: item.name }))} />}
          <ScopeSelect label="文档分类" icon={<Layers3 size={17} aria-hidden="true" />} value={selectedCategoryId}
            placeholder="暂无可用分类" disabled={isUploading} onChange={setSelectedCategoryId}
            options={categories.map(category => ({ value: category.category_id, label: category.label, description: category.description }))} />
          <label className={'kb-file-picker' + (isUploading ? ' is-disabled' : '')}>
            <input ref={fileInput} className="kb-file-input" aria-label="选择文档文件" type="file" accept={DOCUMENT_ACCEPT} disabled={isUploading} onChange={handleFileChange} />
            <UploadCloud size={23} aria-hidden="true" /><strong>{selectedFile ? '更换文件' : '选择本地文件'}</strong>
            <span>Word（.docx）、PDF、PNG / JPG / WEBP、Markdown / TXT · 最大 2 MB</span>
          </label>
          {selectedFile ? <div className="kb-selected-file"><FileText size={17} aria-hidden="true" /><strong>{selectedFile.name}</strong>
            <button type="button" className="text-button" aria-label="移除已选文件" disabled={isUploading}
              onClick={() => { setSelectedFile(null); if (fileInput.current) fileInput.current.value = '' }}><X size={16} aria-hidden="true" /></button>
          </div> : null}
          {versionTarget ? <p className="kb-upload-note">新版本处理成功后自动生效，失败时保留原生效版本。</p> : null}
        </>}
        <div className="session-dialog-actions">
          <button type="button" className={uploadResult ? 'primary-button' : 'ghost-button'} disabled={isUploading}
            onClick={() => { setVersionTarget(null); setErrorMessage('') }}>{uploadResult ? '完成' : '取消'}</button>
          {!uploadResult ? <button type="button" className="primary-button"
            disabled={!canUpload || !selectedFile || !selectedCategory || Boolean(versionTarget && !targetGroup) || uploadState.uploadUncertain}
            onClick={() => { void handleUploadDocument() }}>{isReadingFile && !uploadState.uploading ? '正在提取正文…' : isUploading ? '正在上传…' : '开始上传'}</button> : null}
        </div>
      </div>
    </SessionActionDialog> : null}
  </section>
}
