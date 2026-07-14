import { onActivated, onDeactivated, onMounted, onUnmounted } from "vue"

/**
 * 轻量轮询：
 * - 页面切到后台（标签页隐藏）时自动暂停，回到前台立即刷新一次再继续。
 * - 兼容 <keep-alive>：被缓存页 deactivated 时暂停，重新 activated 时恢复并刷新。
 * - onUnmounted 清理定时器与监听。
 *
 * 这样多个页面间切换不会有多个定时器在后台空转，避免无谓的整表重渲染与请求。
 */
export function usePolling(
  fn: () => void | Promise<void>,
  intervalMs: number,
  immediate = true,
) {
  let timer: ReturnType<typeof setInterval> | null = null
  let visibleHandler: (() => void) | null = null
  let isActive = true

  const run = () => {
    if (!document.hidden && isActive) void fn()
  }
  const start = () => {
    if (timer === null) timer = setInterval(run, intervalMs)
  }
  const stop = () => {
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
  }

  onMounted(() => {
    isActive = true
    if (immediate) void fn()
    start()
    visibleHandler = () => {
      if (document.hidden) {
        stop()
      } else if (isActive) {
        void fn()
        start()
      }
    }
    document.addEventListener("visibilitychange", visibleHandler)
  })

  // keep-alive 重新激活（非首次挂载）：恢复轮询并刷新一次
  onActivated(() => {
    isActive = true
    if (timer === null && !document.hidden) {
      void fn()
      start()
    }
  })

  onDeactivated(() => {
    isActive = false
    stop()
  })

  onUnmounted(() => {
    stop()
    if (visibleHandler) {
      document.removeEventListener("visibilitychange", visibleHandler)
      visibleHandler = null
    }
  })

  return { start, stop }
}
