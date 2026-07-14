<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import {
  Coin,
  Connection,
  Present,
  TrendCharts,
  TrophyBase,
} from "@element-plus/icons-vue"

import { api } from "@/api"
import type { Overview } from "@/types"
import { KIND_LABEL, WALLET_LABEL } from "@/types"
import { VChart } from "@/lib/echarts"
import { usePolling } from "@/composables/usePolling"
import PageHeader from "@/components/PageHeader.vue"
import StatCard from "@/components/StatCard.vue"

const ov = ref<Overview | null>(null)
const loading = ref(true)

// ECharts Memphis 亮色调色板（与主题 token 对齐）
const C = {
  primary: "#ec5b13",
  success: "#16a34a",
  warning: "#f59e0b",
  danger: "#ef4444",
  info: "#64748b",
  axis: "#1a1a1a",
  grid: "rgba(0, 0, 0, 0.12)",
  text: "rgba(0, 0, 0, 0.6)",
}
const PIE = [C.success, C.primary, "#fcbb00", C.danger, "#0ea5e9", "#a855f7", "#14b8a6"]

async function load() {
  try {
    ov.value = await api.getOverview()
  } catch (e) {
    ElMessage.error("加载总览失败：" + (e as Error).message)
  } finally {
    loading.value = false
  }
}

const hasTrend = computed(() => (ov.value?.trend ?? []).some((d) => d.total > 0))
const hasWallet = computed(() => (ov.value?.by_wallet ?? []).length > 0)
const hasAccount = computed(() => (ov.value?.by_account ?? []).length > 0)

const trendOption = computed(() => {
  const t = ov.value?.trend ?? []
  return {
    tooltip: { trigger: "axis" },
    legend: { data: ["抢包", "成功"], textStyle: { color: C.text }, right: 0, top: 0 },
    grid: { left: 8, right: 12, top: 34, bottom: 4, containLabel: true },
    xAxis: {
      type: "category",
      data: t.map((d) => d.date),
      axisLine: { lineStyle: { color: C.axis } },
      axisLabel: { color: C.text },
    },
    yAxis: {
      type: "value",
      minInterval: 1,
      splitLine: { lineStyle: { color: C.grid, type: "dashed" } },
      axisLabel: { color: C.text },
    },
    series: [
      {
        name: "抢包",
        type: "line",
        smooth: true,
        data: t.map((d) => d.total),
        itemStyle: { color: C.primary },
        lineStyle: { width: 3 },
        areaStyle: { color: C.primary, opacity: 0.1 },
      },
      {
        name: "成功",
        type: "line",
        smooth: true,
        data: t.map((d) => d.success),
        itemStyle: { color: C.success },
        lineStyle: { width: 3 },
        areaStyle: { color: C.success, opacity: 0.16 },
      },
    ],
  }
})

const walletOption = computed(() => {
  const w = ov.value?.by_wallet ?? []
  return {
    tooltip: { trigger: "item", formatter: "{b}: {c} ({d}%)" },
    legend: { bottom: 0, textStyle: { color: C.text } },
    color: PIE,
    series: [
      {
        type: "pie",
        radius: ["45%", "70%"],
        center: ["50%", "42%"],
        avoidLabelOverlap: true,
        itemStyle: { borderColor: "#000", borderWidth: 2 },
        label: { color: C.text },
        data: w.map((x) => ({ name: WALLET_LABEL[x.wallet] || x.wallet, value: x.count })),
      },
    ],
  }
})

const accountOption = computed(() => {
  const a = ov.value?.by_account ?? []
  return {
    tooltip: { trigger: "axis" },
    grid: { left: 8, right: 12, top: 16, bottom: 4, containLabel: true },
    xAxis: {
      type: "category",
      data: a.map((x) => x.name),
      axisLine: { lineStyle: { color: C.axis } },
      axisLabel: { color: C.text, interval: 0, rotate: a.length > 5 ? 30 : 0 },
    },
    yAxis: {
      type: "value",
      minInterval: 1,
      splitLine: { lineStyle: { color: C.grid, type: "dashed" } },
      axisLabel: { color: C.text },
    },
    series: [
      {
        type: "bar",
        data: a.map((x) => x.count),
        itemStyle: {
          color: C.primary,
          borderColor: "#000",
          borderWidth: 2,
          borderRadius: [6, 6, 0, 0],
        },
        barMaxWidth: 38,
      },
    ],
  }
})

function fmtTime(iso: string) {
  if (!iso) return ""
  return new Date(iso).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
}

onMounted(load)
usePolling(load, 10000, false)
</script>

