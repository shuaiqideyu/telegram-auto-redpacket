<script setup lang="ts">
defineProps<{ title: string; desc?: string }>()
</script>

<template>
  <div class="page-header">
    <div class="ph-text">
      <h2 class="ph-title">{{ title }}</h2>
      <p v-if="desc" class="ph-desc">{{ desc }}</p>
    </div>
    <div class="ph-actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped>
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: var(--app-gap);
  flex-wrap: wrap;
}

.ph-title {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
  line-height: 1.3;
  letter-spacing: -0.01em;
  /* 标题下的高亮笔触：Memphis 风格的标记感 */
  display: inline-block;
  background: linear-gradient(transparent 62%, var(--memphis-yellow) 62%, var(--memphis-yellow) 94%, transparent 94%);
  padding: 0 4px;
  margin-left: -4px;
}

.ph-desc {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
  max-width: 760px;
}

.ph-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
