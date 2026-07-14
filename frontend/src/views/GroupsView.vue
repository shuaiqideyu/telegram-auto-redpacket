<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue"
import {
  Refresh,
  ArrowDown,
  CircleClose,
  Delete,
  Search,
  Top,
} from "@element-plus/icons-vue"

import { api } from "@/api"
import { usePolling } from "@/composables/usePolling"
import { mergeById } from "@/lib/merge"
import type { Account, MonitoredGroup } from "@/types"

const groups = ref<MonitoredGroup[]>([])
const accounts = ref<Account[]>([])
const loading = ref(true)
const scanning = ref(false)
const busy = ref<number | null>(null)
const batchBusy = ref(false)
const keyword = ref("")
const activeTab = ref("all")
const typeFilter = ref<"all" | "group" | "channel">("all")
const page = ref(1)
const pageSize = ref(20)

// 账号 tab：仅显示有窗口的账号，带各自窗口数
const accountTabs = computed(() => {
  const counts = new Map<number, number>()
  for (const g of groups.value) {
    for (const aid of g.source_account_ids) {
      counts.set(aid, (counts.get(aid) || 0) + 1)
    }
  }
  return accounts.value
    .filter((a) => counts.has(a.id))
    .map((a) => ({
      id: a.id,
      name: a.name || a.username || `账号 ${a.id}`,
      count: counts.get(a.id) || 0,
    }))
})

const filteredGroups = computed(() => {
  let list = groups.value
  if (activeTab.value !== "all") {
    const aid = Number(activeTab.value)
    list = list.filter((g) => g.source_account_ids.includes(aid))
  }
  if (typeFilter.value !== "all") {
    list = list.filter((g) => g.chat_type === typeFilter.value)
  }
  const kw = keyword.value.trim().toLowerCase()
  if (kw) {
    list = list.filter(
      (g) =>
        (g.title || "").toLowerCase().includes(kw) ||
        (g.username || "").toLowerCase().includes(kw) ||
        String(g.chat_id).includes(kw)
    )
  }
  return list
})

const groupCount = computed(() => groups.value.filter((g) => g.chat_type === "group").length)
const channelCount = computed(() => groups.value.filter((g) => g.chat_type === "channel").length)

const total = computed(() => filteredGroups.value.length)
const enabledCount = computed(() => groups.value.filter((g) => g.enabled).length)
const pagedGroups = computed(() => {
  const start = (page.value - 1) * pageSize.value
  return filteredGroups.value.slice(start, start + pageSize.value)
})

watch([keyword, activeTab, typeFilter], () => {
  page.value = 1
})

// 选中的账号 tab 没窗口了（账号停了）→ 回到全部
watch(accountTabs, (tabs) => {
  if (activeTab.value !== "all" && !tabs.some((t) => String(t.id) === activeTab.value)) {
    activeTab.value = "all"
  }
})

async function load() {
  try {
    const [g, a] = await Promise.all([api.listGroups(), api.listAccounts()])
    groups.value = g
    accounts.value = a
  } catch (e) {
    ElMessage.error("加载群组失败：" + (e as Error).message)
  } finally {
    loading.value = false
  }
}

async function silentRefresh() {
  if (busy.value !== null || batchBusy.value || scanning.value) return
  try {
    const [g, a] = await Promise.all([api.listGroups(), api.listAccounts()])
    // 原地合并：未变的行（含 avatar_url）保持引用，头像不重新拉取
    mergeById(groups.value, g)
    mergeById(accounts.value, a)
  } catch {
    /* ignore */
  }
}

async function scan() {
  scanning.value = true
  try {
    groups.value = await api.scanGroups()
    ElMessage.success(`已扫描汇总 ${groups.value.length} 个群组/频道`)
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    scanning.value = false
  }
}

async function onToggle(g: MonitoredGroup, val: boolean) {
  busy.value = g.id
  try {
    const updated = await api.toggleGroup(g.id, val)
    groups.value = groups.value.map((x) => (x.id === g.id ? updated : x))
  } catch (e) {
    ElMessage.error("操作失败：" + (e as Error).message)
  } finally {
    busy.value = null
  }
}

async function onPin(g: MonitoredGroup) {
  busy.value = g.id
  try {
    await api.pinGroup(g.id, !g.pinned)
    await load()
    ElMessage.success(g.pinned ? "已取消置顶" : "已置顶")
  } catch (e) {
    ElMessage.error("操作失败：" + (e as Error).message)
  } finally {
    busy.value = null
  }
}

