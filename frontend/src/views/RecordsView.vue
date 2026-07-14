<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import { Refresh, TrophyBase, CircleCheck, CircleClose, Wallet } from "@element-plus/icons-vue"

import { api } from "@/api"
import type { GrabRecordItem, RecordStats } from "@/types"
import { WALLET_LABEL, KIND_LABEL, conditionText } from "@/types"

const items = ref<GrabRecordItem[]>([])
const stats = ref<RecordStats | null>(null)
const loading = ref(true)
const page = ref(1)
const total = ref(0)
const pageSize = ref(20)
const okOnly = ref(false)

const successRate = computed(() => {
  if (!stats.value || stats.value.total === 0) return "0"
  return ((stats.value.success / stats.value.total) * 100).toFixed(1)
})

async function load() {
  loading.value = true
  try {
    const [r, st] = await Promise.all([
      api.getRecords(page.value, pageSize.value, okOnly.value),
      api.getRecordStats(),
    ])
    items.value = r.items
    total.value = r.total
    stats.value = st
  } catch (e) {
    ElMessage.error("加载记录失败：" + (e as Error).message)
  } finally {
    loading.value = false
  }
}

function onPageChange(p: number) {
  page.value = p
  load()
}

function onFilterChange(val: boolean) {
  okOnly.value = val
  page.value = 1
  load()
}

function formatTime(iso: string) {
  if (!iso) return ""
  const d = new Date(iso)
  return d.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  })
}

onMounted(load)
</script>

