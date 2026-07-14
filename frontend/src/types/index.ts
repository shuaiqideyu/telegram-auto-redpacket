export interface Account {
  id: number
  phone: string
  user_id: number | null
  name: string | null
  username: string | null
  enabled: boolean
  status: string
  monitor_enabled: boolean
  claim_enabled: boolean
  running: boolean
  connected: boolean
  has_session: boolean
  groups_count: number
  proxy: string | null
  avatar_url: string | null
  uptime_s: number | null
  detected: number
  success: number
  failed: number
  created_at: string | null
}

export interface ModuleToggle {
  key: string
  label: string
  enabled: boolean
  description: string
  sort: number
}

export interface ModuleConfigField {
  key: string
  label: string
  type: "text" | "password" | "switch"
  hint: string
  value: string
}

export interface FulilaiPoolStatus {
  running: boolean
  available: number
  pool_size: number
  solving: number
  total_solved: number
  total_failed: number
}

export interface CaptchaStatus {
  glyph_count: number
}

export interface Settings {
  vision_api_key: string
  vision_base_url: string
  vision_model: string
  vision_models: string
  max_attempts: string
  notify_bot_token: string
  direct_keywords: string
  twocaptcha_key: string
  // —— 领取策略过滤（字符串形态，与后端 settings KV 对齐）——
  filter_keywords: string // 换行分隔关键词黑名单
  filter_currency_mode: string // off | white | black
  filter_currencies: string // 逗号分隔币种（大写）
  filter_min_amounts: string // JSON 对象 {"USDT":1,"*":0.5}
  filter_skip_conditions: string // 逗号分隔条件类型
}

export interface MonitoredGroup {
  id: number
  chat_id: number
  title: string | null
  username: string | null
  members_count: number | null
  chat_type: string
  avatar_url: string | null
  enabled: boolean
  pinned: boolean
  source_count: number
  source_account_ids: number[]
  updated_at: string | null
}

export interface GrabRecordItem {
  id: number
  account_name: string
  chat: string
  target_bot: string
  kind: string
  wallet: string
  conditions: string[]
  ok: boolean
  amount: string
  total_s: number | null
  report: string
  created_at: string
}

export interface RecordPage {
  total: number
  page: number
  size: number
  items: GrabRecordItem[]
}

export interface RecordStats {
  total: number
  success: number
  failed: number
  by_account: { name: string; count: number }[]
  by_bot: { bot: string; count: number }[]
  by_wallet: { wallet: string; count: number }[]
}

// key 集合与后端 core/detector.py WALLET_LABELS 对齐。
// 展示文案约定：英文品牌（OKPay/KKPay）不带后缀，中文品牌带「钱包」后缀。
export const WALLET_LABEL: Record<string, string> = {
  okpay: "OKPay",
  kkpay: "KKPay",
  wlqb: "未来钱包",
  dlqb: "达利钱包",
  fllqb: "福利来钱包",
  unknown: "未知",
}

export const KIND_LABEL: Record<string, string> = {
  direct: "关键词领取",
  captcha: "窗口验证码",
  dm_captcha: "私信验证",
  webapp: "网页验证",
  fulilai: "福利来红包",
  locked: "未解锁红包",
}

export const CONDITION_LABEL: Record<string, string> = {
  premium: "Premium会员",
  group: "指定群组",
  user: "指定用户",
  turnover: "流水要求",
  winloss: "输赢要求",
  locked: "已锁定",
}

// 把 "group:28圈" → "指定群组[28圈]"，"premium" → "Premium会员"
export function conditionText(cond: string): string {
  const idx = cond.indexOf(":")
  if (idx === -1) return CONDITION_LABEL[cond] ?? cond
  const base = cond.slice(0, idx)
  const val = cond.slice(idx + 1)
  const label = CONDITION_LABEL[base] ?? base
  return val ? `${label}[${val}]` : label
}

export interface ImportResult {
  imported: number
  failed: number
  accounts: Account[]
  errors: { session: string; error: string }[]
}

// —— 屏蔽规则 ——
export type BlockType = "group" | "channel" | "user" | "bot"

export interface BlockRule {
  id: number
  target_type: BlockType
  target_id: number
  target_name: string | null
  note: string | null
  created_at: string | null
}

export interface BlockList {
  rules: BlockRule[]
  block_private: boolean
  counts: Record<BlockType, number>
}

export const BLOCK_TYPE_LABEL: Record<BlockType, string> = {
  group: "群组",
  channel: "频道",
  user: "用户",
  bot: "机器人",
}

// —— 总览 Dashboard ——
export interface TrendPoint {
  date: string
  total: number
  success: number
}

export interface OverviewRecent {
  id: number
  account_name: string
  chat: string
  wallet: string
  kind: string
  amount: string
  created_at: string
}

export interface Overview {
  accounts: {
    total: number
    running: number
    connected: number
    by_status: Record<string, number>
  }
  today: { total: number; success: number; failed: number; success_rate: number }
  totals: { total: number; success: number; failed: number }
  trend: TrendPoint[]
  by_wallet: { wallet: string; count: number }[]
  by_account: { name: string; count: number }[]
  recent: OverviewRecent[]
  pool: FulilaiPoolStatus
}

// 账号状态文案 + 语义色（前端唯一来源，账号页/总览共用）
export const ACCOUNT_STATUS: Record<string, { label: string; type: "success" | "info" | "warning" | "danger" | "primary" }> = {
  running: { label: "监听中", type: "success" },
  authorized: { label: "已登录", type: "primary" },
  stopped: { label: "已停止", type: "info" },
  error: { label: "异常", type: "danger" },
  banned: { label: "已封禁", type: "danger" },
  expired: { label: "已失效", type: "warning" },
  deactivated: { label: "已注销", type: "danger" },
  new: { label: "未登录", type: "info" },
}

export interface QRStart {
  bind_id: string
  qr_url: string
  expires_at: string
}

export interface QRPoll {
  pending?: boolean
  expired?: boolean
  needs_password?: boolean
  bind_id?: string
  done?: boolean
  account?: Account
}
