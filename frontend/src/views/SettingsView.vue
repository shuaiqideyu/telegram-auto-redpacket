<script setup lang="ts">
import { onMounted, reactive, ref } from "vue"
import { Delete, Plus } from "@element-plus/icons-vue"

import { api } from "@/api"
import type { Settings } from "@/types"

// —— 基础配置 ——
const form = reactive<Pick<Settings, "max_attempts" | "notify_bot_token">>({
  max_attempts: "4",
  notify_bot_token: "",
})

// —— 领取策略过滤 ——
interface MinRule {
  currency: string
  min: string
}

const filterKeywords = ref("")
const currencyMode = ref<"off" | "white" | "black">("off")
const currencies = ref<string[]>([])
const minRules = ref<MinRule[]>([])
const skipConditions = ref<string[]>([])

const CURRENCY_SUGGESTIONS = ["USDT", "CNY", "KKCOIN", "WLCOIN", "JIBA", "TRX"]
// 与 core/detector.py CONDITION_LABELS 对齐（locked 是状态不是条件，不在此列）
const CONDITION_OPTIONS = [
  { value: "premium", label: "Premium会员" },
  { value: "group", label: "指定群组" },
  { value: "user", label: "指定用户" },
  { value: "turnover", label: "流水要求" },
  { value: "winloss", label: "输赢要求" },
]

const loading = ref(true)
const saving = ref(false)
const savingFilters = ref(false)

function applySettings(s: Settings) {
  form.max_attempts = s.max_attempts || "4"
  form.notify_bot_token = s.notify_bot_token || ""

  filterKeywords.value = s.filter_keywords || ""
  const mode = s.filter_currency_mode
  currencyMode.value = mode === "white" || mode === "black" ? mode : "off"
  currencies.value = (s.filter_currencies || "")
    .split(",")
    .map((c) => c.trim())
    .filter(Boolean)
  skipConditions.value = (s.filter_skip_conditions || "")
    .split(",")
    .map((c) => c.trim())
    .filter(Boolean)

  minRules.value = []
  if (s.filter_min_amounts) {
    try {
      const obj = JSON.parse(s.filter_min_amounts) as Record<string, unknown>
      for (const [cur, v] of Object.entries(obj)) {
        minRules.value.push({ currency: cur, min: String(v) })
      }
    } catch {
      // 历史坏数据按空规则处理，保存时会覆盖
    }
  }
}

