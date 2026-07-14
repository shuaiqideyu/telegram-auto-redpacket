<script setup lang="ts">
import { onUnmounted, reactive, ref, watch } from "vue"
import { Iphone, Loading } from "@element-plus/icons-vue"
import QRCode from "qrcode"

import { api } from "@/api"
import type { Account } from "@/types"
import {
  COUNTRIES,
  DEFAULT_COUNTRY,
  flagEmoji,
  type CountryCode,
} from "@/lib/country-codes"

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  "update:modelValue": [boolean]
  success: [account?: Account]
}>()

const visible = ref(props.modelValue)
watch(
  () => props.modelValue,
  (v) => {
    visible.value = v
    if (v) resetAll()
  }
)
watch(visible, (v) => emit("update:modelValue", v))

const tab = ref("phone")

// ── 手机号 ──
type PhoneStep = "input" | "code" | "password"
const phone = reactive({
  step: "input" as PhoneStep,
  dial: DEFAULT_COUNTRY.dial,
  local: "",
  code: "",
  pw: "",
  loading: false,
})
const fullPhone = () => `+${phone.dial}${phone.local.replace(/\D/g, "")}`
// 锁定 tab 切换：手机号流程进入到 code/password 后不允许切 tab
const busy = () => phone.step !== "input"

// 区号下拉选项
const countryOptions = COUNTRIES.map((c: CountryCode) => ({
  value: c.dial,
  label: `${flagEmoji(c.iso)} ${c.nameZh} +${c.dial}`,
  keyword: `${c.nameZh} ${c.nameEn} ${c.dial} ${c.iso}`.toLowerCase(),
}))
const countrySearch = ref("")
const filterCountry = (query: string, item: { keyword: string }) =>
  item.keyword.includes(query.trim().toLowerCase())

// ── QR ──
type QrStep = "idle" | "scanning" | "password"
const qr = reactive({
  step: "idle" as QrStep,
  dataUrl: "",
  bindId: "",
  pw: "",
  loading: false,
  expired: false,
})
let pollTimer: ReturnType<typeof setTimeout> | null = null

// ── Session 导入 ──
type ImportStep = "input" | "loading" | "result"
const imp = reactive({
  step: "input" as ImportStep,
  text: "",
  loading: false,
})
const importResult = ref<{
  imported: number
  failed: number
  errors: { session: string; error: string }[]
} | null>(null)

function resetAll() {
  tab.value = "phone"
  phone.step = "input"
  phone.dial = DEFAULT_COUNTRY.dial
  phone.local = ""
  phone.code = ""
  phone.pw = ""
  phone.loading = false
  stopQrPoll()
  qr.step = "idle"
  qr.dataUrl = ""
  qr.bindId = ""
  qr.pw = ""
  qr.loading = false
  qr.expired = false
  imp.step = "input"
  imp.text = ""
  imp.loading = false
  importResult.value = null
}

function close() {
  visible.value = false
}

function finish(account?: Account) {
  ElMessage.success(`登录成功：${account?.name ?? "账号已添加"}`)
  resetAll()
  visible.value = false
  emit("success", account)
}

// ════════════════ 手机号登录 ════════════════
async function onPhone() {
  const digits = phone.local.replace(/\D/g, "")
  if (digits.length < 4) {
    ElMessage.error("请输入有效手机号")
    return
  }
  phone.loading = true
  try {
    await api.loginStart(fullPhone())
    phone.step = "code"
    ElMessage.info("验证码已发送，请查收 Telegram 登录验证码")
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    phone.loading = false
  }
}

async function onCode() {
  phone.loading = true
  try {
    const r = await api.loginCode(fullPhone(), phone.code.trim())
    if (r.needs_password) {
      phone.step = "password"
      ElMessage.info("需要两步验证，请输入二级密码")
    } else if (r.done) {
      finish(r.account)
    }
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    phone.loading = false
  }
}

async function onPhonePw() {
  phone.loading = true
  try {
    const r = await api.loginPassword(fullPhone(), phone.pw)
    if (r.done) finish(r.account)
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    phone.loading = false
  }
}

function phoneBack() {
  phone.step = "input"
  phone.code = ""
  phone.pw = ""
}

