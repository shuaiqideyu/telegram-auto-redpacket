<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue"
import { ArrowDown, Connection, Delete, Plus, Refresh, VideoPause, VideoPlay } from "@element-plus/icons-vue"

import { api } from "@/api"
import type { Account } from "@/types"
import { ACCOUNT_STATUS } from "@/types"
import { usePolling } from "@/composables/usePolling"
import { mergeById } from "@/lib/merge"
import LoginDialog from "@/components/LoginDialog.vue"
import PageHeader from "@/components/PageHeader.vue"
import StatusDot from "@/components/StatusDot.vue"

const accounts = ref<Account[]>([])
const loading = ref(true)
const dialogOpen = ref(false)
const busy = ref<number | null>(null)
const batchBusy = ref(false)
const selected = ref<Account[]>([])
const page = ref(1)
const pageSize = ref(20)

const AVATAR_COLORS = ["#ec5b13", "#16a34a", "#fcbb00", "#ef4444", "#0ea5e9", "#a855f7", "#14b8a6"]
function avatarColor(id: number) {
  return AVATAR_COLORS[id % AVATAR_COLORS.length]
}
function initial(a: Account) {
  return (a.name || a.username || "?").charAt(0).toUpperCase()
}
function maskPhone(p: string) {
  if (!p || p.startsWith("user_")) return ""
  return p.length > 6 ? p.slice(0, 3) + "****" + p.slice(-2) : p
}
function fmtUptime(s: number | null) {
  if (!s || s < 0) return ""
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (h > 0) return `${h}h${m}m`
  if (m > 0) return `${m}m`
  return `${Math.floor(s)}s`
}

function statusOf(s: string) {
  return ACCOUNT_STATUS[s] ?? ACCOUNT_STATUS.new
}

const total = computed(() => accounts.value.length)
const runningCount = computed(() => accounts.value.filter((a) => a.running).length)
const pagedAccounts = computed(() => {
  const start = (page.value - 1) * pageSize.value
  return accounts.value.slice(start, start + pageSize.value)
})
const hasSelected = computed(() => selected.value.length > 0)

async function load() {
  try {
    accounts.value = await api.listAccounts()
  } catch (e) {
    ElMessage.error("加载账号失败：" + (e as Error).message)
  } finally {
    loading.value = false
  }
}

async function silentRefresh() {
  if (busy.value !== null || batchBusy.value) return
  try {
    mergeById(accounts.value, await api.listAccounts())
  } catch {
    /* 静默 */
  }
}

async function onMonitorChange(a: Account, val: boolean) {
  try {
    await api.setMonitor(a.id, val)
    a.monitor_enabled = val
  } catch (e) {
    ElMessage.error("操作失败：" + (e as Error).message)
  }
}

async function onClaimChange(a: Account, val: boolean) {
  try {
    await api.setClaim(a.id, val)
    a.claim_enabled = val
  } catch (e) {
    ElMessage.error("操作失败：" + (e as Error).message)
  }
}

async function act(id: number, fn: () => Promise<unknown>, ok: string) {
  busy.value = id
  try {
    await fn()
    ElMessage.success(ok)
    await load()
  } catch (e) {
    ElMessage.error("操作失败：" + (e as Error).message)
  } finally {
    busy.value = null
  }
}

function toggleRun(a: Account) {
  if (a.running) act(a.id, () => api.stopAccount(a.id), "已停止监听")
  else act(a.id, () => api.startAccount(a.id), "已启动监听")
}

async function onDelete(a: Account) {
  try {
    await ElMessageBox.confirm(`确定删除账号「${a.name || "未命名"}」吗？`, "删除账号", {
      type: "warning",
      confirmButtonText: "删除",
      cancelButtonText: "取消",
    })
  } catch {
    return
  }
  act(a.id, () => api.deleteAccount(a.id), "已删除")
}

// 代理编辑
const proxyDialog = ref(false)
const proxyTarget = ref<Account | null>(null)
const proxyForm = reactive({ proxy: "" })
const proxySaving = ref(false)

function openProxy(a: Account) {
  proxyTarget.value = a
  proxyForm.proxy = a.proxy || ""
  proxyDialog.value = true
}

async function saveProxy() {
  if (!proxyTarget.value) return
  proxySaving.value = true
  try {
    const r = await api.setProxy(proxyTarget.value.id, proxyForm.proxy.trim() || null)
    proxyDialog.value = false
    await load()
    ElMessage.success(r.restarted ? "代理已保存，账号已重启生效" : "代理已保存")
  } catch (e) {
    ElMessage.error("保存失败：" + (e as Error).message)
  } finally {
    proxySaving.value = false
  }
}

