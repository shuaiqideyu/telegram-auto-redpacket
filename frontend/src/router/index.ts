import { createRouter, createWebHistory } from "vue-router"

import AppLayout from "@/layout/AppLayout.vue"

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: "/",
      component: AppLayout,
      redirect: "/dashboard",
      children: [
        {
          path: "dashboard",
          name: "dashboard",
          meta: { title: "总览" },
          component: () => import("@/views/DashboardView.vue"),
        },
        {
          path: "accounts",
          name: "accounts",
          meta: { title: "账号管理" },
          component: () => import("@/views/AccountsView.vue"),
        },
        {
          path: "modules",
          name: "modules",
          meta: { title: "红包模块" },
          component: () => import("@/views/ModulesView.vue"),
        },
        {
          path: "groups",
          name: "groups",
          meta: { title: "秒包管理" },
          component: () => import("@/views/GroupsView.vue"),
        },
        {
          path: "blocklist",
          name: "blocklist",
          meta: { title: "屏蔽管理" },
          component: () => import("@/views/BlocklistView.vue"),
        },
        {
          path: "records",
          name: "records",
          meta: { title: "秒包记录" },
          component: () => import("@/views/RecordsView.vue"),
        },
        {
          path: "settings",
          name: "settings",
          meta: { title: "系统配置" },
          component: () => import("@/views/SettingsView.vue"),
        },
      ],
    },
    { path: "/:pathMatch(.*)*", redirect: "/dashboard" },
  ],
})