<template>
  <div class="records-page">
    <!-- 统计概览卡片 -->
    <div class="stat-grid">
      <el-card shadow="never" class="stat-card">
        <div class="stat-body">
          <div class="stat-icon stat-icon--total"><el-icon :size="20"><TrophyBase /></el-icon></div>
          <div class="stat-text">
            <div class="stat-value">{{ stats?.total ?? 0 }}</div>
            <div class="stat-label">总记录</div>
          </div>
        </div>
      </el-card>
      <el-card shadow="never" class="stat-card">
        <div class="stat-body">
          <div class="stat-icon stat-icon--ok"><el-icon :size="20"><CircleCheck /></el-icon></div>
          <div class="stat-text">
            <div class="stat-value">{{ stats?.success ?? 0 }}</div>
            <div class="stat-label">成功</div>
          </div>
        </div>
      </el-card>
      <el-card shadow="never" class="stat-card">
        <div class="stat-body">
          <div class="stat-icon stat-icon--fail"><el-icon :size="20"><CircleClose /></el-icon></div>
          <div class="stat-text">
            <div class="stat-value">{{ stats?.failed ?? 0 }}</div>
            <div class="stat-label">失败</div>
          </div>
        </div>
      </el-card>
      <el-card shadow="never" class="stat-card">
        <div class="stat-body">
          <div class="stat-icon stat-icon--rate"><el-icon :size="20"><Wallet /></el-icon></div>
          <div class="stat-text">
            <div class="stat-value">{{ successRate }}%</div>
            <div class="stat-label">成功率</div>
          </div>
        </div>
      </el-card>
    </div>

    <!-- 按钱包成功分布 -->
    <el-card v-if="stats && stats.by_wallet.length" shadow="never" class="wallet-card">
      <div class="wallet-dist">
        <span class="wallet-dist-title">钱包成功分布</span>
        <el-tag
          v-for="w in stats.by_wallet"
          :key="w.wallet"
          class="tag-wallet"
          size="small"
        >
          {{ WALLET_LABEL[w.wallet] || w.wallet }} · {{ w.count }}
        </el-tag>
      </div>
    </el-card>

    <el-card shadow="never">
      <template #header>
        <div class="table-header">
          <span class="card-title">秒包记录</span>
          <div class="table-actions">
            <el-checkbox
              :model-value="okOnly"
              @update:model-value="(v: boolean) => onFilterChange(v)"
            >
              只看成功
            </el-checkbox>
            <el-button :icon="Refresh" @click="load">刷新</el-button>
          </div>
        </div>
      </template>

      <el-skeleton v-if="loading" :rows="5" animated />

      <el-empty v-else-if="items.length === 0" description="暂无记录" />

      <template v-else>
        <el-table :data="items" style="width: 100%">
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="report-detail">
                <pre v-if="row.report">{{ row.report }}</pre>
                <el-empty v-else description="无详情" :image-size="60" />
              </div>
            </template>
          </el-table-column>
          <el-table-column label="时间" width="130">
            <template #default="{ row }">
              {{ formatTime(row.created_at) }}
            </template>
          </el-table-column>
          <el-table-column label="账号" prop="account_name" width="100" />
          <el-table-column label="群组" prop="chat" min-width="130" show-overflow-tooltip />
          <el-table-column label="钱包" width="120">
            <template #default="{ row }">
              <el-tag v-if="row.wallet" class="tag-wallet" size="small">
                {{ WALLET_LABEL[row.wallet] || row.wallet }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="类型" width="110">
            <template #default="{ row }">
              <el-tag size="small">
                {{ KIND_LABEL[row.kind] || row.kind }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="条件" min-width="140">
            <template #default="{ row }">
              <template v-if="row.conditions && row.conditions.length">
                <el-tag
                  v-for="c in row.conditions"
                  :key="c"
                  type="warning"
                  effect="plain"
                  size="small"
                  class="cond-tag"
                >
                  {{ conditionText(c) }}
                </el-tag>
              </template>
              <span v-else class="muted">无门槛</span>
            </template>
          </el-table-column>
          <el-table-column label="结果" width="80">
            <template #default="{ row }">
              <el-tag :type="row.ok ? 'success' : 'danger'" effect="light">
                {{ row.ok ? "成功" : "失败" }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="金额" prop="amount" width="100" />
          <el-table-column label="耗时" width="80">
            <template #default="{ row }">
              {{ row.total_s ? row.total_s.toFixed(2) + 's' : '' }}
            </template>
          </el-table-column>
        </el-table>

        <div class="pagination">
          <el-pagination
            v-model:current-page="page"
            :page-size="pageSize"
            :total="total"
            layout="total, prev, pager, next"
            @current-change="onPageChange"
          />
        </div>
      </template>
    </el-card>
  </div>
</template>

<style scoped>
.card-title {
  font-size: 16px;
  font-weight: 600;
}

/* 统计卡片 */
.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 16px;
}

.stat-card {
  border-radius: 10px;
}

.stat-body {
  display: flex;
  align-items: center;
  gap: 14px;
}

.stat-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border: 2px solid #000;
  border-radius: 10px;
  box-shadow: var(--chunky-shadow-sm);
  flex-shrink: 0;
}

.stat-icon--total {
  background: var(--el-color-primary-light-8);
  color: #9a3412;
}

.stat-icon--ok {
  background: var(--memphis-mint);
  color: #14532d;
}

.stat-icon--fail {
  background: var(--memphis-pink);
  color: #881337;
}

.stat-icon--rate {
  background: var(--memphis-yellow);
  color: #713f12;
}

.stat-value {
  font-size: 22px;
  font-weight: 700;
  line-height: 1.1;
  letter-spacing: -0.01em;
}

.stat-label {
  margin-top: 2px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.wallet-card {
  margin-bottom: 16px;
  border-radius: 10px;
}

.wallet-dist {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.wallet-dist-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--el-text-color-regular);
  margin-right: 4px;
}

.table-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.table-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.cond-tag {
  margin: 2px 4px 2px 0;
}

.muted {
  color: var(--el-text-color-placeholder);
}

.report-detail {
  padding: 8px 16px;
}

.report-detail pre {
  margin: 0;
  font-family: var(--el-font-family);
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--el-text-color-regular);
}

.pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
