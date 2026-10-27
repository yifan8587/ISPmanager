import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import zhCn from 'element-plus/es/locale/lang/zh-cn'

import App from './App.vue'
import router from './router'
import { useAuthStore } from './stores/auth'
import { setExpireHandler, startIdleWatch } from './utils/idleSession'

// Vite 预构建缓存过期时动态 import 会 504，整页刷新一次即可恢复跳转
window.addEventListener('vite:preloadError', (event) => {
  event.preventDefault()
  const key = 'vite_dep_reload'
  const last = Number(sessionStorage.getItem(key) || 0)
  if (Date.now() - last < 8000) return
  sessionStorage.setItem(key, String(Date.now()))
  window.location.reload()
})

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(router)
setExpireHandler(() => {
  useAuthStore(pinia).logout({ expired: true })
})
startIdleWatch()
app.use(ElementPlus, { locale: zhCn })

for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

app.mount('#app')
