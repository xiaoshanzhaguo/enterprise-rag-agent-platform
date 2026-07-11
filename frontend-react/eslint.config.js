/*
 * ESLint 配置文件。
 * 它的作用是：告诉 ESLint：这个 React + TypeScript 项目要检查哪些文件、使用哪些规则、忽略哪些目录、代码运行环境是什么。
 * 可以记为：ESLint 是代码检查工具，eslint.config.js 就是告诉 ESLint 按什么规则检查代码。
 *
 * 功能说明：
 * 1. 统一检查 React + TypeScript 代码风格和常见错误。
 * 2. 忽略 dist 构建产物，避免检查生成文件。
 * 3. 启用 React Hooks 和 Vite React Refresh 相关规则。
 */

// JavaScript 官方推荐规则。
// 这里的 js 可以理解为：ESLint 提供的 JavaScript 基础规则集合。更准备地说，它是 ESLint 官方提供的 JavaScript 推荐配置。
import js from '@eslint/js'

// 浏览器全局变量，例如 window、document。
// 导入 globals 包，它的作用是提供不同运行环境里的全局变量。
import globals from 'globals'

// React Hooks 规则，例如检查 useEffect 依赖。
// 导入 React Hooks 的 ESLint 插件，它主要检查 React Hook 的使用规则。比如：useState、useEffect、useMemo、useCallback 等 Hook 是否正确使用。
import reactHooks from 'eslint-plugin-react-hooks'

// React Refresh 规则，用于开发时热更新更稳定。
// 导入 React Refresh 插件，它是开发时的热更新能力。简单理解为：你修改 React 组件代码后，页面可以快速更新，并尽量保留组件状态。
import reactRefresh from 'eslint-plugin-react-refresh'

// TypeScript ESLint 推荐规则。
// 导入 TypeScript ESLint 配置，普通 ESLint 主要检查 JavaScript，但我们的项目是 React + TypeScript，里面有 .ts 和 .tsx 文件，所以需要 TypeScript ESLint 来检查 TypeScript 相关代码。
import tseslint from 'typescript-eslint'

// defineConfig 用于获得配置类型提示；globalIgnores 用于声明全局忽略文件。
// defineConfig 用于定义 ESLint 配置，好处：1. 提供类型提示；2. 让配置结构更清晰；3. 编辑器能更好地自动补全。
// globalIgnores 用于配置全局忽略文件或目录，比如：globalIgnores(['dist'])，意思是不要检查 dist 目录。因为 dist 是构建产物，不是手写的源码。
import { defineConfig, globalIgnores } from 'eslint/config'

// 导出 ESLint 配置。
// ESLint 执行时会读取这个默认导出的配置。比如运行 npm run lint 时，ESLint 会读取这个文件，然后按这里的规则检查代码。
// 注意 defineConfig 里面是一个数组，这是 ESLint 新版常见的 flat config 写法，可以理解成：ESLint 配置由多段规则组成，数组里的每一项都是一段配置。
export default defineConfig([
  // dist 是构建输出目录，不需要做代码检查。
  globalIgnores(['dist']),

  // 针对 TypeScript 和 TSX 文件启用下面这些规则。
  // 这里开始定义一段配置对象，这段配置主要针对：.ts 文件、.tsx 文件，也就是项目中的 TypeScript 和 React 组件文件。
  {
    // 匹配所有 .ts 和 .tsx 文件。
    // 这一行指定这段规则应用在哪些文件上。
    // 拆开看：**: 任意层级目录； /: 目录分隔符；*: 任意文件名；.{ts, tsx}: 后缀为 .ts 或 .tsx。
    // 因此它能匹配 src/main.tsx、src/pages/ChatPage.tsx 等文件。
    files: ['**/*.{ts,tsx}'],

    // 继承多套推荐规则。
    // 可以理解为：不用自己一条条写规则，直接使用别人整理好的推荐规则。
    extends: [
      // 启用 ESLint 官方推荐的 JavaScript 基础规则。它主要检查 JavaScript 代码中的常见问题。比如：未定义变量、重复声明、无法执行的代码、不合理的语法。
      js.configs.recommended,
      // 启用 TypeScript ESLint 推荐规则。它主要检查 .ts 和 .tsx 文件里的 TypeScript 代码问题。比如：类型相关问题、未使用变量、一些不推荐的 TypeScript 写法。
      tseslint.configs.recommended,
      // 启用 React Hooks 推荐规则。这里的重点是检查 Hook 使用是否符合规则。
      // React Hooks 有两个非常重要的规则：
      // 1. Hook 只能在组件顶层调用，不能写在 if、for、普通函数内部。
      // 2. useEffect、useMemo、useCallback 等 Hook 的依赖数组要正确。
      reactHooks.configs.flat.recommended,
      // 启用合适的 Vite 的 React Refresh 规则。它主要是为了开发时热更新更稳定。
      // 比如你修改组件代码时，Vite 可以快速更新页面，而不是整个页面完全刷新。这个规则会提醒你避免某些影响 Fast Refresh 的导出写法。
      reactRefresh.configs.vite,
    ],

    // 设置代码运行环境里的全局变量。
    // languageOptions 用来配置语言相关选项。比如：代码运行环境有哪些全局变量、解析方式、ECMAScript 版本、模块类型。
    languageOptions: {
      // 当前项目运行在浏览器里，所以启用浏览器全局变量。
      // 所以这些变量是合法的：window, document, localStorage, console, setTimeout, fetch。
      // 如果不配置，ESLint 可能会认为：window 是未定义变量。配置 globals: globals.browser 后，ESLint 就知道：window 和 document 是浏览器自带的全局变量，不需要你手动定义。
      globals: globals.browser,
    },
  },
])
