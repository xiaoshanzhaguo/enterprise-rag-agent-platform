/* HTTP 请求工具封装文件，是前端和 FastAPI 后端通信的公共工具。
*  作用：
*  - 统一管理后端 API 地址
*  - 统一拼接请求 URL
*  - 统一处理 GET / POST 请求
*  - 统一处理后端返回结果
*  - 统一抛出接口错误 ApiError
* */

// 配置后端 API 基础地址
// ?? 为 空值合并运算符，意思是：如果左边是 null 或 undefined，就使用右边的默认值。
// .replace(/\/$/, ''): 去掉地址最后的 /, 避免后面拼接路径时出现双斜杠。
export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000').replace(
  /\/$/,
  '',
)

// 自定义接口错误类
// ApiError 是一种专门表示接口请求失败的错误。
export class ApiError extends Error {
  status: number // 保存 HTTP 状态码
  payload: unknown // 保存后端返回的错误内容。后端错误返回的结构不一定固定，有时候是对象，有时候是字符串，所以先用 unknown 表示。

  // 构造函数。message：错误提示文字。
  constructor(message: string, status: number, payload: unknown) {
    // 调用父类 Error 的构造函数。作用：把错误消息交给原生 Error 处理，使得后面能拿到 error.message。
    super(message)
    this.name = 'ApiError' // 错误名字。普通错误的名字一般是 Error，这里改成 ApiError，方便调试和区分错误来源。
    this.status = status   // 把传进来的 HTTP 状态码保存到当前错误对象上
    this.payload = payload // 把后端返回的错误内容保存到当前错误对象上
  }
}

// 拼接完整 API 地址。path: 接口路径。函数作用：把基础地址 API_BASE_URL 和接口路径 path 拼成完整 URL。
function buildApiUrl(path: string) {
  // 规范化路径
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  // 返回完整 API 地址
  return `${API_BASE_URL}${normalizedPath}`
}

// 统一解析响应。作用：统一解析 fetch 返回的 response，并处理错误。
// <TResponse>：TypeScript 泛型，表示：调用这个函数时，可以告诉 TypeScript：我期望返回的数据是什么类型。
// response: Response，表示：参数 response 的类型是浏览器 fetch 返回的 Response 对象。
// Promise<TResponse>: 表示这个异步函数最终会返回一个 TResponse 类型的数据，而async 函数返回值天然会包在 Promise 里。
async function parseResponse<TResponse>(response: Response): Promise<TResponse> {
  // 判断响应内容类型
  const contentType = response.headers.get('content-type') ?? ''
  // 根据内容类型解析响应体。这一行是在读取返回内容，逻辑：如果返回的是 JSON，就用 response.json() 解析；否则就用 response.text() 解析成普通文本。
  // response.json()：把 JSON 响应内容解析成 JavaScript 对象；response.text()： 把响应内容解析成字符串
  const payload = contentType.includes('application/json') ? await response.json() : await response.text()

  // 判断请求是否失败。response.ok 是浏览器 Response 对象自带的属性。当状态码为：200-299时，response.ok 为 true。
  if (!response.ok) {
    // 抛出自定义错误
    throw new ApiError(`请求失败：${response.status}`, response.status, payload)
  }

  // 如果请求成功，就返回解析后的数据
  // as TResponse 是 TypeScript 类型断言，意思是告诉 TypeScript：我认为 payload 就是调用方期望的 TResponse 类型。
  return payload as TResponse
}

// GET 请求封装
// 定义并导出一个 GET 请求函数。这是一个异步函数，最终返回接口数据。
export async function apiGet<TResponse>(path: string): Promise<TResponse> {
  // 发送 HTTP 请求
  // fetch: 浏览器内置的网络请求函数，它会向后端发送请求。fetch 默认是 GET 请求。
  // await: 表示等待请求完成
  const response = await fetch(buildApiUrl(path), {
    headers: {
      Accept: 'application/json', // 告诉后端，我希望你返回 JSON 格式的数据。Accept 是 HTTP 请求头。
    },
  })

  // 把 response 交给 前面写的 parseResponse 统一处理
  // 它会做三件事：
  // 解析 JSON 或文本
  // 判断 response.ok
  // 成功就返回数据，失败就抛出 ApiError
  return parseResponse<TResponse>(response)
}

// POST 请求封装。
// 定义并导出一个 POST 请求函数 apiPost，它有两个泛型，分别对应 TRequest-请求体 body 的类型，TResponse-后端返回数据的类型
export async function apiPost<TRequest, TResponse>(path: string, body: TRequest): Promise<TResponse> {
  // 发送请求
  // POST 一般用于：提交数据、创建资源、上传内容、发送聊天问题、提交文档
  const response = await fetch(buildApiUrl(path), {
    method: 'POST',
    headers: { // 配置请求头
      Accept: 'application/json', // 告诉后端：我希望你返回 JSON
      'Content-Type': 'application/json', // 告诉后端：我发送给你的请求体是 JSON 格式。这行对 POST 很重要，如果没有它，后端可能不知道怎么解析请求体。
    },
    body: JSON.stringify(body), // 把请求体转换为 JSON 字符串
  })

  return parseResponse<TResponse>(response)
}
