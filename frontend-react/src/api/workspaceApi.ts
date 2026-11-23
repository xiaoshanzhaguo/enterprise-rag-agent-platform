import { apiDelete, apiGet, apiPatch, apiPost } from './httpClient'

export const workspaceModules = [
  { id: 'chat', label: '智能问答', path: '/chat' },
  { id: 'workflow', label: '发起申请', path: '/tickets/new' },
  { id: 'tickets', label: '工单中心', path: '/tickets' },
  { id: 'knowledge', label: '知识库管理', path: '/admin/knowledge' },
  { id: 'logs', label: '执行日志', path: '/logs' },
  { id: 'evaluation', label: '评测结果', path: '/eval' },
] as const
export type WorkspaceModule = typeof workspaceModules[number]['id']

export const workspaceNavigation = workspaceModules.filter(module => module.id !== 'workflow')

export function canOpenModule(permissions: readonly WorkspaceModule[], module: WorkspaceModule) {
  return permissions.includes(module) || (module === 'tickets' && permissions.includes('workflow'))
}

export interface WorkspaceUser {
  id: number
  username: string
  display_name: string
  role: 'admin' | 'member'
  enabled: boolean
  department: string | null
  module_permissions: WorkspaceModule[]
}

export interface ManagedWorkspaceUser extends WorkspaceUser {
  work_email: string | null
}

type UserProfile = { username: string; display_name: string; role: WorkspaceUser['role']; module_permissions: WorkspaceModule[]; work_email?: string | null }

interface DepartmentIdentity { id: number; name: string }
export interface WorkspaceDepartment extends DepartmentIdentity { user_count: number; rule_count: number }

export const listDepartments = () => apiGet<WorkspaceDepartment[]>('/workspace/departments')
export const createDepartment = (name: string) => apiPost<{ name: string }, DepartmentIdentity>(
  '/workspace/departments', { name }, AbortSignal.timeout(15000))
export const renameDepartment = (id: number, name: string) => apiPatch<DepartmentIdentity>(`/workspace/departments/${id}`, { name })
export const deleteDepartment = (id: number) => apiDelete<{ deleted: boolean }>(`/workspace/departments/${id}`)

export const getWorkspaceUser = () => apiGet<WorkspaceUser>('/workspace/me')
export const login = (username: string, password: string) =>
  apiPost<{ username: string; password: string }, WorkspaceUser>('/workspace/login', { username, password }, AbortSignal.timeout(15000))
export const logout = () => apiPost('/workspace/logout', {}, AbortSignal.timeout(15000))
export const listUsers = () => apiGet<ManagedWorkspaceUser[]>('/workspace/users')
export const createUser = (data: UserProfile & { password: string; department_id: number }) =>
  apiPost<typeof data, ManagedWorkspaceUser>('/workspace/users', data, AbortSignal.timeout(15000))
export const updateUser = (id: number, data: UserProfile & { department_id: number | null }) =>
  apiPatch<ManagedWorkspaceUser>(`/workspace/users/${id}`, data)
export const setUserEnabled = (id: number, enabled: boolean) =>
  apiPost<{ enabled: boolean }, ManagedWorkspaceUser>(`/workspace/users/${id}/status`, { enabled }, AbortSignal.timeout(15000))
