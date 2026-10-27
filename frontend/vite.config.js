import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

// 生产环境挂在 https://站点/manager/ ；开发仍用 /
function resolveBase() {
  if (process.env.NODE_ENV !== 'production') return '/'
  const raw = process.env.VITE_BASE_PATH || '/manager/'
  return raw.endsWith('/') ? raw : `${raw}/`
}

export default defineConfig({
  base: resolveBase(),
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src')
    }
  },
  // noVNC 1.7+ 在 core/util/browser.js 使用 top-level await，需 ES2022 目标
  build: {
    target: 'es2022'
  },
  esbuild: {
    target: 'es2022'
  },
  optimizeDeps: {
    include: [
      'vue',
      'vue-router',
      'pinia',
      'axios',
      'element-plus',
      'element-plus/es/locale/lang/zh-cn',
      '@element-plus/icons-vue',
    ],
    esbuildOptions: {
      target: 'es2022',
      supported: { 'top-level-await': true }
    }
  },
  server: {
    host: '0.0.0.0',
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://192.168.1.65:8000',
        changeOrigin: true
      },
      // WebSSH 终端 WebSocket
      '/ws': {
        target: 'http://192.168.1.65:8000',
        changeOrigin: true,
        ws: true
      }
    }
  }
})
