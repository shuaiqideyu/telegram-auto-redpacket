/**
 * 按 id 把新数据原地合并进响应式数组（用于轮询刷新）：
 * - 已存在项：仅更新发生变化的字段，保留原对象引用 → Vue 最小 diff，
 *   未变的 `avatar_url` 等字段不重新赋值，el-avatar 不会重新拉图。
 * - 新增项：追加。
 * - 已消失项：移除。
 * - 末尾按 incoming 顺序重排，保证排序与后端一致。
 *
 * 相比 `list.value = incoming` 整体替换，避免整张表/全部头像每次轮询重渲染。
 */
export function mergeById<T extends { id: number | string }>(
  target: T[],
  incoming: T[],
): void {
  const incomingIds = new Set(incoming.map((x) => x.id))

  // 1) 删除已不存在的项
  for (let i = target.length - 1; i >= 0; i--) {
    if (!incomingIds.has(target[i].id)) target.splice(i, 1)
  }

  // 2) 更新已有项的变化字段；新增项追加
  const indexById = new Map(target.map((x, i) => [x.id, i]))
  for (const item of incoming) {
    const idx = indexById.get(item.id)
    if (idx === undefined) {
      target.push(item)
    } else {
      const existing = target[idx]
      for (const k in item) {
        if (existing[k] !== item[k]) existing[k] = item[k]
      }
    }
  }

  // 3) 按 incoming 顺序重排（处理置顶/人数变化导致的顺序变动）
  const orderById = new Map(incoming.map((x, i) => [x.id, i]))
  target.sort((a, b) => (orderById.get(a.id) ?? 0) - (orderById.get(b.id) ?? 0))
}