async function batchToggle(enabled: boolean) {
  batchBusy.value = true
  try {
    await api.batchToggleGroups(enabled)
    await load()
    ElMessage.success(enabled ? "已全部开启秒包" : "已全部关闭秒包")
  } catch (e) {
    ElMessage.error("操作失败：" + (e as Error).message)
  } finally {
    batchBusy.value = false
  }
}

async function onDelete(g: MonitoredGroup) {
  try {
    await ElMessageBox.confirm(
      `确定移除「${displayName(g)}」？下次扫描仍会重新加入。`,
      "移除群组",
      { type: "warning", confirmButtonText: "移除", cancelButtonText: "取消" }
    )
  } catch {
    return
  }
  busy.value = g.id
  try {
    await api.removeGroup(g.id)
    groups.value = groups.value.filter((x) => x.id !== g.id)
    ElMessage.success("已移除")
  } catch (e) {
    ElMessage.error("移除失败：" + (e as Error).message)
  } finally {
    busy.value = null
  }
}

async function onBlock(g: MonitoredGroup) {
  const typeLabel = g.chat_type === "channel" ? "频道" : "群组"
  try {
    await ElMessageBox.confirm(
      `屏蔽「${displayName(g)}」后，该${typeLabel}的红包将被所有账号忽略（不检测/不领取）。`,
      `屏蔽${typeLabel}`,
      { type: "warning", confirmButtonText: "屏蔽", cancelButtonText: "取消" }
    )
  } catch {
    return
  }
  try {
    await api.addBlockRule({
      target_type: g.chat_type === "channel" ? "channel" : "group",
      target_id: g.chat_id,
      target_name: g.title || g.username || String(g.chat_id),
    })
    ElMessage.success("已加入屏蔽，可在「屏蔽管理」移除")
  } catch (e) {
    ElMessage.error("屏蔽失败：" + (e as Error).message)
  }
}

function displayName(g: MonitoredGroup) {
  if (g.title) return g.title
  if (g.username) return `@${g.username}`
  return String(g.chat_id)
}

function membersText(g: MonitoredGroup) {
  if (!g.members_count) return ""
  if (g.members_count >= 10000) return (g.members_count / 10000).toFixed(1) + "w"
  if (g.members_count >= 1000) return (g.members_count / 1000).toFixed(1) + "k"
  return String(g.members_count)
}

onMounted(load)
// 轮询静默刷新：隐藏页/切走时自动暂停（8s）
usePolling(silentRefresh, 8000, false)
</script>

