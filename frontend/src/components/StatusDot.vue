<script setup lang="ts">
defineProps<{
  tone: "success" | "info" | "warning" | "danger" | "primary"
  label?: string
  pulse?: boolean
}>()
</script>

<template>
  <span class="status-dot">
    <span class="dot" :class="[`dot--${tone}`, { 'dot--pulse': pulse }]" />
    <span v-if="label" class="dot-label">{{ label }}</span>
  </span>
</template>

<style scoped>
.status-dot {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.dot {
  width: 9px;
  height: 9px;
  border: 1.5px solid #000;
  border-radius: 50%;
  flex-shrink: 0;
}

.dot--success {
  background: var(--el-color-success);
}
.dot--info {
  background: var(--el-color-info);
}
.dot--warning {
  background: var(--el-color-warning);
}
.dot--danger {
  background: var(--el-color-danger);
}
.dot--primary {
  background: var(--el-color-primary);
}

.dot--pulse {
  box-shadow: 0 0 0 0 currentColor;
  animation: dot-pulse 1.8s infinite;
}

.dot--success.dot--pulse {
  color: var(--el-color-success);
}

@keyframes dot-pulse {
  0% {
    box-shadow: 0 0 0 0 rgba(103, 194, 58, 0.5);
  }
  70% {
    box-shadow: 0 0 0 6px rgba(103, 194, 58, 0);
  }
  100% {
    box-shadow: 0 0 0 0 rgba(103, 194, 58, 0);
  }
}

.dot-label {
  font-size: 13px;
  color: var(--el-text-color-regular);
}
</style>