async function load() {
  try {
    applySettings(await api.getSettings())
  } catch (e) {
    ElMessage.error("加载配置失败：" + (e as Error).message)
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  try {
    const payload: Partial<Settings> = {
      max_attempts: String(parseInt(form.max_attempts || "4", 10) || 4),
      notify_bot_token: form.notify_bot_token,
    }
    applySettings(await api.updateSettings(payload))
    ElMessage.success("配置已保存")
  } catch (e) {
    ElMessage.error("保存失败：" + (e as Error).message)
  } finally {
    saving.value = false
  }
}

function addMinRule() {
  minRules.value.push({ currency: "", min: "" })
}

function removeMinRule(idx: number) {
  minRules.value.splice(idx, 1)
}

/** 金额规则 → JSON 字符串；校验失败返回 null（已弹错误提示）。 */
function serializeMinRules(): string | null {
  const obj: Record<string, number> = {}
  for (let i = 0; i < minRules.value.length; i++) {
    const cur = minRules.value[i].currency.trim().toUpperCase()
    const minStr = minRules.value[i].min.trim()
    if (!cur && !minStr) continue // 整行空 → 忽略
    if (!cur) {
      ElMessage.error(`金额下限第 ${i + 1} 行未填币种（可用 * 表示任意币种）`)
      return null
    }
    const n = Number(minStr)
    if (!minStr || !Number.isFinite(n) || n < 0) {
      ElMessage.error(`金额下限第 ${i + 1} 行（${cur}）的金额无效`)
      return null
    }
    obj[cur] = n
  }
  return Object.keys(obj).length ? JSON.stringify(obj) : ""
}

async function saveFilters() {
  const minAmounts = serializeMinRules()
  if (minAmounts === null) return
  savingFilters.value = true
  try {
    const payload: Partial<Settings> = {
      filter_keywords: filterKeywords.value,
      filter_currency_mode: currencyMode.value,
      filter_currencies: currencies.value
        .map((c) => c.trim().toUpperCase())
        .filter(Boolean)
        .join(","),
      filter_min_amounts: minAmounts,
      filter_skip_conditions: skipConditions.value.join(","),
    }
    applySettings(await api.updateSettings(payload))
    ElMessage.success("领取策略已保存，对运行中账号实时生效")
  } catch (e) {
    ElMessage.error("保存失败：" + (e as Error).message)
  } finally {
    savingFilters.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="settings-page">
    <el-card shadow="never">
      <template #header>
        <div class="card-title">系统配置</div>
        <div class="card-desc">全局重试次数与通知机器人。各模块的 AI / 密钥配置请在「红包模块」中按模块单独设置。</div>
      </template>

      <el-skeleton v-if="loading" :rows="3" animated />

      <el-form v-else label-position="top" class="settings-form">
        <el-form-item label="最大重试次数">
          <el-input v-model="form.max_attempts" inputmode="numeric" />
          <div class="hint">单个红包未抢到时的重试上限。</div>
        </el-form-item>

        <el-form-item label="通知机器人 Token">
          <el-input v-model="form.notify_bot_token" type="password" show-password />
          <div class="hint">领取结果由该 bot 私聊推送给账号本人。</div>
        </el-form-item>

        <div class="form-footer">
          <el-button type="primary" :loading="saving" @click="save">
            保存配置
          </el-button>
        </div>
      </el-form>
    </el-card>

    <el-card shadow="never">
      <template #header>
        <div class="card-title">领取策略过滤</div>
        <div class="card-desc">
          不满足策略的红包直接跳过：不分发领取、不进广播频道、不消耗重试次数。
          四项均为可选、默认不启用；保存后对运行中账号实时生效，无需重启。
        </div>
      </template>

      <el-skeleton v-if="loading" :rows="6" animated />

      <el-form v-else label-position="top" class="settings-form">
        <el-form-item label="关键词黑名单">
          <el-input
            v-model="filterKeywords"
            type="textarea"
            :rows="5"
            placeholder="每行一个关键词，例如：&#10;练习用&#10;仅演示&#10;无效红包"
          />
          <div class="hint">红包消息文本命中任意关键词即跳过；匹配时自动忽略零宽字符等反爬干扰。</div>
        </el-form-item>

        <el-form-item label="币种过滤">
          <el-radio-group v-model="currencyMode">
            <el-radio-button value="off">不过滤</el-radio-button>
            <el-radio-button value="white">白名单</el-radio-button>
            <el-radio-button value="black">黑名单</el-radio-button>
          </el-radio-group>
          <el-select
            v-model="currencies"
            multiple
            filterable
            allow-create
            default-first-option
            :disabled="currencyMode === 'off'"
            placeholder="输入或选择币种，回车添加"
            class="currency-select"
          >
            <el-option v-for="c in CURRENCY_SUGGESTIONS" :key="c" :label="c" :value="c" />
          </el-select>
          <div class="hint">白名单 = 只抢所列币种；黑名单 = 跳过所列币种。未识别出币种的红包不受此项限制。</div>
        </el-form-item>

        <el-form-item label="金额下限（按币种）">
          <div class="min-rules">
            <div v-for="(row, i) in minRules" :key="i" class="min-rule-row">
              <el-input
                v-model="row.currency"
                placeholder="币种，如 KKCOIN（* = 任意币种）"
                class="rule-currency"
              />
              <el-input v-model="row.min" placeholder="最低总金额" inputmode="decimal" class="rule-min" />
              <el-button :icon="Delete" plain type="danger" @click="removeMinRule(i)" />
            </div>
            <el-button :icon="Plus" plain @click="addMinRule">添加规则</el-button>
          </div>
          <div class="hint">
            红包总金额低于下限时跳过，如 KKCOIN 填 100000 表示只抢十万以上的 KKCOIN 包；
            币种填 * 可对未单独设置的币种兜底。未识别出金额的红包不受影响。
          </div>
        </el-form-item>

        <el-form-item label="条件红包过滤">
          <el-checkbox-group v-model="skipConditions">
            <el-checkbox v-for="o in CONDITION_OPTIONS" :key="o.value" :value="o.value">
              {{ o.label }}
            </el-checkbox>
          </el-checkbox-group>
          <div class="hint">红包带有勾选的领取条件时跳过（如不满足「流水要求」时领取必然失败，提前跳过可减少无效请求与账号暴露）。</div>
        </el-form-item>

        <div class="form-footer">
          <el-button type="primary" :loading="savingFilters" @click="saveFilters">
            保存领取策略
          </el-button>
        </div>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.settings-page {
  display: flex;
  flex-direction: column;
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

.settings-form {
  max-width: 560px;
}

.hint {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
}

.form-footer {
  display: flex;
  justify-content: flex-end;
}

.currency-select {
  width: 100%;
  margin-top: 8px;
}

.min-rules {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}

.min-rule-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.rule-currency {
  flex: 1.4;
}

.rule-min {
  flex: 1;
}
</style>