function onSelectionChange(rows: Account[]) {
  selected.value = rows
}

async function batchMonitor(val: boolean) {
  batchBusy.value = true
  for (const a of accounts.value) {
    try {
      await api.setMonitor(a.id, val)
    } catch {
      /* continue */
    }
  }
  await load()
  batchBusy.value = false
  ElMessage.success(val ? "全部已开启监听" : "全部已关闭监听")
}

async function batchClaim(val: boolean) {
  batchBusy.value = true
  for (const a of accounts.value) {
    try {
      await api.setClaim(a.id, val)
    } catch {
      /* continue */
    }
  }
  await load()
  batchBusy.value = false
  ElMessage.success(val ? "全部已开启秒包" : "全部已关闭秒包")
}

async function batchStart() {
  batchBusy.value = true
  const targets = selected.value.filter((a) => !a.running && a.has_session)
  let ok = 0
  for (const a of targets) {
    try {
      await api.startAccount(a.id)
      ok++
    } catch {
      /* continue */
    }
  }
  ElMessage.success(`已开启 ${ok} 个账号监听`)
  await load()
  batchBusy.value = false
}

async function batchStop() {
  batchBusy.value = true
  const targets = selected.value.filter((a) => a.running)
  let ok = 0
  for (const a of targets) {
    try {
      await api.stopAccount(a.id)
      ok++
    } catch {
      /* continue */
    }
  }
  ElMessage.success(`已停止 ${ok} 个账号监听`)
  await load()
  batchBusy.value = false
}

async function batchDelete() {
  if (selected.value.length === 0) return
  try {
    await ElMessageBox.confirm(`确定删除选中的 ${selected.value.length} 个账号吗？`, "批量删除", {
      type: "warning",
      confirmButtonText: "删除",
      cancelButtonText: "取消",
    })
  } catch {
    return
  }
  batchBusy.value = true
  let ok = 0
  for (const a of selected.value) {
    try {
      await api.deleteAccount(a.id)
      ok++
    } catch {
      /* continue */
    }
  }
  ElMessage.success(`已删除 ${ok} 个账号`)
  await load()
  batchBusy.value = false
}

onMounted(load)
usePolling(silentRefresh, 8000, false)
</script>

