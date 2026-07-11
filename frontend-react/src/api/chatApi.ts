/*
 * 聊天接口辅助工具。
 *
 * 功能说明：
 * 1. 保存 Agent 流式问答接口地址，避免页面里硬编码 /agent_stream。
 * 2. 先保留请求体整理函数，后续接 SSE 时可以直接复用。
 */

// AgentStreamRequest 是调用 /agent_stream 时的请求体类型。
// 这句话的意思：我只是为了类型检查导入它，编译成 JavaScript 后可以删掉这行。
// import type的好处：它可以明确告诉 TypeScript 和打包工具：这个导入只用于类型，不要把它当成运行时代码处理。
import type { AgentStreamRequest } from '../types/chat'

// API_BASE_URL 来自统一 HTTP 配置，开发环境一般是 /api。
import { API_BASE_URL } from './httpClient'

// 后端 Agent 流式接口路径。
export const AGENT_STREAM_ENDPOINT = '/agent_stream'

// 拼出完整的 Agent 流式接口地址。
export function buildAgentStreamUrl() {
  // 例如开发环境会得到 /api/agent_stream。
  return `${API_BASE_URL}${AGENT_STREAM_ENDPOINT}`
}

// 整理 Agent 请求体，暂时直接返回原对象。
export function buildAgentStreamRequest(request: AgentStreamRequest) {
  // 这里先只做请求体整理，真正的 SSE 读取会放到后续任务中接入。
  return request
}
