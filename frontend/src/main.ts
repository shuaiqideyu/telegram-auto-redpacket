import { createApp } from "vue"

// Memphis 主题（亮色，覆盖 Element Plus 变量与组件皮肤）
import "@/styles/main.css"
import "@/styles/memphis.css"

import App from "@/App.vue"
import { router } from "@/router"

createApp(App).use(router).mount("#app")
