<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue"
import {
  Key,
  EditPen,
  Monitor,
  Coin,
  Setting,
  ChatDotSquare,
  Search,
  Select,
  CloseBold,
} from "@element-plus/icons-vue"

import type { InputInstance } from "element-plus"

import { api } from "@/api"
import type {
  ModuleToggle,
  ModuleConfigField,
  FulilaiPoolStatus,
  CaptchaStatus,
} from "@/types"
import { WALLET_LABEL } from "@/types"
import { usePolling } from "@/composables/usePolling"

const modules = ref<ModuleToggle[]>([])
const loading = ref(true)
const busy = ref<string | null>(null)
const batching = ref(false)
const search = ref("")

const ICONS: Record<string, unknown> = {
  direct: Key,
  captcha: EditPen,
  dm_captcha: ChatDotSquare,
  webapp: Monitor,
  fulilai: Coin,
}

const HAS_CONFIG = new Set(["captcha", "webapp", "fulilai"])

// 每个模块的「具体化」信息：适用钱包 + 识别/领取特征（与 core/detector.py 对齐）
const MODULE_META: Record<string, { wallets: string[]; detail: string }> = {
  direct: {
    wallets: ["okpay", "kkpay", "wlqb", "dlqb"],
    detail: "群内 callback「领取红包」按钮，一步到账，无验证码（最快）",
  },
  captcha: {
    wallets: ["okpay", "kkpay"],
    detail: "群内算式 X + Y = ?，自动解题点按钮（含花式 emoji 解码，答案跨账号共享）",
  },
  dm_captcha: {
    wallets: ["wlqb"],
    detail: "群内 URL 跳转 bot 私聊出题 → 解算式后领取",
  },
  webapp: {
    wallets: ["okpay", "kkpay", "dlqb"],
    detail: "vweb 链接 → WebView → AI 多模型并发识别图片验证码",
  },
  fulilai: {
    wallets: ["fllqb"],
    detail: "fllqb 链接 → hCaptcha token 池 + 纯 HTTP 领取（一 token 一号）",
  },
}

// 搜索过滤：匹配标签 / 描述 / 适用钱包名
const filteredModules = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return modules.value
  return modules.value.filter((m) => {
    const meta = MODULE_META[m.key]
    const walletNames = (meta?.wallets ?? [])
      .map((w) => WALLET_LABEL[w] || w)
      .join(" ")
    const hay = `${m.label} ${m.description} ${meta?.detail ?? ""} ${walletNames} ${m.key}`
    return hay.toLowerCase().includes(q)
  })
})

const enabledCount = computed(() => modules.value.filter((m) => m.enabled).length)

async function onBatch(enabled: boolean) {
  batching.value = true
  try {
    await api.batchToggleModules(enabled)
    modules.value = modules.value.map((m) => ({ ...m, enabled }))
    ElMessage.success(enabled ? "已开启全部模块" : "已关闭全部模块")
  } catch (e) {
    ElMessage.error("批量操作失败：" + (e as Error).message)
  } finally {
    batching.value = false
  }
}

// ── 模块运行状态（轮询） ──
const fulilaiStatus = ref<FulilaiPoolStatus | null>(null)
const captchaStatus = ref<CaptchaStatus | null>(null)

// 两个状态并行拉取（原先串行 await）
async function loadStatus() {
  const [fulilai, captcha] = await Promise.allSettled([
    api.getModuleStatus<FulilaiPoolStatus>("fulilai"),
    api.getModuleStatus<CaptchaStatus>("captcha"),
  ])
  if (fulilai.status === "fulfilled") fulilaiStatus.value = fulilai.value
  if (captcha.status === "fulfilled") captchaStatus.value = captcha.value
}

// ── 直接领取关键词管理 ──
const keywords = ref<string[]>([])
const newKw = ref("")
const kwInputVisible = ref(false)
const kwInputRef = ref<InputInstance>()

