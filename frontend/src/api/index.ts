// 后端 API 客户端（开发态经 Vite 代理到 FastAPI）。
import axios, { AxiosError } from "axios"

import type {
  Account,
  BlockList,
  BlockRule,
  BlockType,
  CaptchaStatus,
  FulilaiPoolStatus,
  ImportResult,
  ModuleConfigField,
  ModuleToggle,
  MonitoredGroup,
  Overview,
  QRPoll,
  QRStart,
  RecordPage,
  RecordStats,
  Settings,
} from "@/types"

const http = axios.create({
  baseURL: (import.meta.env.VITE_API_BASE ?? "") + "/api",
  headers: { "Content-Type": "application/json" },
})

// 统一把后端 {detail} 错误展开为 Error.message
http.interceptors.response.use(
  (res) => res,
  (err: AxiosError<{ detail?: string }>) => {
    const detail =
      err.response?.data?.detail ??
      (err.response ? `HTTP ${err.response.status}` : err.message)
    return Promise.reject(new Error(detail))
  }
)

export const api = {
  // —— 账号 ——
  listAccounts: () => http.get<Account[]>("/accounts").then((r) => r.data),
  loginStart: (phone: string) =>
    http
      .post<{ phone: string; needs_code: boolean }>("/accounts/login/start", {
        phone,
      })
      .then((r) => r.data),
  loginCode: (phone: string, code: string) =>
    http
      .post<{ needs_password?: boolean; done?: boolean; account?: Account }>(
        "/accounts/login/code",
        { phone, code }
      )
      .then((r) => r.data),
  loginPassword: (phone: string, password: string) =>
    http
      .post<{ done?: boolean; account?: Account }>("/accounts/login/password", {
        phone,
        password,
      })
      .then((r) => r.data),
  setEnabled: (id: number, enabled: boolean) =>
    http
      .post<{ ok: boolean; enabled: boolean }>(
        `/accounts/${id}/enable?enabled=${enabled}`
      )
      .then((r) => r.data),
  startAccount: (id: number) =>
    http
      .post<{ ok: boolean; running: boolean }>(`/accounts/${id}/start`)
      .then((r) => r.data),
  stopAccount: (id: number) =>
    http
      .post<{ ok: boolean; running: boolean }>(`/accounts/${id}/stop`)
      .then((r) => r.data),
  setMonitor: (id: number, enabled: boolean) =>
    http
      .post<{ ok: boolean; monitor_enabled: boolean }>(
        `/accounts/${id}/monitor?enabled=${enabled}`
      )
      .then((r) => r.data),
  setClaim: (id: number, enabled: boolean) =>
    http
      .post<{ ok: boolean; claim_enabled: boolean }>(
        `/accounts/${id}/claim?enabled=${enabled}`
      )
      .then((r) => r.data),
  setProxy: (id: number, proxy: string | null) =>
    http
      .post<{ ok: boolean; proxy: string | null; restarted: boolean }>(
        `/accounts/${id}/proxy`,
        { proxy }
      )
      .then((r) => r.data),
  deleteAccount: (id: number) =>
    http.delete<{ ok: boolean }>(`/accounts/${id}`).then((r) => r.data),

  // —— QR 扫码登录 ——
  qrStart: () =>
    http.post<QRStart>("/accounts/login/qr/start").then((r) => r.data),
  qrPoll: (bind_id: string) =>
    http
      .post<QRPoll>("/accounts/login/qr/poll", { bind_id })
      .then((r) => r.data),
  qrPassword: (bind_id: string, password: string) =>
    http
      .post<{ done?: boolean; account?: Account }>(
        "/accounts/login/qr/password",
        { bind_id, password }
      )
      .then((r) => r.data),

  // —— Session 批量导入 ——
  importSessions: (sessions: string[]) =>
    http
      .post<ImportResult>("/accounts/import-sessions", { sessions })
      .then((r) => r.data),

  // —— 模块开关 ——
  getModules: () => http.get<ModuleToggle[]>("/modules").then((r) => r.data),
  toggleModule: (key: string, enabled: boolean) =>
    http
      .put<ModuleToggle>(`/modules/${key}`, { enabled })
      .then((r) => r.data),
  batchToggleModules: (enabled: boolean) =>
    http
      .post<{ ok: boolean; count: number; enabled: boolean }>("/modules/batch", {
        enabled,
      })
      .then((r) => r.data),
  getModuleConfig: (key: string) =>
    http
      .get<ModuleConfigField[]>(`/modules/${key}/config`)
      .then((r) => r.data),
  updateModuleConfig: (key: string, data: Record<string, string>) =>
    http
      .put<ModuleConfigField[]>(`/modules/${key}/config`, data)
      .then((r) => r.data),
  getModuleStatus: <T = FulilaiPoolStatus | CaptchaStatus>(key: string) =>
    http.get<T>(`/modules/${key}/status`).then((r) => r.data),

  // —— 系统配置 ——
  getSettings: () => http.get<Settings>("/settings").then((r) => r.data),
  updateSettings: (data: Partial<Settings>) =>
    http.put<Settings>("/settings", data).then((r) => r.data),

  // —— 秒包记录 ——
  getRecords: (page = 1, size = 20, okOnly = false) =>
    http
      .get<RecordPage>("/records", { params: { page, size, ok_only: okOnly } })
      .then((r) => r.data),
  getRecordStats: () =>
    http.get<RecordStats>("/records/stats").then((r) => r.data),

  // —— 秒包群组 ——
  listGroups: () =>
    http.get<MonitoredGroup[]>("/groups").then((r) => r.data),
  scanGroups: () =>
    http.post<MonitoredGroup[]>("/groups/scan").then((r) => r.data),
  toggleGroup: (id: number, enabled: boolean) =>
    http
      .put<MonitoredGroup>(`/groups/${id}/toggle`, { enabled })
      .then((r) => r.data),
  pinGroup: (id: number, pinned: boolean) =>
    http
      .put<MonitoredGroup>(`/groups/${id}/pin`, { pinned })
      .then((r) => r.data),
  batchToggleGroups: (enabled: boolean) =>
    http
      .post<{ ok: boolean; count: number }>("/groups/batch", { enabled })
      .then((r) => r.data),
  removeGroup: (id: number) =>
    http.delete<{ ok: boolean }>(`/groups/${id}`).then((r) => r.data),

  // —— 总览 ——
  getOverview: () => http.get<Overview>("/overview").then((r) => r.data),

  // —— 屏蔽规则 ——
  getBlocklist: () => http.get<BlockList>("/blocklist").then((r) => r.data),
  addBlockRule: (rule: {
    target_type: BlockType
    target_id: number
    target_name?: string | null
    note?: string | null
  }) => http.post<BlockRule>("/blocklist", rule).then((r) => r.data),
  removeBlockRule: (id: number) =>
    http.delete<{ ok: boolean }>(`/blocklist/${id}`).then((r) => r.data),
  setBlockPrivate: (enabled: boolean) =>
    http
      .put<{ ok: boolean; block_private: boolean }>("/blocklist/private", {
        enabled,
      })
      .then((r) => r.data),
}
