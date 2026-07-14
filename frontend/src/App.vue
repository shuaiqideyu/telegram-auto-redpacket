<script setup lang="ts">
// 中文 locale 通过 ElConfigProvider 注入（按需导入下不再用 app.use(ElementPlus)）
import zhCn from "element-plus/es/locale/lang/zh-cn"
</script>

<template>
  <el-config-provider :locale="zhCn">
    <router-view />
  </el-config-provider>
</template>