async function loadKeywords() {
  try {
    const s = await api.getSettings()
    keywords.value = (s.direct_keywords || "领取")
      .split(",")
      .map((k) => k.trim())
      .filter(Boolean)
  } catch {
    /* ignore */
  }
}

async function saveKeywords() {
  try {
    await api.updateSettings({ direct_keywords: keywords.value.join(",") })
  } catch (e) {
    ElMessage.error("保存关键词失败：" + (e as Error).message)
  }
}

function removeKw(kw: string) {
  keywords.value = keywords.value.filter((k) => k !== kw)
  saveKeywords()
}

function showKwInput() {
  kwInputVisible.value = true
  nextTick(() => kwInputRef.value?.focus?.())
}

function confirmKw() {
  const val = newKw.value.trim()
  if (val && !keywords.value.includes(val)) {
    keywords.value.push(val)
    saveKeywords()
  }
  kwInputVisible.value = false
  newKw.value = ""
}

// ── 模块 ──
async function load() {
  try {
    modules.value = await api.getModules()
  } catch (e) {
    ElMessage.error("加载模块失败：" + (e as Error).message)
  } finally {
    loading.value = false
  }
}

async function onToggle(m: ModuleToggle, val: boolean) {
  busy.value = m.key
  try {
    const updated = await api.toggleModule(m.key, val)
    modules.value = modules.value.map((x) => (x.key === m.key ? updated : x))
    ElMessage.success(`${updated.label} ${val ? "已开启" : "已关闭"}`)
  } catch (e) {
    ElMessage.error("操作失败：" + (e as Error).message)
  } finally {
    busy.value = null
  }
}

// ── 模块独立配置弹窗 ──
const configVisible = ref(false)
const configModuleKey = ref("")
const configModuleLabel = ref("")
const configFields = ref<ModuleConfigField[]>([])
const configForm = ref<Record<string, string>>({})
const configLoading = ref(false)
const configSaving = ref(false)

async function openConfig(m: ModuleToggle) {
  configModuleKey.value = m.key
  configModuleLabel.value = m.label
  configLoading.value = true
  configVisible.value = true
  try {
    const fields = await api.getModuleConfig(m.key)
    configFields.value = fields
    const form: Record<string, string> = {}
    for (const f of fields) {
      form[f.key] = f.value
    }
    configForm.value = form
  } catch (e) {
    ElMessage.error("加载配置失败：" + (e as Error).message)
  } finally {
    configLoading.value = false
  }
}

async function saveConfig() {
  configSaving.value = true
  try {
    const fields = await api.updateModuleConfig(
      configModuleKey.value,
      configForm.value
    )
    configFields.value = fields
    for (const f of fields) {
      configForm.value[f.key] = f.value
    }
    ElMessage.success(`${configModuleLabel.value} 配置已保存`)
    configVisible.value = false
    loadStatus()
  } catch (e) {
    ElMessage.error("保存失败：" + (e as Error).message)
  } finally {
    configSaving.value = false
  }
}

onMounted(() => {
  load()
  loadKeywords()
})
// 状态轮询：隐藏页/切走时暂停，回到前台立即刷新（5s）
usePolling(loadStatus, 5000)
</script>

