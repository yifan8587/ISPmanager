import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { login as apiLogin, getMe } from '@/api/auth'
import { getMyPermissions } from '@/api/accounts'
import { hasModuleManage, hasModuleView, userHasPermission } from '@/constants/permissions'
import router from '@/router'
import { clearActivity, markActivity } from '@/utils/idleSession'

export const useAuthStore = defineStore('auth', () => {
  const user = ref(JSON.parse(localStorage.getItem('user') || 'null'))
  const accessToken = ref(localStorage.getItem('access_token') || '')
  const refreshToken = ref(localStorage.getItem('refresh_token') || '')

  const isLoggedIn = computed(() => !!accessToken.value)
  const isAdmin = computed(() =>
    user.value?.is_superuser ||
    user.value?.is_staff ||
    user.value?.permissions?.includes('accounts.manage')
  )

  function hasPermission(code) {
    return userHasPermission(user.value, code)
  }

  function hasAnyPermission(...codes) {
    if (user.value?.is_superuser) return true
    return codes.some((c) => userHasPermission(user.value, c))
  }

  function canViewModule(moduleKey) {
    return hasModuleView(user.value, moduleKey)
  }

  function canManageModule(moduleKey) {
    return hasModuleManage(user.value, moduleKey)
  }

  async function refreshPermissions() {
    try {
      const res = await getMyPermissions()
      const data = res?.data || res
      user.value = { ...user.value, ...data }
      localStorage.setItem('user', JSON.stringify(user.value))
    } catch {
      await fetchMe()
    }
  }

  async function login(username, password) {
    const res = await apiLogin({ username, password })
    const payload = res?.data || res
    const { access, refresh, user: userInfo } = payload
    if (!access) {
      throw new Error(res?.message || '登录成功但未返回令牌，请刷新页面后重试')
    }
    accessToken.value = access
    refreshToken.value = refresh || ''
    user.value = userInfo || null
    localStorage.setItem('access_token', access)
    localStorage.setItem('refresh_token', refresh || '')
    localStorage.setItem('user', JSON.stringify(userInfo || null))
    markActivity()
    return res
  }

  async function fetchMe() {
    try {
      const res = await getMe()
      user.value = res
      localStorage.setItem('user', JSON.stringify(res))
    } catch {}
  }

  function logout(options = {}) {
    accessToken.value = ''
    refreshToken.value = ''
    user.value = null
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    localStorage.removeItem('user')
    clearActivity()
    if (router.currentRoute.value.path === '/login') return
    router.push(options.expired ? { path: '/login', query: { expired: '1' } } : '/login')
  }

  return {
    user, accessToken, refreshToken, isLoggedIn, isAdmin,
    hasPermission, hasAnyPermission, canViewModule, canManageModule,
    login, fetchMe, refreshPermissions, logout,
  }
})