// ════════════════ QR 扫码登录 ════════════════
function stopQrPoll() {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

async function startQr() {
  qr.loading = true
  qr.expired = false
  qr.step = "scanning"
  try {
    const r = await api.qrStart()
    qr.bindId = r.bind_id
    qr.dataUrl = await QRCode.toDataURL(r.qr_url, {
      width: 240,
      margin: 2,
      color: { dark: "#000000", light: "#ffffff" },
    })
    qr.loading = false
    pollQr(r.bind_id)
  } catch (e) {
    ElMessage.error("QR 初始化失败：" + (e as Error).message)
    qr.step = "idle"
    qr.loading = false
  }
}

function pollQr(bindId: string) {
  const tick = async () => {
    try {
      const r = await api.qrPoll(bindId)
      if (r.done) return finish(r.account)
      if (r.needs_password) {
        qr.step = "password"
        return
      }
      if (r.expired) {
        qr.expired = true
        qr.step = "scanning"
        return
      }
      if (r.pending) pollTimer = setTimeout(tick, 2000)
    } catch {
      pollTimer = setTimeout(tick, 3000)
    }
  }
  tick()
}

async function onQrPw() {
  qr.loading = true
  try {
    const r = await api.qrPassword(qr.bindId, qr.pw)
    if (r.done) finish(r.account)
  } catch (e) {
    ElMessage.error("两步验证失败：" + (e as Error).message)
  } finally {
    qr.loading = false
  }
}

// ════════════════ Session 导入 ════════════════
async function onImport() {
  const sessions = imp.text
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean)
  if (sessions.length === 0) {
    ElMessage.error("请粘贴至少一条 StringSession")
    return
  }
  imp.step = "loading"
  imp.loading = true
  try {
    const r = await api.importSessions(sessions)
    importResult.value = {
      imported: r.imported,
      failed: r.failed,
      errors: r.errors,
    }
    imp.step = "result"
    if (r.imported > 0) {
      ElMessage.success(`成功导入 ${r.imported} 个账号`)
      emit("success")
    }
    if (r.failed > 0) ElMessage.warning(`${r.failed} 条 session 导入失败`)
  } catch (e) {
    ElMessage.error("导入失败：" + (e as Error).message)
    imp.step = "input"
  } finally {
    imp.loading = false
  }
}

function importAgain() {
  imp.step = "input"
  imp.text = ""
  importResult.value = null
}

onUnmounted(stopQrPoll)
</script>