<template>
  <div>
    <PageHeader title="账号管理" :desc="`共 ${total} 个账号，${runningCount} 个监听中。登录导入账号并控制每个账号的抢红包监听。`">
      <template #actions>
        <el-button :icon="Refresh" @click="load">刷新</el-button>
        <el-dropdown
          @command="(cmd: string) => {
            if (cmd === 'mon-on') batchMonitor(true)
            else if (cmd === 'mon-off') batchMonitor(false)
            else if (cmd === 'claim-on') batchClaim(true)
            else if (cmd === 'claim-off') batchClaim(false)
          }"
        >
          <el-button>快捷操作<el-icon class="el-icon--right"><ArrowDown /></el-icon></el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="mon-on">全部开启监听</el-dropdown-item>
              <el-dropdown-item command="mon-off">全部关闭监听</el-dropdown-item>
              <el-dropdown-item divided command="claim-on">全部开启秒包</el-dropdown-item>
              <el-dropdown-item command="claim-off">全部关闭秒包</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button type="primary" :icon="Plus" @click="dialogOpen = true">添加账号</el-button>
      </template>
    </PageHeader>

    <el-card shadow="never">
      <el-table
        v-loading="loading"
        :data="loading ? [] : pagedAccounts"
        style="width: 100%"
        :row-style="{ height: '60px' }"
        empty-text="还没有账号，点击右上角「添加账号」导入"
        @selection-change="onSelectionChange"
      >
        <el-table-column type="selection" width="40" />

        <el-table-column label="账号" min-width="170">
          <template #default="{ row }">
            <div class="acct-cell">
              <el-avatar
                :size="34"
                shape="square"
                :src="row.avatar_url || undefined"
                class="acct-avatar"
                :style="{ background: avatarColor(row.id) }"
              >
                {{ initial(row) }}
              </el-avatar>
              <span class="acct-name">{{ row.name || "未命名" }}</span>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="用户名" width="130">
          <template #default="{ row }">
            <span v-if="row.username" class="acct-username">@{{ row.username }}</span>
          </template>
        </el-table-column>

        <el-table-column label="ID" width="120">
          <template #default="{ row }">
            <span v-if="row.user_id" class="mono dim">{{ row.user_id }}</span>
          </template>
        </el-table-column>

        <el-table-column label="手机号" width="120">
          <template #default="{ row }">
            <span class="mono dim">{{ maskPhone(row.phone) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="状态" width="150">
          <template #default="{ row }">
            <div class="status-cell">
              <el-tag :type="statusOf(row.status).type" effect="light" size="small">
                {{ statusOf(row.status).label }}
              </el-tag>
              <span v-if="row.running" class="conn">
                <StatusDot
                  :tone="row.connected ? 'success' : 'warning'"
                  :pulse="row.connected"
                  :label="row.connected ? fmtUptime(row.uptime_s) : '连接中'"
                />
              </span>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="窗口" width="70" align="right">
          <template #default="{ row }">
            <span v-if="row.running" class="tnum">{{ row.groups_count }}</span>
          </template>
        </el-table-column>

        <el-table-column label="代理" width="100">
          <template #default="{ row }">
            <el-button text size="small" @click="openProxy(row)">
              <el-icon class="proxy-icon"><Connection /></el-icon>
              <span :class="row.proxy ? 'proxy-on' : 'dim'">{{ row.proxy ? "已设" : "直连" }}</span>
            </el-button>
          </template>
        </el-table-column>

        <el-table-column label="监听" width="64">
          <template #default="{ row }">
            <el-switch
              :model-value="row.monitor_enabled"
              size="small"
              @update:model-value="(v: boolean) => onMonitorChange(row, v)"
            />
          </template>
        </el-table-column>

        <el-table-column label="秒包" width="64">
          <template #default="{ row }">
            <el-switch
              :model-value="row.claim_enabled"
              size="small"
              @update:model-value="(v: boolean) => onClaimChange(row, v)"
            />
          </template>
        </el-table-column>

        <el-table-column label="操作" width="110" align="right">
          <template #default="{ row }">
            <el-button
              text
              :icon="row.running ? VideoPause : VideoPlay"
              :type="row.running ? 'warning' : 'success'"
              :disabled="busy === row.id || (!row.running && !row.has_session)"
              :title="row.running ? '停止监听' : '启动监听'"
              @click="toggleRun(row)"
            />
            <el-button
              text
              :icon="Delete"
              :disabled="busy === row.id"
              title="删除"
              @click="onDelete(row)"
            />
          </template>
        </el-table-column>
      </el-table>

      <div class="footer-bar">
        <div class="batch-area">
          <template v-if="hasSelected">
            <span class="batch-info">已选 {{ selected.length }} 个</span>
            <el-button size="small" type="success" :loading="batchBusy" @click="batchStart">开启</el-button>
            <el-button size="small" type="warning" :loading="batchBusy" @click="batchStop">停止</el-button>
            <el-button size="small" type="danger" :loading="batchBusy" @click="batchDelete">删除</el-button>
          </template>
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

    <!-- 代理编辑 -->
    <el-dialog v-model="proxyDialog" :title="`代理设置 — ${proxyTarget?.name || ''}`" width="460" destroy-on-close>
      <el-form label-position="top">
        <el-form-item label="代理地址">
          <el-input v-model="proxyForm.proxy" placeholder="socks5://user:pass@host:port（留空=直连）" clearable />
          <div class="hint">
            支持 socks5 / socks4 / http。变更后该账号若在运行会自动重启以生效。
            建议每账号独立出口 IP，降低多账号同 IP 并发被风控的风险。
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="proxyDialog = false">取消</el-button>
        <el-button type="primary" :loading="proxySaving" @click="saveProxy">保存</el-button>
      </template>
    </el-dialog>

    <LoginDialog v-model="dialogOpen" @success="load" />
  </div>
</template>

<style scoped>
.acct-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}

.acct-avatar {
  border-radius: 8px;
  box-shadow: var(--chunky-shadow-sm);
  color: #fff;
  font-weight: 700;
  font-size: 15px;
  flex-shrink: 0;
}

.acct-name {
  font-weight: 600;
}

.acct-username {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-color-primary);
}

.status-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.conn {
  display: inline-flex;
}

.proxy-icon {
  margin-right: 3px;
}

.proxy-on {
  color: var(--el-color-success);
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.dim {
  color: var(--el-text-color-placeholder);
}

.footer-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 12px;
  min-height: 32px;
}

.batch-area {
  display: flex;
  align-items: center;
  gap: 6px;
}

.batch-info {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.hint {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
}
</style>