<template>
  <el-card shadow="never" class="groups-card">
    <template #header>
      <div class="card-header">
        <div>
          <div class="card-title">秒包管理</div>
          <div class="card-desc">
            汇总所有运行中账号的群组/频道（去重）。开关控制每个群是否参与秒包，默认全开。
          </div>
        </div>
        <div class="card-actions">
          <el-input
            v-model="keyword"
            placeholder="搜索群名 / 用户名 / ID"
            :prefix-icon="Search"
            clearable
            class="search-input"
          />
          <el-dropdown
            :loading="batchBusy"
            @command="(cmd: string) => batchToggle(cmd === 'on')"
          >
            <el-button>
              快捷操作<el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="on">全部开启秒包</el-dropdown-item>
                <el-dropdown-item command="off">全部关闭秒包</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-button
            type="primary"
            :icon="Refresh"
            :loading="scanning"
            @click="scan"
          >
            扫描群组
          </el-button>
        </div>
      </div>
    </template>

    <el-tabs
      v-if="accountTabs.length > 0"
      v-model="activeTab"
      class="account-tabs"
    >
      <el-tab-pane name="all">
        <template #label>
          全部窗口 <span class="tab-count">{{ groups.length }}</span>
        </template>
      </el-tab-pane>
      <el-tab-pane
        v-for="t in accountTabs"
        :key="t.id"
        :name="String(t.id)"
      >
        <template #label>
          {{ t.name }} <span class="tab-count">{{ t.count }}</span>
        </template>
      </el-tab-pane>
    </el-tabs>

    <div class="filter-bar">
      <el-radio-group v-model="typeFilter" size="small">
        <el-radio-button value="all">全部 {{ total }}</el-radio-button>
        <el-radio-button value="group">群组 {{ groupCount }}</el-radio-button>
        <el-radio-button value="channel">频道 {{ channelCount }}</el-radio-button>
      </el-radio-group>
    </div>

    <el-table
      v-loading="loading"
      :data="loading ? [] : pagedGroups"
      style="width: 100%"
      size="small"
      :row-style="{ height: '52px' }"
      empty-text="没有匹配的群组，点击右上角「扫描群组」从运行中的账号汇总"
    >
      <el-table-column label="类型" width="84">
        <template #default="{ row }">
          <el-tag size="small">
            {{ row.chat_type === "channel" ? "频道" : "群组" }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column label="群组" min-width="220">
        <template #default="{ row }">
          <div class="group-cell">
            <el-avatar :size="32" :src="row.avatar_url || ''" class="group-avatar">
              {{ displayName(row).charAt(0) }}
            </el-avatar>
            <div class="group-info">
              <span class="group-name">
                <el-icon v-if="row.pinned" class="pin-mark"><Top /></el-icon>
                {{ displayName(row) }}
              </span>
            </div>
          </div>
        </template>
      </el-table-column>

      <el-table-column label="ID" width="150">
        <template #default="{ row }">
          <span class="group-id">{{ row.chat_id }}</span>
        </template>
      </el-table-column>

      <el-table-column label="用户名" width="150">
        <template #default="{ row }">
          <span v-if="row.username" class="group-username">@{{ row.username }}</span>
        </template>
      </el-table-column>

      <el-table-column label="人数" width="90">
        <template #default="{ row }">
          <span class="group-members">{{ membersText(row) }}</span>
        </template>
      </el-table-column>

      <el-table-column label="秒包" width="72">
        <template #default="{ row }">
          <el-switch
            :model-value="row.enabled"
            :loading="busy === row.id"
            :disabled="busy === row.id"
            size="small"
            @update:model-value="(v: boolean) => onToggle(row, v)"
          />
        </template>
      </el-table-column>

      <el-table-column label="操作" width="130" align="right">
        <template #default="{ row }">
          <el-button
            text
            :icon="Top"
            :type="row.pinned ? 'primary' : 'default'"
            :disabled="busy === row.id"
            :title="row.pinned ? '取消置顶' : '置顶'"
            @click="onPin(row)"
          />
          <el-button
            text
            :icon="CircleClose"
            type="danger"
            :disabled="busy === row.id"
            title="屏蔽此群/频道"
            @click="onBlock(row)"
          />
          <el-button
            text
            :icon="Delete"
            :disabled="busy === row.id"
            title="移除"
            @click="onDelete(row)"
          />
        </template>
      </el-table-column>
    </el-table>

    <div class="footer-bar">
      <div class="stat-area">
        <span v-if="!loading && groups.length > 0" class="stat-info">
          共 {{ groups.length }} 个群组，{{ enabledCount }} 个开启秒包{{
            keyword ? `（筛选出 ${total} 个）` : ""
          }}
        </span>
      </div>
      <el-pagination
        v-model:current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="total, prev, pager, next"
        small
      />
    </div>
  </el-card>
</template>

<style scoped>
.groups-card {
  display: flex;
  flex-direction: column;
}

.card-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.card-title {
  font-size: 16px;
  font-weight: 600;
}

.card-desc {
  margin-top: 4px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.card-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}

.search-input {
  width: 220px;
}

.account-tabs {
  margin-bottom: 4px;
}

.account-tabs :deep(.el-tabs__header) {
  margin-bottom: 8px;
}

.filter-bar {
  display: flex;
  align-items: center;
  margin-bottom: 12px;
}

.tab-count {
  display: inline-block;
  min-width: 18px;
  padding: 0 5px;
  margin-left: 2px;
  font-size: 11px;
  line-height: 16px;
  text-align: center;
  border-radius: 8px;
  background: var(--el-fill-color);
  color: var(--el-text-color-secondary);
}

.group-cell {
  display: flex;
  align-items: center;
  gap: 12px;
}

.group-avatar {
  flex-shrink: 0;
}

.group-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.group-name {
  display: flex;
  align-items: center;
  gap: 4px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.pin-mark {
  color: var(--el-color-primary);
  font-size: 13px;
  flex-shrink: 0;
}

.group-username {
  color: var(--el-color-primary);
  font-size: 13px;
}

.group-id {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.group-members {
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-regular);
}

.group-muted {
  color: var(--el-text-color-secondary);
}

.footer-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 12px;
  min-height: 32px;
}

.stat-area {
  display: flex;
  align-items: center;
}

.stat-info {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
</style>
