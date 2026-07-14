import path from "path"
import vue from "@vitejs/plugin-vue"
import AutoImport from "unplugin-auto-import/vite"
import Components from "unplugin-vue-components/vite"
import { ElementPlusResolver } from "unplugin-vue-components/resolvers"
import { createLogger, defineConfig } from "vite"

// 后端启动/重启窗口期，/api 代理会对每个轮询请求刷一段 ETIMEDOUT 堆栈。
// 失败本身前端 UI 已有提示，这里把代理错误压成 10s 一条的单行提醒。
const logger = createLogger()
const originalError = logger.error.bind(logger)
let lastProxyErrorAt = 0
logger.error = (msg, options) => {
  if (typeof msg === "string" && msg.includes("http proxy error")) {
    const now = Date.now()
    if (now - lastProxyErrorAt > 10_000) {
      lastProxyErrorAt = now
      logger.warn("[proxy] 后端 :8000 未就绪，/api 请求暂时失败（10s 内不重复提示）")
    }
    return
  }
  originalError(msg, options)
}

// https://vite.dev/config/
export default defineConfig({
  customLogger: logger,
  plugins: [
    vue(),
    // Element Plus 按需自动导入：模板里的 <el-*> 组件 + 程序式 API（ElMessage 等）
    // 仅引入用到的组件与样式，避免整库 + 全量 CSS 拖慢首屏。
    AutoImport({
      resolvers: [ElementPlusResolver({ importStyle: "css" })],
      dts: "src/types/auto-imports.d.ts",
    }),
    // dts:false —— 不生成 GlobalComponents 类型声明，保持模板对 el-* 的宽松类型
    // （与改造前一致，避免 strictTemplates 误报）；按需导入/样式注入仍照常工作。
    Components({
      resolvers: [ElementPlusResolver({ importStyle: "css" })],
      dts: false,
    }),
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  // 不手动 manualChunks：强制把 element-plus/es barrel 归到单 chunk 会带入
  // 默认导出的整库 installer，反而破坏 tree-shaking。交给 Rollup 自动按需拆分
  // （配合 unplugin 按需导入 + 路由懒加载，已是最优）。
  server: {
    port: 5173,
    proxy: {
      // 开发时把 /api 代理到 FastAPI 后端
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
})