<template>
  <div class="dash">
    <PageHeader title="总览" desc="账号运行、今日战绩与抢包趋势一览。数据每 10 秒自动刷新。" />

    <el-skeleton v-if="loading && !ov" :rows="8" animated />

    <template v-else-if="ov">
      <!-- KPI -->
      <div class="kpi-grid">
        <StatCard
          label="运行账号"
          :value="`${ov.accounts.running}/${ov.accounts.total}`"
          :sub="`在线 ${ov.accounts.connected}`"
          tone="primary"
        >
          <template #icon><el-icon><Connection /></el-icon></template>
        </StatCard>
        <StatCard label="今日抢包" :value="ov.today.total" :sub="`成功 ${ov.today.success}`" tone="warning">
          <template #icon><el-icon><Present /></el-icon></template>
        </StatCard>
        <StatCard label="今日成功率" :value="`${ov.today.success_rate}%`" tone="success">
          <template #icon><el-icon><TrendCharts /></el-icon></template>
        </StatCard>
        <StatCard label="累计成功" :value="ov.totals.success" :sub="`共 ${ov.totals.total}`" tone="info">
          <template #icon><el-icon><TrophyBase /></el-icon></template>
        </StatCard>
        <StatCard
          label="福利来池"
          :value="`${ov.pool.available}/${ov.pool.pool_size}`"
          :sub="ov.pool.running ? '运行中' : '已暂停'"
          tone="info"
        >
          <template #icon><el-icon><Coin /></el-icon></template>
        </StatCard>
      </div>

      <!-- 图表 -->
      <div class="chart-grid">
        <el-card shadow="never" class="chart-card chart-card--wide">
          <template #header><span class="ch-title">近 7 日抢包趋势</span></template>
          <VChart v-if="hasTrend" :option="trendOption" autoresize class="chart" />
          <el-empty v-else description="近 7 日暂无抢包数据" :image-size="70" />
        </el-card>

        <el-card shadow="never" class="chart-card">
          <template #header><span class="ch-title">钱包成功分布</span></template>
          <VChart v-if="hasWallet" :option="walletOption" autoresize class="chart" />
          <el-empty v-else description="暂无成功记录" :image-size="70" />
        </el-card>
      </div>

      <div class="chart-grid">
        <el-card shadow="never" class="chart-card chart-card--wide">
          <template #header><span class="ch-title">账号战绩（成功数）</span></template>
          <VChart v-if="hasAccount" :option="accountOption" autoresize class="chart" />
          <el-empty v-else description="暂无成功记录" :image-size="70" />
        </el-card>

        <el-card shadow="never" class="chart-card">
          <template #header><span class="ch-title">最近成功</span></template>
          <div v-if="ov.recent.length" class="recent">
            <div v-for="r in ov.recent" :key="r.id" class="recent-item">
              <div class="recent-main">
                <span class="recent-acct">{{ r.account_name }}</span>
                <el-tag v-if="r.wallet" class="tag-wallet" size="small">{{ WALLET_LABEL[r.wallet] || r.wallet }}</el-tag>
                <span class="recent-amount tnum">{{ r.amount || "" }}</span>
              </div>
              <div class="recent-sub">
                <span class="recent-chat">{{ r.chat || KIND_LABEL[r.kind] || "" }}</span>
                <span class="recent-time">{{ fmtTime(r.created_at) }}</span>
              </div>
            </div>
          </div>
          <el-empty v-else description="暂无成功记录" :image-size="70" />
        </el-card>
      </div>
    </template>
  </div>
</template>

<style scoped>
.dash {
  display: flex;
  flex-direction: column;
}

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: var(--app-gap);
  margin-bottom: var(--app-gap);
}

.chart-grid {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: var(--app-gap);
  margin-bottom: var(--app-gap);
}

.chart-card {
  border-radius: var(--app-radius);
}

.ch-title {
  font-size: 14px;
  font-weight: 600;
}

.chart {
  height: 300px;
  width: 100%;
}

.recent {
  display: flex;
  flex-direction: column;
}

.recent-item {
  padding: 9px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.recent-item:last-child {
  border-bottom: none;
}

.recent-main {
  display: flex;
  align-items: center;
  gap: 8px;
}

.recent-acct {
  font-weight: 500;
  font-size: 13px;
}

.recent-amount {
  margin-left: auto;
  color: var(--el-color-success);
  font-weight: 600;
  font-size: 13px;
}

.recent-sub {
  display: flex;
  justify-content: space-between;
  margin-top: 3px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.recent-chat {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 60%;
}

@media (max-width: 1100px) {
  .kpi-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .chart-grid {
    grid-template-columns: 1fr;
  }
}
</style>
