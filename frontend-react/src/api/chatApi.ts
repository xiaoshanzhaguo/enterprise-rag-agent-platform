/*
 * 聊天接口辅助工具。
 *
 * 功能说明：
 * 1. 保存 Agent 流式问答接口地址，避免页面里硬编码 /agent_stream。
 * 2. 先保留请求体整理函数，后续接 SSE 时可以直接复用。
 * 3. 当前已经使用 fetch + ReadableStream 解析后端 POST SSE 响应。
 */

// AgentStreamRequest 是调用 /agent_stream 时的请求体类型。
// 这句话的意思：我只是为了类型检查导入它，编译成 JavaScript 后可以删掉这行。
// import type的好处：它可以明确告诉 TypeScript 和打包工具：这个导入只用于类型，不要把它当成运行时代码处理。
import type { AgentStreamEvent, AgentStreamRequest } from '../types/chat'

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
  // 当前 SSE 读取已经由 streamAgentAnswer 负责，这个函数继续作为统一整理请求体的入口。
  return request
}

// 从一段 SSE 文本里解析出后端 StreamEvent。解析 Agent 流式事件。
// 这个函数的作用是：把一段 SSE 原始文本 eventText 解析成前端能用的 AgentStreamEvent 对象。如果这段文本不是合法业务事件，就返回 null。
// 也就是：SSE 文本 -> 提取 data: 后面的 JSON 字符串 -> JSON.parse 转成对象 -> 返回 AgentStreamEvent
function parseAgentStreamEvent(eventText: string): AgentStreamEvent | null {
  // SSE 事件可能由多行组成，这里只取 data: 开头的数据行。
  const dataLines = eventText
    .split('\n')  // 按照换行符 \n，把一整段文本拆成多行。
    .map((line) => line.trim())  // 遍历每一行文本，并去掉每一行前后的空格。
    .filter((line) => line.startsWith('data: ')) // 筛选数组，只保留以 'data: ' 开头的行。

  // 没有 data 行说明不是业务事件，直接跳过。
  if (!dataLines.length) {
    return null
  }

  // 后端当前每条事件只有一行 data；这里 join 是为了兼容未来多行 data。
  // 把 data: 后面的内容取出来，拼成真正的 JSON 字符串。
  // slice(6) 表示从字符串下标 6 开始截取，一直截取到最后。
  // join('\n) 表示把多个 data 行重新用换行符拼起来。
  const jsonText = dataLines.map((line) => line.slice(6)).join('\n')

  try {
    // JSON.parse 可能失败，所以用 try/catch 防止单条坏事件打断整个流。
    // JSON.parse 将 JSON 字符串转换成 JavaScript 对象。
    // as AgentStreamEvent 是 TypeScript 的类型断言。意思是：告诉 TypeScript：我认为这个对象符合 AgentStreamEvent 类型。
    // 因此，整行的意思：把 jsonText 解析成对象，并把它当成 AgentStreamEvent 返回。
    return JSON.parse(jsonText) as AgentStreamEvent
  } catch {
    // 解析失败时返回 null，这样做的好处：单条坏事件不会影响整个 SSE 流。
    // 前端可以跳过这条坏事件，继续处理后面的事件。
    return null
  }
}

