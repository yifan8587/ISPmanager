import axios from 'axios'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import router from '@/router'
import { expireSession, isIdleExpired, shouldReportActivity } from '@/utils/idleSession'

const request = axios.create({
  baseURL: '/api',
  timeout: 15000
})

function isIdleResponse(data) {
  if (!data) return false
  if (data.code === 'idle_timeout') return true
  const text = `${data.message || ''} ${typeof data.detail === 'string' ? data.detail : ''}`
  return text.includes('登录已超时')
}

request.interceptors.request.use(
  (config) => {
    if (isIdleExpired()) {
      expireSession()
      return Promise.reject(new axios.Cancel('登录已超时'))
    }
    const token = localStorage.getItem('access_token')
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    if (shouldReportActivity()) {
      config.headers['X-User-Activity'] = '1'
    }
    return config
  },
  (error) => Promise.reject(error)
)

request.interceptors.response.use(
  (response) => response.data,
  async (error) => {
    const { response } = error
    if (response?.status === 401) {
      if (isIdleResponse(response.data)) {
        expireSession()
        return Promise.reject(error)
      }
      const refreshToken = localStorage.getItem('refresh_token')
      if (refreshToken) {
        try {
          const res = await axios.post('/api/auth/token/refresh/', { refresh: refreshToken })
          const newAccess = res.data.access
          localStorage.setItem('access_token', newAccess)
          error.config.headers.Authorization = `Bearer ${newAccess}`
          return request(error.config)
        } catch (refreshError) {
          if (isIdleResponse(refreshError?.response?.data)) {
            expireSession()
            return Promise.reject(error)
          }
          localStorage.removeItem('access_token')
          localStorage.removeItem('refresh_token')
          router.push('/login')
        }
      } else {
        router.push('/login')
      }
    } else if (response?.status >= 500) {
      ElMessage.error('服务器错误，请稍后重试')
    } else if (response?.status >= 400) {
      const data = response.data
      const detail = data?.detail
      let fieldMsg = ''
      if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
        const [key, val] = Object.entries(detail)[0] || []
        const msg = Array.isArray(val) ? val[0] : val
        if (key && msg) fieldMsg = `${key}：${msg}`
      }
      if (fieldMsg) ElMessage.error(fieldMsg)
      else if (data?.message) ElMessage.error(data.message)
    }
    return Promise.reject(error)
  }
)

export default request
