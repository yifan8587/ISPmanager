import { computed } from 'vue'
import { useAuthStore } from '@/stores/auth'
import {
  canAccessPermission,
  canManagePage,
  canViewPage,
  hasModuleManage,
  hasModuleView,
  userHasPermission,
} from '@/constants/permissions'

export function usePermission() {
  const auth = useAuthStore()
  const user = computed(() => auth.user)

  function can(code) {
    return userHasPermission(user.value, code)
  }

  function canAny(...codes) {
    if (user.value?.is_superuser) return true
    return codes.some((c) => userHasPermission(user.value, c))
  }

  function canViewModule(moduleKey) {
    return hasModuleView(user.value, moduleKey)
  }

  function canManageModule(moduleKey) {
    return hasModuleManage(user.value, moduleKey)
  }

  function canRoute(required) {
    return canAccessPermission(user.value, required)
  }

  function canPage(module, page) {
    return canViewPage(user.value, module, page)
  }

  function canManagePageAction(module, page) {
    return canManagePage(user.value, module, page)
  }

  function canEditSetting(key) {
    return userHasPermission(user.value, `system.setting.${key}.manage`)
  }

  return {
    user,
    can,
    canAny,
    canViewModule,
    canManageModule,
    canRoute,
    canPage,
    canManagePageAction,
    canEditSetting,
    isSuperuser: computed(() => !!user.value?.is_superuser),
    isCustomerAccount: computed(() => user.value?.account_type === 'customer'),
  }
}