// 使用 fetch + ReadableStream 调用后端 POST SSE，并把事件逐条交给页面处理。函数名可以理解为：流式获取 Agent 回答。
// 这段代码的整体作用是：
// 用 fetch 向后端 /agent_stream 发送 POST 请求；
// 后端不是一次性返回 JSON，而是不断返回 SSE 流式数据；
// 前端一边读取流，一边把每条 SSE 事件解析成 AgentStreamEvent；
// 然后通过 onEvent(event) 交给页面处理。
// 可以理解成：用户提问 -> 前端 POST / agent_stream -> 后端持续返回 text/event-stream -> 前端 ReadableStream 一块一块读取 -> 拼接 buffer -> 按 \n\n 拆成一条条 SSE 事件 -> parseAgentStreamEvent 解析 -> onEvent(event) 更新页面
export async function streamAgentAnswer(
  request: AgentStreamRequest,  // request 是调用后端 /agent_stream 接口时要发送的请求体。request 保存用户问题、会话 ID、知识库范围、是否启用 RAG 等信息。
  onEvent: (event: AgentStreamEvent) => void, // 意思是这个函数只负责处理事件，不需要返回结果。通俗理解：每当后端推送一条 SSE 事件，streamAgentAnswer 就调用一次 onEvent(event)。
) {
  // fetch 发送 POST 请求；后端返回 text/event-stream，不是普通 JSON。
  // response 不是最终回答文本，而是 HTTP 响应对象，里面包含：状态码、响应头、响应体 body。
  const response = await fetch(buildAgentStreamUrl(), {
    method: 'POST', // 因为这次不是简单获取数据，而是要把用户问题、会话ID、知识库参数提交给后端。
    headers: {      // 设置请求头。请求头用来告诉后端：前端希望接收什么格式的数据；前端发送的请求体是什么格式。
      Accept: 'text/event-stream',  // 告诉后端：我希望你返回 SSE 流式响应。text/event-stream 是 SSE 的标准响应类型。
      'Content-Type': 'application/json',  // 告诉后端：我发送给你的请求体是 JSON 格式。
    },
    // buildAgentStreamRequest(request) 把前端页面里的 request 整理成后端真正需要的请求结构。
    // JSON.stringify(...) 把 JavaScript 对象转成 JSON 字符串。
    body: JSON.stringify(buildAgentStreamRequest(request)),
  })

  // HTTP 状态码不是 2xx 时，先抛出错误，让页面显示失败。
  if (!response.ok) {
    // throw new Error(...) 会中断当前函数执行，把错误交给调用方处理。
    // 模板字符串：`流式问答请求失败：${response.status}` 会把状态码拼进去。
    // 比如状态码是 500，错误信息就是：流式问答请求失败：500
    // 页面层可以用 try/catch 捕获这个错误，然后展示：请求失败，请稍后重试
    throw new Error(`流式问答请求失败：${response.status}`)
  }

  // 浏览器不支持 ReadableStream 或响应体为空时，无法继续读取流。
  // 这一行判断后端响应里有没有可读取的流。对于流式响应，前端需要从 response.body 里不断读取数据。
  // response.body 的类型通常是：ReadableStream<Uint8Array> | null。也就是说，它可能是一个可读流，也可能是 null。如果是 null，说明浏览器没有给我们可读取的响应体。
  if (!response.body) {
    // 如果没有响应体，就抛出错误。因为没有 response.body，后面就无法执行 response.body.getReader()，所以必须提前拦住。
    throw new Error('浏览器没有返回可读取的流式响应。')
  }

  // reader 用来逐块读取后端返回的数据。
  // 从响应体里拿到一个 reader。reader 的作用是：一块一块读取后端返回的流式数据。
  const reader = response.body.getReader()

  // TextDecoder 把 Uint8Array 字节流转换成字符串。
  // 这一行创建一个文本解码器。因为 reader.read() 读出来的不是字符串，而是字节数据。读出来的 value 类型通常是：Unit8Array，类似一组二进制字节。
  // 浏览器不能直接把它当成中文文本用。所以要用：TextDecoder 把字节流转换成字符串。
  const decoder = new TextDecoder('utf-8')

  // buffer 保存尚未组成完整 SSE 事件的半截文本。
  // 为什么需要 buffer? 因为网络传输是分块的，后端一次推送的一条 SSE 事件，不一定会被浏览器完整读到。
  // buffer 的作用：先保存不完整的文本；等下一块数据来了再拼上；拼成完整 SSE 事件后再解析。
  let buffer = ''

  // 开始循环读取流。因为后端 SSE 是持续推送的，我们不知道它会推送多少块数据。所以用 while(true) 一直读，直到后面发现 done = true，才 break 退出循环。
  while (true) {
    // done 为 true 表示后端流式响应已经结束。
    // 这一行读取下一块流式数据。
    // const { value, done } 使用了解构赋值。value: 当前读到的数据块，通常是 Unit8Array，done: 是否已经读取结束。
    const { value, done } = await reader.read()

    // 判断流是否结束。如果 done 是 true，说明后端已经不再发送数据。退出 while 循环，即：流结束了，不再继续 reader.read()
    if (done) {
      break
    }

    // stream: true 表示这是连续流的一部分，避免中文被切块时乱码。不加容易导致中文乱码。
    // 把字节块解码成字符串并追加到 buffer。
    // decoder.decode(value, { stream: true })： 把当前读到的字节数据 value 转成字符串。
    // buffer += ... 把这次解码出来的字符串加到 buffer 后面。
    buffer += decoder.decode(value, { stream: true })

    // SSE 事件之间用空行分隔，也就是两个换行。
    const eventTexts = buffer.split('\n\n')

    // 最后一段可能是不完整事件，先放回 buffer，等待下一块数据补齐。
    // eventTexts.pop() 会取出数组最后一个元素，并把它从数组里删除。
    // 为什么要取最后一个？因为 buffer.split('\n\n') 拆出来后，最后一段可能是不完整的。
    buffer = eventTexts.pop() ?? ''

    // 逐条解析已经完整的 SSE 事件。
    for (const eventText of eventTexts) {
      // 把一段 SSE 文本解析成：AgentStreamEvent。
      // 如果解析失败，会返回 null，因此 event 的类型是：AgentStreamEvent | null。
      const event = parseAgentStreamEvent(eventText)

      // 判断解析结果是否存在。如果 event 不是 null，说明解析成功。
      if (event) {
        // 调用回调函数，把解析好的事件交给页面处理。这就是前端页面能实时显示流式回答的关键。
        onEvent(event)
      }
    }
  }

  // 流结束后，如果 buffer 里还有完整数据，也尝试解析一次。
  // 流结束后，buffer 里可能还剩下一段数据。正常情况下，完整 SSE 事件应该以：\n\n 结尾。
  // 但有时候最后一条事件可能没有完整的两个换行，导致它一直留在 buffer 里。所以流结束后，再尝试解析一次 buffer。
  // 这行的意思是：尝试把剩下的尾部文本解析成 AgentStreamEvent。
  const tailEvent = parseAgentStreamEvent(buffer)

  // 判断尾部事件是否解析成功。如果 tailEvent 不是 null，说明最后的 buffer 里确实还有一条有效事件。
  if (tailEvent) {
    // 把最后这条事件也交给页面处理。这样可以避免丢掉最后一条消息。
    onEvent(tailEvent)
  }
}
