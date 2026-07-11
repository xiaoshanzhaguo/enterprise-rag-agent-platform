/*
 * Vite 项目的开发和构建配置文件。可以理解为：这个文件是在告诉 Vite: 这个 React 项目怎么启动、怎么编译、开发时怎么把前端请求转发给 FastAPI 后端。
 *
 * 功能说明：
 * 1. 启用 React 插件，让 Vite 能编译 React + TypeScript。
 * 2. 开发环境把 /api 代理到 FastAPI，避免浏览器跨域问题。
 * 3. 生产部署时可以通过环境变量把接口地址换成真实后端地址。
 */

// defineConfig 用于获得 Vite 配置的类型提示。可以记作：defineConfig = 帮你更规范地写 Vite 配置。
// defineConfig 的作用是帮助你写 Vite 配置，它本质上不是必须的，但用了之后有好处：
// 1. TypeScript 会给你配置项提示。
// 2. 写错配置字段时更容易发现。
// 3. 编辑器里会有自动补全。
import { defineConfig } from 'vite'

// React 插件负责处理 JSX、Fast Refresh 等 React 开发能力。
// 导入 Vite 的 React 插件 react，这个插件主要负责：
// 1. 让 Vite 能识别 JSX / TSX。
// 2. 支持 React Fast Refresh。
// 3. 支持 React 项目的开发编译。
// Fast Refresh 可以理解为：你修改 React 代码后，页面快速更新，并尽量保留当前组件状态。
import react from '@vitejs/plugin-react'

// 导出 Vite 配置对象。
// export default 表示默认导出。Vite 启动后，会自动读取这个文件里的默认导出配置。
// 也就是说，当你执行：npm run dev 或者 npm run build 时， Vite 会读取这里的配置。
export default defineConfig({
  // plugins 表示 Vite 构建过程中要启用哪些插件。
  // 这里是 react()，而不是 react。因为 react 是一个函数，需要调用后返回真正的插件配置。
  // 可以理解为：react 是插件工厂函数，react() 才是生成出来的 React 插件。
  // 这行的作用：让 Vite 能正常处理 React + TypeScript + JSX。
  plugins: [react()],

  // server 只影响本地 npm run dev，不影响生产构建 (npm run build)。
  // 所以这里配置的代理，只是在本地调试时生效。可以记为：server = 本地开发服务器配置。
  server: {
    // proxy 用于把前端请求转发给后端。
    // 它的作用是，当前端请求某个路径时，不直接由前端处理，而是转发给后端。
    // 比如前端请求 /api/health，Vite 开发服务器会帮你转发到：http://127.0.0.1:8000/health，这样可以避免浏览器跨域问题。
    proxy: {
      // 配置 /api 代理规则。这里定义了一条代理规则，意思是：只要前端请求路径以 /api 开头，就走这条代理规则。比如 /api/health 等，这些都会被 Vite 代理到 FastAPI 后端。
      // 开发环境用 /api 代理 FastAPI，避免浏览器因为跨域拦截本地调试请求。
      // 生产环境可以把 VITE_API_BASE_URL 改成真实后端地址。
      '/api': {
        // FastAPI 本地默认地址。
        // target 表示代理目标地址。
        target: 'http://127.0.0.1:8000',

        // 把请求来源改成目标地址，减少后端校验来源时的问题。
        // changeOrigin: true 的意思是：转发请求时，把请求头里的 Origin 改成目标服务器地址。可以通俗理解成：让后端觉得这个请求更像是直接发给它的，而不是从前端开发服务器转发来的。
        changeOrigin: true,

        // 发送给 FastAPI 前去掉 /api 前缀，例如 /api/health -> /health。
        // 这一行的作用：在请求转发给 FastAPI 前，把路径开头的 /api 去掉。
        // /^\/api/ 的意思是：匹配路径开头的 /api。
        // 完整流程如下：
        // 1. 前端请求：/api/health
        // 2. Vite 代理匹配：/api
        // 3. rewrite 去掉 /api：/health
        // 4. 转发到 FastAPI：http://127.0.0.1:8000/health
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
