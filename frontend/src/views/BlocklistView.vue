<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue"
import { Delete, Plus } from "@element-plus/icons-vue"

import { api } from "@/api"
import type { BlockList, BlockType } from "@/types"
import { BLOCK_TYPE_LABEL } from "@/types"
import PageHeader from "@/components/PageHeader.vue"

const data = ref<BlockList | null>(null)
const loading = ref(true)
const activeTab = ref<"all" | BlockType>("all")
const busy = ref<number | null>(null)

const TYPES: BlockType[] = ["group", "channel", "user", "bot"]

const rules = computed(() => {
  const list = data.value?.rules ?? []
  return activeTab.value === "all"
    ? list
    : list.filter((r) => r.target_type === activeTab.value)
})

function countOf(t: BlockType) {
  return data.value?.counts?.[t] ?? 0
}

async function load() {
  try {
    data.value = await api.getBlocklist()
  } catch (e) {
    ElMessage.error("加载屏蔽规则失败：" + (e as Error).message)
  } finally {
    loading.value = false
  }
}

async function togglePrivate(val: boolean) {
  try {
    await api.setBlockPrivate(val)
    if (data.value) data.value.block_private = val
    ElMessage.success(val ? "已屏蔽所有私信红包" : "已恢复私信红包")
  } catch (e) {
    ElMessage.error("操作失败：" + (e as Error).message)
  }
}

async function removeRule(id: number) {
  busy.value = id
  try {
    await api.removeBlockRule(id)
    await load()
    ElMessage.success("已移除屏蔽")
  } catch (e) {
    ElMessage.error("移除失败：" + (e as Error).message)
  } finally {
    busy.value = null
  }
}

// 添加弹窗
const dialogOpen = ref(false)
const adding = ref(false)
const form = reactive<{ target_type: BlockType; target_id: string; target_name: string; note: string }>({
  target_type: "group",
  target_id: "",
  target_name: "",
  note: "",
})

function openAdd() {
  form.target_type = activeTab.value === "all" ? "group" : activeTab.value
  form.target_id = ""
  form.target_name = ""
  form.note = ""
  dialogOpen.value = true
}

async function submitAdd() {
  const id = Number(form.target_id.trim())
  if (!Number.isFinite(id) || id === 0) {
    ElMessage.error("请输入有效的数字 ID（群/频道为负数，用户/机器人为正数）")
    return
  }
  adding.value = true
  try {
    await api.addBlockRule({
      target_type: form.target_type,
      target_id: id,
      target_name: form.target_name.trim() || null,
      note: form.note.trim() || null,
    })
    dialogOpen.value = false
    await load()
    ElMessage.success("已添加屏蔽")
  } catch (e) {
    ElMessage.error("添加失败：" + (e as Error).message)
  } finally {
    adding.value = false
  }
}

function fmtTime(iso: string | null) {
  if (!iso) return ""
  return new Date(iso).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
}

onMounted(load)
</script>

<template>
  <div>
    <PageHeader
      title="屏蔽管理"
      desc="命中屏蔽的红包来源会被直接忽略：不检测、不领取、不通知、不广播。保存后对运行中账号实时生效。"
    >
      <template #actions>
        <el-button type="primary" :icon="Plus" @click="openAdd">添加屏蔽</el-button>
      </template>
    </PageHeader>

    <el-card shadow="never" class="private-card">
      <div class="private-row">
        <div class="private-text">
          <div class="private-title">屏蔽所有私信红包</div>
          <div class="private-desc">开启后，任何来自私聊（bot 私信）的红包都不领取。</div>
        </div>
        <el-switch
          :model-value="data?.block_private ?? false"
          :loading="loading"
          @update:model-value="(v: boolean) => togglePrivate(v)"
        />
      </div>
    </el-card>

    <el-card shadow="never">
      <el-tabs v-model="activeTab">
        <el-tab-pane name="all">
          <template #label>全部 <span class="tab-count">{{ data?.rules.length ?? 0 }}</span></template>
        </el-tab-pane>
        <el-tab-pane v-for="t in TYPES" :key="t" :name="t">
          <template #label>{{ BLOCK_TYPE_LABEL[t] }} <span class="tab-count">{{ countOf(t) }}</span></template>
        </el-tab-pane>
      </el-tabs>

      <el-table
        v-loading="loading"
        :data="rules"
        style="width: 100%"
        empty-text="暂无屏蔽规则，点击右上角「添加屏蔽」"
      >
        <el-table-column label="类型" width="90">
          <template #default="{ row }">
            <el-tag size="small">
              {{ BLOCK_TYPE_LABEL[row.target_type as BlockType] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="目标 ID" width="180">
          <template #default="{ row }">
            <span class="mono">{{ row.target_id }}</span>
          </template>
        </el-table-column>
        <el-table-column label="名称" min-width="160">
          <template #default="{ row }">
            <span v-if="row.target_name">{{ row.target_name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="备注" min-width="180">
          <template #default="{ row }">
            <span v-if="row.note">{{ row.note }}</span>
          </template>
        </el-table-column>
        <el-table-column label="添加时间" width="140">
          <template #default="{ row }">
            <span class="muted">{{ fmtTime(row.created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="70" align="right">
          <template #default="{ row }">
            <el-button text :icon="Delete" :disabled="busy === row.id" @click="removeRule(row.id)" />
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="dialogOpen" title="添加屏蔽" width="460" destroy-on-close>
      <el-form label-position="top">
        <el-form-item label="类型">
          <el-radio-group v-model="form.target_type">
            <el-radio-button v-for="t in TYPES" :key="t" :value="t">{{ BLOCK_TYPE_LABEL[t] }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="目标 ID">
          <el-input v-model="form.target_id" placeholder="群/频道为负数（如 -100xxxx），用户/机器人为正数" />
          <div class="hint">
            群/频道 ID 可在「秒包管理」查看；用户/机器人需填数字 ID。
          </div>
        </el-form-item>
        <el-form-item label="名称（可选）">
          <el-input v-model="form.target_name" placeholder="便于辨识，如 某某福利群" />
        </el-form-item>
        <el-form-item label="备注（可选）">
          <el-input v-model="form.note" placeholder="屏蔽原因等" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="adding" @click="submitAdd">添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.private-card {
  margin-bottom: var(--app-gap);
  border-radius: var(--app-radius);
}

.private-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.private-title {
  font-weight: 600;
  font-size: 14px;
}

.private-desc {
  margin-top: 4px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.tab-count {
  display: inline-block;
  min-width: 18px;
  padding: 0 5px;
  margin-left: 2px;
  font-size: 11px;
  line-height: 16px;
  text-align: center;
  border-radius: 8px;
  background: var(--el-fill-color);
  color: var(--el-text-color-secondary);
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
}

.muted {
  color: var(--el-text-color-placeholder);
}

.hint {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
}
</style>