<template>
  <el-card shadow="never">
    <template #header>
      <div class="module-header">
        <div>
          <div class="card-title">红包模块开关</div>
          <div class="card-desc">
            控制各类红包的自动领取。关闭后该类型红包将被忽略。
            <el-tag size="small" effect="plain" class="enabled-stat">
              已开启 {{ enabledCount }}/{{ modules.length }}
            </el-tag>
          </div>
        </div>
        <div class="header-tools">
          <el-input
            v-model="search"
            :prefix-icon="Search"
            placeholder="搜索模块 / 钱包"
            clearable
            size="default"
            class="search-input"
          />
          <el-button
            type="success"
            plain
            :icon="Select"
            :loading="batching"
            @click="onBatch(true)"
          >
            一键全开
          </el-button>
          <el-button
            type="danger"
            plain
            :icon="CloseBold"
            :loading="batching"
            @click="onBatch(false)"
          >
            一键全关
          </el-button>
        </div>
      </div>
    </template>

    <el-skeleton v-if="loading" :rows="3" animated />

    <el-empty
      v-else-if="filteredModules.length === 0"
      :description="`未找到匹配「${search}」的模块`"
    />

    <div v-else class="module-table">
      <!-- 列标题 -->
      <div class="mod-grid mod-head">
        <div>模块</div>
        <div>适用钱包</div>
        <div>模块描述</div>
        <div class="col-center">配置</div>
        <div class="col-center">开关</div>
      </div>

      <div v-for="m in filteredModules" :key="m.key" class="mod-item">
        <div class="mod-grid mod-row">
          <!-- 模块名 -->
          <div class="cell-module">
            <div class="module-icon">
              <el-icon :size="18"><component :is="ICONS[m.key] ?? Key" /></el-icon>
            </div>
            <span class="module-label">{{ m.label }}</span>
          </div>

          <!-- 适用钱包 -->
          <div class="cell-wallets">
            <el-tag
              v-for="w in MODULE_META[m.key]?.wallets ?? []"
              :key="w"
              class="tag-wallet"
              size="small"
            >
              {{ WALLET_LABEL[w] || w }}
            </el-tag>
          </div>

          <!-- 模块描述 -->
          <div class="cell-desc">
            <div class="module-desc">{{ m.description }}</div>
            <div v-if="MODULE_META[m.key]" class="module-detail">
              {{ MODULE_META[m.key].detail }}
            </div>
          </div>

          <!-- 配置 -->
          <div class="cell-config col-center">
            <el-button
              v-if="HAS_CONFIG.has(m.key)"
              text
              size="small"
              :icon="Setting"
              @click="openConfig(m)"
            >
              配置
            </el-button>
          </div>

          <!-- 开关 -->
          <div class="cell-switch col-center">
            <el-switch
              :model-value="m.enabled"
              :loading="busy === m.key"
              :disabled="busy === m.key"
              @update:model-value="(v: boolean) => onToggle(m, v)"
            />
          </div>
        </div>

        <!-- 扩展信息：运行状态 / 关键词管理（全宽，不挤进列里） -->
        <div
          v-if="m.key === 'direct' || (m.key === 'fulilai' && fulilaiStatus) || (m.key === 'captcha' && captchaStatus)"
          class="mod-extra"
        >
          <!-- 福利来 token 池状态 -->
          <div v-if="m.key === 'fulilai' && fulilaiStatus" class="extra-block">
            <span class="extra-label">运行状态</span>
            <el-tag :type="fulilaiStatus.running ? 'success' : 'info'" size="small" effect="plain">
              池: {{ fulilaiStatus.running ? '运行中' : '已暂停' }}
            </el-tag>
            <el-tag size="small" effect="plain">
              可用 {{ fulilaiStatus.available }}/{{ fulilaiStatus.pool_size }}
            </el-tag>
            <el-tag v-if="fulilaiStatus.solving > 0" type="warning" size="small" effect="plain">
              解题中 {{ fulilaiStatus.solving }}
            </el-tag>
            <el-tag size="small" effect="plain" type="success">
              成功 {{ fulilaiStatus.total_solved }}
            </el-tag>
            <el-tag v-if="fulilaiStatus.total_failed > 0" size="small" effect="plain" type="danger">
              失败 {{ fulilaiStatus.total_failed }}
            </el-tag>
          </div>

          <!-- 验证码已识别字体数 -->
          <div v-if="m.key === 'captcha' && captchaStatus" class="extra-block">
            <span class="extra-label">运行状态</span>
            <el-tag size="small" effect="plain">
              已识别 {{ captchaStatus.glyph_count }} 种字体
            </el-tag>
          </div>

          <!-- 直接领取：关键词标签管理 -->
          <div v-if="m.key === 'direct'" class="extra-block">
            <span class="extra-label">识别关键词</span>
            <el-tag
              v-for="kw in keywords"
              :key="kw"
              closable
              class="kw-tag"
              @close="removeKw(kw)"
            >
              {{ kw }}
            </el-tag>
            <el-input
              v-if="kwInputVisible"
              ref="kwInputRef"
              v-model="newKw"
              size="small"
              class="kw-input"
              placeholder="输入关键词"
              @keyup.enter="confirmKw"
              @blur="confirmKw"
            />
            <el-button v-else size="small" class="kw-add-btn" @click="showKwInput">
              + 添加
            </el-button>
            <span class="kw-hint">按钮文字含任一关键词即触发（子串匹配）</span>
          </div>
        </div>
      </div>
    </div>
  </el-card>

  <!-- 模块配置弹窗 -->
  <el-dialog
    v-model="configVisible"
    :title="`${configModuleLabel} — 配置`"
    width="520"
    destroy-on-close
  >
    <el-skeleton v-if="configLoading" :rows="4" animated />
    <el-form v-else label-position="top" class="config-form">
      <el-form-item v-for="field in configFields" :key="field.key" :label="field.label">
        <!-- switch 类型 -->
        <template v-if="field.type === 'switch'">
          <el-switch
            :model-value="configForm[field.key] !== '0'"
            @update:model-value="(v: boolean) => (configForm[field.key] = v ? '1' : '0')"
          />
        </template>
        <!-- 密码类型 -->
        <el-input
          v-else-if="field.type === 'password'"
          v-model="configForm[field.key]"
          type="password"
          show-password
        />
        <!-- 文本类型 -->
        <el-input v-else v-model="configForm[field.key]" />
        <div class="hint">{{ field.hint }}</div>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="configVisible = false">取消</el-button>
      <el-button type="primary" :loading="configSaving" @click="saveConfig">
        保存
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.card-title {
  font-size: 16px;
  font-weight: 600;
}