<template>
  <el-dialog
    v-model="visible"
    title="导入 Telegram 账号"
    width="460px"
    :close-on-click-modal="false"
    @closed="resetAll"
  >
    <p class="dialog-desc">
      添加 Telegram 账号到系统，session 加密存储于云端数据库。
    </p>

    <el-tabs v-model="tab" class="login-tabs">
      <!-- ===== Tab 1: 手机号 ===== -->
      <el-tab-pane label="手机号" name="phone" :disabled="busy()">
        <div class="pane">
          <template v-if="phone.step === 'input'">
            <label class="field-label">手机号</label>
            <div class="phone-row">
              <el-select
                v-model="phone.dial"
                filterable
                :filter-method="(q: string) => (countrySearch = q)"
                class="dial-select"
                @visible-change="(v: boolean) => v || (countrySearch = '')"
              >
                <el-option
                  v-for="opt in countryOptions.filter((o) =>
                    filterCountry(countrySearch, o)
                  )"
                  :key="opt.value + opt.label"
                  :label="'+' + opt.value"
                  :value="opt.value"
                >
                  {{ opt.label }}
                </el-option>
              </el-select>
              <el-input
                v-model="phone.local"
                placeholder="请输入手机号"
                class="phone-input"
                @keyup.enter="phone.local && onPhone()"
              />
            </div>
            <div class="field-hint">
              完整号码：<span class="mono">{{ fullPhone() }}</span>
            </div>
          </template>

          <template v-else-if="phone.step === 'code'">
            <label class="field-label">验证码</label>
            <el-input
              v-model="phone.code"
              placeholder="Telegram 发来的 5 位数字"
              @keyup.enter="phone.code && onCode()"
            />
            <div class="field-hint">
              已向 <span class="mono">{{ fullPhone() }}</span> 发送登录验证码。
            </div>
          </template>

          <template v-else>
            <label class="field-label">两步验证密码</label>
            <el-input
              v-model="phone.pw"
              type="password"
              placeholder="二级密码"
              show-password
              @keyup.enter="phone.pw && onPhonePw()"
            />
            <div class="field-hint">账号开启了两步验证，需要二级密码。</div>
          </template>

          <div class="pane-footer">
            <el-button v-if="phone.step !== 'input'" @click="phoneBack">
              返回
            </el-button>
            <el-button
              v-if="phone.step === 'input'"
              type="primary"
              :loading="phone.loading"
              :disabled="!phone.local.replace(/\D/g, '')"
              @click="onPhone"
            >
              发送验证码
            </el-button>
            <el-button
              v-else-if="phone.step === 'code'"
              type="primary"
              :loading="phone.loading"
              :disabled="!phone.code.trim()"
              @click="onCode"
            >
              登录
            </el-button>
            <el-button
              v-else
              type="primary"
              :loading="phone.loading"
              :disabled="!phone.pw"
              @click="onPhonePw"
            >
              提交密码
            </el-button>
          </div>
        </div>
      </el-tab-pane>

      <!-- ===== Tab 2: 扫码 ===== -->
      <el-tab-pane label="扫码" name="qr" :disabled="busy()">
        <div class="pane qr-pane">
          <template v-if="qr.step === 'idle'">
            <el-icon :size="48" class="qr-placeholder-icon"><Iphone /></el-icon>
            <p class="field-hint center">
              点击下方按钮生成二维码，用 Telegram 手机端扫码登录
            </p>
            <el-button type="primary" :loading="qr.loading" @click="startQr">
              生成二维码
            </el-button>
          </template>

          <template v-else-if="qr.step === 'scanning'">
            <template v-if="qr.expired">
              <div class="qr-expired">二维码已过期</div>
              <el-button @click="startQr">重新生成</el-button>
            </template>
            <template v-else>
              <img v-if="qr.dataUrl" :src="qr.dataUrl" class="qr-img" />
              <el-icon v-else :size="32" class="is-loading"><Loading /></el-icon>
              <p class="field-hint center">
                打开 Telegram 手机端 → 设置 → 设备 → 扫描二维码
              </p>
            </template>
          </template>

          <template v-else>
            <label class="field-label">两步验证密码</label>
            <el-input
              v-model="qr.pw"
              type="password"
              placeholder="二级密码"
              show-password
              @keyup.enter="qr.pw && onQrPw()"
            />
            <div class="field-hint">扫码成功，账号开启了两步验证，请输入密码。</div>
            <div class="pane-footer">
              <el-button
                type="primary"
                :loading="qr.loading"
                :disabled="!qr.pw"
                @click="onQrPw"
              >
                提交密码
              </el-button>
            </div>
          </template>
        </div>
      </el-tab-pane>

      <!-- ===== Tab 3: 导入 ===== -->
      <el-tab-pane label="导入" name="session" :disabled="busy()">
        <div class="pane">
          <template v-if="imp.step === 'input'">
            <label class="field-label">StringSession</label>
            <el-input
              v-model="imp.text"
              type="textarea"
              :rows="6"
              placeholder="粘贴 Telethon StringSession，每行一条&#10;&#10;1BVtsOKxxxxxxx...&#10;1BVtsOKyyyyyyy..."
              class="mono-area"
            />
            <div class="field-hint">
              支持批量导入，每行一条 StringSession。系统会逐条验证有效性并加密存储。
            </div>
            <div class="pane-footer">
              <el-button
                type="primary"
                :loading="imp.loading"
                :disabled="!imp.text.trim()"
                @click="onImport"
              >
                导入
              </el-button>
            </div>
          </template>

          <template v-else-if="imp.step === 'loading'">
            <div class="qr-pane">
              <el-icon :size="32" class="is-loading"><Loading /></el-icon>
              <p class="field-hint center">正在验证并导入 Session...</p>
            </div>
          </template>

          <template v-else-if="importResult">
            <div class="import-stats">
              <div class="stat ok">
                <span class="stat-num">{{ importResult.imported }}</span>
                <span class="stat-label">成功</span>
              </div>
              <div v-if="importResult.failed > 0" class="stat fail">
                <span class="stat-num">{{ importResult.failed }}</span>
                <span class="stat-label">失败</span>
              </div>
            </div>
            <div v-if="importResult.errors.length" class="import-errors">
              <div
                v-for="(err, i) in importResult.errors"
                :key="i"
                class="err-line"
              >
                {{ err.session }}: {{ err.error }}
              </div>
            </div>
            <div class="pane-footer">
              <el-button @click="importAgain">继续导入</el-button>
              <el-button type="primary" @click="close">完成</el-button>
            </div>
          </template>
        </div>
      </el-tab-pane>
    </el-tabs>
  </el-dialog>
</template>

<style scoped>
.dialog-desc {
  margin: 0 0 8px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.pane {
  min-height: 180px;
  padding-top: 4px;
}

.field-label {
  display: block;
  margin-bottom: 6px;
  font-size: 13px;
  font-weight: 500;
}

.field-hint {
  margin-top: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.field-hint.center {
  text-align: center;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.phone-row {
  display: flex;
  gap: 8px;
}

.dial-select {
  width: 120px;
  flex-shrink: 0;
}

.phone-input {
  flex: 1;
}

.pane-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}

.qr-pane {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  min-height: 240px;
}

.qr-placeholder-icon {
  color: var(--el-text-color-secondary);
}

.qr-img {
  width: 200px;
  height: 200px;
  border-radius: 8px;
}

.qr-expired {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 200px;
  height: 200px;
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  color: var(--el-text-color-secondary);
}

.mono-area :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.import-stats {
  display: flex;
  gap: 24px;
  padding: 16px;
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
}

.stat {
  display: flex;
  flex-direction: column;
}

.stat-num {
  font-size: 24px;
  font-weight: 700;
}

.stat.ok .stat-num {
  color: var(--el-color-success);
}

.stat.fail .stat-num {
  color: var(--el-color-danger);
}

.stat-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.import-errors {
  max-height: 120px;
  overflow: auto;
  margin-top: 12px;
  padding: 12px;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}

.err-line {
  color: var(--el-color-danger);
}
</style>
