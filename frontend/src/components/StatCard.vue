<script setup lang="ts">
withDefaults(
  defineProps<{
    label: string
    value: string | number
    tone?: "primary" | "success" | "warning" | "danger" | "info"
    sub?: string
  }>(),
  { tone: "primary" }
)
</script>

<template>
  <div class="stat-card">
    <div class="sc-icon" :class="`sc-icon--${tone}`">
      <slot name="icon" />
    </div>
    <div class="sc-body">
      <div class="sc-value tnum">{{ value }}</div>
      <div class="sc-label">
        {{ label }}
        <span v-if="sub" class="sc-sub">{{ sub }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.stat-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 18px 20px;
  background: var(--el-bg-color);
  border: 2px solid #000;
  border-radius: var(--app-radius);
  box-shadow: var(--chunky-shadow);
}

.sc-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 46px;
  height: 46px;
  border: 2px solid #000;
  border-radius: 8px;
  flex-shrink: 0;
  font-size: 22px;
  box-shadow: var(--chunky-shadow-sm);
}

.sc-icon--primary {
  background: var(--memphis-orange);
  color: #fff;
}
.sc-icon--success {
  background: var(--memphis-mint);
  color: #14532d;
}
.sc-icon--warning {
  background: var(--memphis-yellow);
  color: #713f12;
}
.sc-icon--danger {
  background: var(--memphis-pink);
  color: #881337;
}
.sc-icon--info {
  background: var(--memphis-blue);
  color: #0c4a6e;
}

.sc-body {
  min-width: 0;
}

.sc-value {
  font-size: 24px;
  font-weight: 700;
  line-height: 1.1;
  letter-spacing: -0.01em;
}

.sc-label {
  margin-top: 3px;
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
}

.sc-sub {
  margin-left: 6px;
  color: var(--el-text-color-placeholder);
}
</style>
