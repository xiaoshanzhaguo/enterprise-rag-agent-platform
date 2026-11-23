import { AlertCircle, FileText, FolderOpen, Layers3, Plus, RefreshCw, Search, UploadCloud, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import type { ChangeEvent } from 'react'
import { formatApiError } from '../api/httpClient'
import { versionPresentation, versionUploadMetadata } from '../api/documentVersionState'
import { renameDocument } from '../api/documentVersions'
import { documentFileError, knowledgeAdminView } from '../api/knowledgeAdminView'
import * as knowledgeBaseApi from '../api/knowledgeBaseApi'
import { indexPresentation, KnowledgeIndexController } from '../api/knowledgeIndexState'
import { DocumentIndexStatus } from '../components/DocumentIndexStatus'
import { DocumentVersionControls } from '../components/DocumentVersionControls'
import { DocumentDeletionControls } from '../components/DocumentDeletionControls'
import { SessionActionDialog } from '../components/SessionActionDialog'
import { DEFAULT_TABLE_PAGE_SIZE, TablePagination } from '../components/TablePagination'
import { ScopeSelect } from '../components/ScopeSelect'
import type { KnowledgeBaseCategory, KnowledgeBaseItem, RagStatusDocument } from '../types/knowledgeBase'
import '../styles/knowledge-admin.css'

const ADMIN_UPLOAD_SESSION_ID = 'react-admin-knowledge-upload'
const ADMIN_USER_ROLE = 'kb_admin'

export const KnowledgeAdminPage = () => {
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseItem[]>([])
  const [categories, setCategories] = useState<KnowledgeBaseCategory[]>([])
  const [selectedCategoryId, setSelectedCategoryId] = useState('')
  const [selectedKnowledgeBaseId, setSelectedKnowledgeBaseId] = useState('')
  const [versionTarget, setVersionTarget] = useState<string | null>(null)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(DEFAULT_TABLE_PAGE_SIZE)
  const [isLoading, setIsLoading] = useState(true)
  const [configError, setConfigError] = useState('')
  const [configAttempt, setConfigAttempt] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [isReadingFile, setIsReadingFile] = useState(false)
  const [detailId, setDetailId] = useState<string | null>(null)
  const [nameMode, setNameMode] = useState<'create' | 'rename' | null>(null)
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
  const indexController = useMemo(() => new KnowledgeIndexController({
    session_id: ADMIN_UPLOAD_SESSION_ID, knowledge_base_id: selectedKnowledgeBaseId, user_role: ADMIN_USER_ROLE,
  }, knowledgeBaseApi), [selectedKnowledgeBaseId])
  const indexState = useSyncExternalStore(indexController.subscribe, indexController.getSnapshot)
  const ragStatus = indexState.status
  const uploadResult = indexState.upload
  const isUploading = indexState.uploading || isReadingFile
  const visibleError = errorMessage || indexState.error || indexState.queryError
  const selectedCategory = categories.find(item => item.category_id === selectedCategoryId)
  const view = knowledgeAdminView(ragStatus?.documents ?? [], search)
  const documentTotal = view.filteredGroups.length
  const currentPage = Math.min(page, Math.max(1, Math.ceil(documentTotal / pageSize)))
  const visibleGroups = view.filteredGroups.slice((currentPage - 1) * pageSize, currentPage * pageSize)
  const hasStatus = ragStatus !== null
  const targetGroup = view.groups.find(group => group.id === versionTarget)
  const detailGroup = view.groups.find(group => group.id === detailId)
  const canUpload = !isLoading && hasStatus && Boolean(selectedKnowledgeBaseId) && !isUploading

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

  function handleKnowledgeBaseChange(value: string) {
    if (value === selectedKnowledgeBaseId) return
    setErrorMessage('')
    setVersionTarget(null)
    setDetailId(null)
    setSearch('')
    setPage(1)
    setSelectedKnowledgeBaseId(value)
  }

  function openName(trigger: HTMLButtonElement, mode: 'create' | 'rename') {
    nameTrigger.current = trigger
    setName(mode === 'rename' ? detailGroup?.name ?? '' : '')
    setDescription('')
    setNameError('')
    setNameMode(mode)
  }

  async function saveName() {
    if (nameLock.current || !name.trim() || !nameMode) return
    nameLock.current = true
    setSavingName(true)
    setNameError('')
    try {
      if (nameMode === 'create') {
        const created = await knowledgeBaseApi.createKnowledgeBase(name.trim(), description.trim())
        setKnowledgeBases(items => items.some(item => item.knowledge_base_id === created.knowledge_base_id) ? items : [...items, created])
        handleKnowledgeBaseChange(created.knowledge_base_id)
      } else {
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
    else indexController.clearUpload()
  }

  async function handleUploadDocument() {
    if (readingFile.current || !canUpload || indexState.uploadUncertain || uploadResult || versionTarget === null) return
    if (!selectedFile || !selectedKnowledgeBaseId || !selectedCategory) {
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
      const documentText = await selectedFile.text()
      if (!documentText.trim()) { setErrorMessage('文档正文为空，请重新选择文件。'); return }
      await indexController.upload({
        session_id: ADMIN_UPLOAD_SESSION_ID, document_text: documentText,
        logical_document_id: versionTarget || undefined, file_name: selectedFile.name,
        knowledge_base_id: selectedKnowledgeBaseId, user_role: ADMIN_USER_ROLE,
        ...versionUploadMetadata(ragStatus?.documents ?? [], versionTarget, selectedCategory),
      })
    } catch (error) {
      setErrorMessage(formatApiError(error, '读取文档失败，请重新选择文件。'))
    } finally {
      readingFile.current = false
      setIsReadingFile(false)
    }
  }

  function renderDocument(document: RagStatusDocument) {
    const operationId = document.vector_index?.operation_id || indexState.uncertainDocuments[document.document_id ?? 0]
    const category = categories.find(item => item.knowledge_base_type === document.knowledge_base_type)
    const canBuildIndex = ragStatus?.effective_retrieval_mode === 'vector' && document.document_id
      && ['unknown', 'not_requested'].includes(document.vector_index?.status ?? 'unknown')
    return <article className="knowledge-document kb-version-card" key={document.document_id ?? document.file_name}>
      <div className="knowledge-document-title">
        <FileText aria-hidden="true" size={17} />
        <strong title={document.file_name ?? '未命名文档'}>{document.file_name ?? '未命名文档'}</strong>
      </div>
      <p className="kb-document-meta">{category?.label || document.knowledge_base_type || '未分类'}
        <span aria-hidden="true"> · </span>{document.chunk_count ?? 0} 个文本片段
        {document.department && document.department !== category?.label ? <> · {document.department}</> : null}</p>
      {document.lifecycle_state !== 'deleting' ? <DocumentIndexStatus index={document.vector_index} operationId={operationId}
        uncertain={Boolean(operationId && indexState.uncertain.includes(operationId))}
        busy={Boolean(operationId && indexState.busy.includes(operationId))}
        onQuery={operationId ? () => { void indexController.query(operationId) } : undefined}
        onExecute={operationId ? action => { void indexController.execute(operationId, action) } : undefined} /> : null}
      <div className="kb-document-actions">
        {document.lifecycle_state !== 'deleting' && canBuildIndex ? <button type="button" className="ghost-button"
          disabled={indexingDocument !== null} onClick={() => void buildIndex(document.document_id!)}
          >{indexingDocument === document.document_id ? '正在建立索引…' : '建立向量索引'}</button> : null}
        <DocumentVersionControls document={document} knowledgeBaseId={selectedKnowledgeBaseId} onChanged={indexController.refreshAfterDocumentChange} />
        <DocumentDeletionControls document={document} knowledgeBaseId={selectedKnowledgeBaseId} onChanged={indexController.refreshAfterDocumentChange} />
      </div>
    </article>
  }

  return <section className="page-stack knowledge-admin-page">
    <header className="page-header kb-page-header">
      <div><h2>知识库管理</h2><p>搜索资料，维护文档和生效版本。</p></div>
      <div className="kb-header-actions">
        <button type="button" className="ghost-button" disabled={isLoading || isUploading}
          onClick={event => openName(event.currentTarget, 'create')}><Plus size={17} aria-hidden="true" />新建知识库</button>
        <button type="button" className="primary-button" disabled={!canUpload} onClick={event => openUpload(event.currentTarget)}>
          <UploadCloud size={17} aria-hidden="true" />上传文档
        </button>
      </div>
    </header>

    {configError ? <div className="alert error kb-alert" role="alert"><span>{configError}</span>
      <button className="ghost-button" disabled={isLoading} onClick={() => setConfigAttempt(value => value + 1)}>重新加载</button></div> : null}
    {versionTarget === null && visibleError ? <div className="alert error" role="alert">{visibleError}</div> : null}

    <section className="panel kb-library" aria-label="知识库文档">
      <div className="kb-toolbar">
        <ScopeSelect label="当前知识库" hideLabel icon={<FolderOpen size={17} aria-hidden="true" />}
          value={selectedKnowledgeBaseId} disabled={isLoading || isUploading} onChange={handleKnowledgeBaseChange}
          placeholder={isLoading ? '正在加载…' : '暂无可用知识库'}
          options={knowledgeBases.map(item => ({ value: item.knowledge_base_id, label: item.name, description: item.description }))} />
        <label className="kb-search"><Search size={17} aria-hidden="true" />
          <input aria-label="搜索文档" type="search" value={search} onChange={event => { setSearch(event.target.value); setPage(1) }} placeholder="搜索文档或版本文件名" disabled={!hasStatus} />
        </label>
        <button type="button" className="ghost-button kb-refresh" disabled={!selectedKnowledgeBaseId || indexState.loading || isUploading}
          onClick={() => { setErrorMessage(''); void indexController.refresh() }}>
          <RefreshCw size={15} aria-hidden="true" className={indexState.loading ? 'kb-spinning' : ''} />
          {indexState.loading ? '刷新中…' : '刷新'}
        </button>
      </div>
      {hasStatus ? <p className="kb-count" aria-live="polite">
        {search.trim() ? '找到 ' + view.filteredGroups.length + ' / ' + view.groups.length + ' 份文档' : view.groups.length + ' 份文档'}
        <span aria-hidden="true"> · </span>{view.currentCount} 份生效
      </p> : null}

      {!hasStatus ? <div className="kb-empty" role="status">
        {isLoading || indexState.loading ? <><RefreshCw className="kb-spinning" size={25} aria-hidden="true" /><h3>正在加载文档</h3></>
          : <><AlertCircle size={26} aria-hidden="true" /><h3>{selectedKnowledgeBaseId ? '文档列表暂不可用' : '尚未选择知识库'}</h3>
            <p>{selectedKnowledgeBaseId ? '请点击刷新重试。' : '请先选择可用知识库。'}</p></>}
      </div> : view.groups.length === 0 ? <div className="kb-empty">
        <span className="kb-empty-icon"><FolderOpen size={27} aria-hidden="true" /></span><h3>这个知识库还没有文档</h3>
        <p>点击「上传文档」添加 Markdown 或纯文本资料。</p>
      </div> : !view.filteredGroups.length ? <div className="kb-empty" role="status"><Search size={26} aria-hidden="true" /><h3>没有找到匹配文档</h3>
        <button type="button" className="text-button" onClick={() => { setSearch(''); setPage(1) }}>清除搜索</button>
      </div> : <>
      <div className="admin-table-scroll kb-table-scroll" tabIndex={0} role="region" aria-label="文档列表"><table className="admin-table kb-table">
        <caption className="sr-only">当前知识库的文档</caption>
        <thead><tr><th scope="col">文档</th><th scope="col">分类</th><th scope="col">版本</th><th scope="col">索引状态</th><th scope="col">操作</th></tr></thead>
        <tbody>{visibleGroups.map(group => {
          const primary = group.versions.find(document => document.is_current_version) || group.versions[0]
          const operationId = primary.vector_index?.operation_id || indexState.uncertainDocuments[primary.document_id ?? 0]
          const status = indexPresentation(primary.vector_index, Boolean(operationId && indexState.uncertain.includes(operationId)))
          const category = categories.find(item => item.knowledge_base_type === primary.knowledge_base_type)
          return <tr key={group.id}>
            <th scope="row"><span className="kb-table-name"><FileText size={17} aria-hidden="true" />{group.name}</span></th>
            <td>{category?.label || primary.knowledge_base_type || '未分类'}</td>
            <td><span>V{primary.version_number || 1} · {versionPresentation(primary).label}</span>
              <small>{group.versions.length} 个版本</small></td>
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
      <div className="kb-version-list" aria-label="文档版本">{[...detailGroup.versions]
        .sort((a, b) => Number(Boolean(b.is_current_version)) - Number(Boolean(a.is_current_version)))
        .map(renderDocument)}</div>
    </SessionActionDialog> : null}

    {nameMode ? <SessionActionDialog title={nameMode === 'create' ? '新建知识库' : '编辑文档名称'} busy={savingName}
      returnFocusTo={nameTrigger} onClose={() => setNameMode(null)}>
      <form onSubmit={event => { event.preventDefault(); void saveName() }}>
        {nameError ? <p className="alert error" role="alert">{nameError}</p> : null}
        <label className="field"><span>{nameMode === 'create' ? '知识库名称' : '文档名称'}</span>
          <input required maxLength={nameMode === 'create' ? 80 : 255} value={name} disabled={savingName}
            onChange={event => setName(event.target.value)} /></label>
        {nameMode === 'create' ? <label className="field"><span>简介（选填）</span>
          <textarea rows={3} maxLength={300} value={description} disabled={savingName}
            onChange={event => setDescription(event.target.value)} /></label> : null}
        <div className="session-dialog-actions">
          <button type="button" className="ghost-button" disabled={savingName} onClick={() => setNameMode(null)}>取消</button>
          <button type="submit" className="primary-button" disabled={savingName || !name.trim()}>
            {savingName ? '正在保存…' : nameMode === 'create' ? '创建' : '保存'}
          </button>
        </div>
      </form>
    </SessionActionDialog> : null}

    {versionTarget !== null ? <SessionActionDialog title={versionTarget ? '上传新版本' : '上传文档'} busy={isUploading}
      returnFocusTo={uploadTrigger} onClose={() => { setVersionTarget(null); setErrorMessage('') }}>
      <div className="kb-upload">
        {visibleError ? <div className="alert error" role="alert">{visibleError}</div> : null}
        {uploadResult ? <div className="upload-result-card" role="status"><strong>{uploadResult.document_reused ? '已复用已有文档' : '文本已保存'}</strong>
          <span>{uploadResult.file_name || selectedFile?.name || '未命名文档'} · {uploadResult.chunk_count} 个片段</span>
          <DocumentIndexStatus index={uploadResult.vector_index} operationId={uploadResult.operation_id}
            uncertain={!uploadResult.state_confirmed || indexState.uncertain.includes(uploadResult.operation_id || '')}
            busy={indexState.busy.includes(uploadResult.operation_id || '')}
            onQuery={() => { if (uploadResult.operation_id) void indexController.query(uploadResult.operation_id) }}
            onExecute={action => { if (uploadResult.operation_id) void indexController.execute(uploadResult.operation_id, action) }} />
        </div> : <>
          <p className="kb-upload-target">{versionTarget ? targetGroup?.name || '文档已不可用'
            : knowledgeBases.find(item => item.knowledge_base_id === selectedKnowledgeBaseId)?.name}</p>
          <ScopeSelect label="文档分类" icon={<Layers3 size={17} aria-hidden="true" />} value={selectedCategoryId}
            placeholder="暂无可用分类" disabled={isUploading} onChange={setSelectedCategoryId}
            options={categories.map(category => ({ value: category.category_id, label: category.label, description: category.description }))} />
          <label className={'kb-file-picker' + (isUploading ? ' is-disabled' : '')}>
            <input ref={fileInput} className="kb-file-input" aria-label="选择文档文件" type="file" accept=".md,.txt" disabled={isUploading} onChange={handleFileChange} />
            <UploadCloud size={23} aria-hidden="true" /><strong>{selectedFile ? '更换文件' : '选择本地文件'}</strong><span>Markdown / TXT · 最大 2 MB</span>
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
            disabled={!canUpload || !selectedFile || !selectedCategory || Boolean(versionTarget && !targetGroup) || indexState.uploadUncertain}
            onClick={() => { void handleUploadDocument() }}>{isUploading ? '正在上传…' : '开始上传'}</button> : null}
        </div>
      </div>
    </SessionActionDialog> : null}
  </section>
}
