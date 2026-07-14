<script setup lang="ts">
import { computed, ref } from "vue"
import { useRoute, useRouter } from "vue-router"
import {
  ChatLineSquare,
  CircleClose,
  DataLine,
  Expand,
  Fold,
  Grid,
  Odometer,
  Present,
  Setting,
  User,
} from "@element-plus/icons-vue"

import { api } from "@/api"
import type { Overview } from "@/types"
import { usePolling } from "@/composables/usePolling"

const route = useRoute()
const router = useRouter()
const collapsed = ref(false)

const NAV = [
  { index: "/dashboard", label: "总览", icon: Odometer },
  { index: "/accounts", label: "账号管理", icon: User },
  { index: "/modules", label: "红包模块", icon: Grid },
  { index: "/groups", label: "秒包管理", icon: ChatLineSquare },
  { index: "/blocklist", label: "屏蔽管理", icon: CircleClose },
  { index: "/records", label: "秒包记录", icon: DataLine },
  { index: "/settings", label: "系统配置", icon: Setting },
]

const activeIndex = computed(() => "/" + (route.path.split("/")[1] || "dashboard"))
const title = computed(() => (route.meta.title as string) || "")

function onSelect(index: string) {
  router.push(index)
}

// 顶栏全局状态（轻量轮询，切到后台自动暂停）
const ov = ref<Overview | null>(null)
async function loadStatus() {
  try {
    ov.value = await api.getOverview()
  } catch {
    /* 静默：后端重启窗口期忽略 */
  }
}
usePolling(loadStatus, 10000)
</script>

<template>
  <el-container class="layout">
    <el-aside :width="collapsed ? '72px' : '224px'" class="aside">
      <div class="brand">
        <div class="brand-logo chunky-shadow-sm">
          <el-icon :size="20"><Present /></el-icon>
        </div>
        <div v-if="!collapsed" class="brand-text">
          <span class="brand-name">自动抢红包 v0.2 学习版</span>
          <span class="brand-sub">红包系统</span>
        </div>
      </div>

      <el-menu
        :default-active="activeIndex"
        :collapse="collapsed"
        :collapse-transition="false"
        class="menu"
        @select="onSelect"
      >
        <el-menu-item v-for="item in NAV" :key="item.index" :index="item.index">
          <el-icon><component :is="item.icon" /></el-icon>
          <template #title>{{ item.label }}</template>
        </el-menu-item>
      </el-menu>

      <div v-if="!collapsed" class="aside-footer">
        <span class="version-pill">v0.2.0</span>
      </div>
    </el-aside>

    <el-container>
      <el-header class="header">
        <el-button text class="collapse-btn" @click="collapsed = !collapsed">
          <el-icon :size="18">
            <Expand v-if="collapsed" />
            <Fold v-else />
          </el-icon>
        </el-button>
        <el-divider direction="vertical" />
        <span class="header-title">{{ title }}</span>

        <div class="header-status" v-if="ov">
          <span class="hs-pill">
            <span class="hs-dot hs-dot--running" />
            运行 <b class="tnum">{{ ov.accounts.running }}</b
            >/<span class="tnum">{{ ov.accounts.total }}</span>
          </span>
          <span class="hs-pill">
            今日成功 <b class="tnum">{{ ov.today.success }}</b>
            <span class="hs-muted">/{{ ov.today.total }}</span>
          </span>
          <span class="hs-pill">
            福利来池
            <b class="tnum">{{ ov.pool.available }}</b
            ><span class="hs-muted">/{{ ov.pool.pool_size }}</span>
          </span>
        </div>
      </el-header>

      <el-main class="main">
        <div class="main-inner">
          <router-view v-slot="{ Component }">
            <keep-alive>
              <component :is="Component" />
            </keep-alive>
          </router-view>
        </div>
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.layout {
  height: 100vh;
}

.aside {
  display: flex;
  flex-direction: column;
  border-right: 3px solid #000;
  background: var(--el-bg-color);
  transition: width 0.2s;
  overflow: hidden;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 64px;
  padding: 0 16px;
  border-bottom: 2px solid #000;
  flex-shrink: 0;
}

.brand-logo {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: 2px solid #000;
  background: var(--memphis-orange);
  color: #fff;
  flex-shrink: 0;
}

.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
  white-space: nowrap;
}

.brand-name {
  font-weight: 700;
  font-size: 15px;
  letter-spacing: -0.01em;
}

.brand-sub {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
}

.menu {
  flex: 1;
  border-right: none;
  padding: 10px 12px;
  --el-menu-item-height: 44px;
}

/* 胶囊式导航项 */
.menu :deep(.el-menu-item) {
  border-radius: 8px;
  margin-bottom: 6px;
  font-weight: 600;
  border: 2px solid transparent;
  transition:
    background-color 0.12s,
    border-color 0.12s,
    box-shadow 0.12s;
}

.menu :deep(.el-menu-item:hover) {
  background: var(--memphis-yellow);
  border-color: #000;
}

.menu :deep(.el-menu-item.is-active) {
  background: var(--memphis-orange);
  color: #fff;
  border-color: #000;
  box-shadow: var(--chunky-shadow);
  font-weight: 700;
}

.menu.el-menu--collapse {
  padding: 10px 8px;
}

.menu.el-menu--collapse :deep(.el-menu-item) {
  border-radius: 8px;
}

.aside-footer {
  padding: 14px 16px;
  border-top: 2px solid #000;
}

.version-pill {
  display: inline-block;
  padding: 2px 12px;
  font-size: 11px;
  font-weight: 700;
  background: var(--memphis-yellow);
  border: 2px solid #000;
  border-radius: 6px;
  box-shadow: var(--chunky-shadow-sm);
  transform: rotate(-2deg);
}

.header {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 64px;
  border-bottom: 3px solid #000;
  background: var(--el-bg-color);
  flex-shrink: 0;
}

.collapse-btn {
  padding: 6px;
}

.header-title {
  font-size: 16px;
  font-weight: 700;
  letter-spacing: -0.01em;
}

.header-status {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-left: auto;
  font-size: 12.5px;
}

/* 顶栏状态胶囊：统一白底（信息分类不靠色相，状态点才用色） */
.hs-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 12px;
  border: 2px solid #000;
  border-radius: 999px;
  font-weight: 600;
  background: #fff;
  box-shadow: var(--chunky-shadow-sm);
  white-space: nowrap;
}

.hs-pill b {
  font-weight: 700;
}

.hs-muted {
  color: rgba(0, 0, 0, 0.45);
}

.hs-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  border: 1.5px solid #000;
  margin-right: 2px;
}

.hs-dot--running {
  background: var(--el-color-success);
}

.main {
  background: var(--el-bg-color-page);
  padding: 24px;
}

.main-inner {
  max-width: 1320px;
  margin: 0 auto;
}

@media (max-width: 768px) {
  .header-status {
    display: none;
  }
}
</style>
