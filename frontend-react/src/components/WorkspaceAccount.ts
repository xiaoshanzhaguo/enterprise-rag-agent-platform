import { createContext, useContext } from 'react'
import type { WorkspaceUser } from '../api/workspaceApi'

export const WorkspaceAccount = createContext<WorkspaceUser | null>(null)
export const WorkspaceSignOut = createContext<(() => void) | null>(null)
export const WorkspaceAccountUpdate = createContext<(account: WorkspaceUser) => void>(() => {})

export function useWorkspaceAccount() {
  const account = useContext(WorkspaceAccount)
  if (!account) throw new Error('Workspace account is required')
  return account
}