.card-desc {
  margin-top: 4px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.enabled-stat {
  margin-left: 8px;
}

/* 头部工具栏 */
.module-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.header-tools {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.search-input {
  width: 200px;
}

/* 列式布局：模块 / 适用钱包 / 模块描述 / 配置 / 开关 */
.mod-grid {
  display: grid;
  grid-template-columns: 150px minmax(170px, 1.4fr) minmax(220px, 2.2fr) 90px 72px;
  gap: 16px;
  align-items: center;
}

.col-center {
  display: flex;
  justify-content: center;
  text-align: center;
}

.mod-head {
  padding: 0 0 12px;
  font-size: 12px;
  font-weight: 500;
  color: var(--el-text-color-secondary);
  border-bottom: 1px solid var(--el-border-color);
}

.mod-item {
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.mod-item:last-child {
  border-bottom: none;
}

.mod-row {
  padding: 14px 0;
}

.cell-module {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

/* 模块图标：统一黄色（分类色，与钱包蓝、状态绿/红区分开） */
.module-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: 2px solid #000;
  border-radius: 8px;
  box-shadow: var(--chunky-shadow-sm);
  background: var(--memphis-yellow);
  color: #1a1a1a;
  flex-shrink: 0;
}

.module-label {
  font-weight: 500;
}

.cell-wallets {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.cell-desc {
  min-width: 0;
}

.module-desc {
  font-size: 13px;
  color: var(--el-text-color-regular);
}

.module-detail {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
}

.muted {
  color: var(--el-text-color-placeholder);
}

/* 扩展行（运行状态 / 关键词，全宽不挤列） */
.mod-extra {
  padding: 0 0 14px 26px;
}

.extra-block {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.extra-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--el-text-color-regular);
  margin-right: 2px;
}

.kw-tag {
  font-size: 13px;
}

.kw-input {
  width: 110px;
}

.kw-add-btn {
  font-size: 12px;
}

.kw-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

/* 配置弹窗 */
.config-form {
  max-width: 480px;
}

.hint {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
}
</style>
