/** 与后端 accounts.role_permissions 保持一致 */

export const Perm = {
  ACCOUNTS_VIEW: 'accounts.view',
  ACCOUNTS_MANAGE: 'accounts.manage',
  CUSTOMER_VIEW: 'customer.view',
  CUSTOMER_MANAGE: 'customer.manage',
  BILLING_VIEW: 'billing.view',
  BILLING_MANAGE: 'billing.manage',
  RESOURCE_VIEW: 'resource.view',
  RESOURCE_MANAGE: 'resource.manage',
  SERVICE_VIEW: 'service.view',
  SERVICE_MANAGE: 'service.manage',
  PRODUCT_VIEW: 'product.view',
  PRODUCT_MANAGE: 'product.manage',
  SUPPLIER_VIEW: 'supplier.view',
  SUPPLIER_MANAGE: 'supplier.manage',
  OPERATION_VIEW: 'operation.view',
  OPERATION_MANAGE: 'operation.manage',
  SYSTEM_VIEW: 'system.view',
  SYSTEM_MANAGE: 'system.manage',
}

/** 模块 → 查看/管理权限码 */
export const MODULE_PERMISSIONS = {
  accounts: { view: Perm.ACCOUNTS_VIEW, manage: Perm.ACCOUNTS_MANAGE, label: '账号管理' },
  customer: { view: Perm.CUSTOMER_VIEW, manage: Perm.CUSTOMER_MANAGE, label: '客户管理' },
  billing: { view: Perm.BILLING_VIEW, manage: Perm.BILLING_MANAGE, label: '账单管理' },
  resource: { view: Perm.RESOURCE_VIEW, manage: Perm.RESOURCE_MANAGE, label: '资源管理' },
  service: { view: Perm.SERVICE_VIEW, manage: Perm.SERVICE_MANAGE, label: '服务管理' },
  product: { view: Perm.PRODUCT_VIEW, manage: Perm.PRODUCT_MANAGE, label: '产品管理' },
  supplier: { view: Perm.SUPPLIER_VIEW, manage: Perm.SUPPLIER_MANAGE, label: '供应商管理' },
  operation: { view: Perm.OPERATION_VIEW, manage: Perm.OPERATION_MANAGE, label: '运维管理' },
  system: { view: Perm.SYSTEM_VIEW, manage: Perm.SYSTEM_MANAGE, label: '系统设置' },
}

/** 与后端 ROLE_LABELS / UserProfile.Role 保持一致 */
export const ROLE_LABELS = {
  superadmin: '超级管理员',
  admin: '管理员',
  customer_manager: '客户管理员',
  operator: '运维人员',
  sales: '销售人员',
  customer: '客户用户',
  supplier: '供应商',
  channel: '渠道',
  readonly: '只读用户',
}

/** 归属客户账号允许的角色 */
export const CUSTOMER_ACCOUNT_ALLOWED_ROLES = ['customer', 'readonly']
export const CUSTOMER_ACCOUNT_DEFAULT_ROLE = 'customer'

/** 路由 → 页面级权限码（view） */
export const ROUTE_PAGE_PERMISSIONS = {
  '/system/users': 'accounts.user.manage',
  '/system/roles': 'accounts.role.manage',
  '/customer/list': 'customer.list.view',
  '/customer/channels': 'customer.channel.view',
  '/customer/contracts': 'customer.contract.view',
  '/customer/billings': 'billing.list.view',
  '/resource/pop': 'resource.pop.view',
  '/resource/ip': 'resource.ip.view',
  '/resource/vps': 'resource.vps.view',
  '/resource/edge': 'resource.edge.view',
  '/resource/bandwidth': 'resource.bandwidth.view',
  '/operation/monitoring/hosts': 'operation.monitor_hosts.view',
  '/operation/monitoring/pop-quality': 'operation.pop_quality.view',
  '/operation/monitoring/traffic': 'operation.traffic.view',
  '/operation/monitoring/profiles': 'operation.traffic_profile.manage',
  '/operation/monitoring/zabbix': 'operation.zabbix.manage',
  '/operation/tools/webssh': 'operation.webssh.manage',
  '/operation/tools/templates': 'operation.template.manage',
  '/service/overview': 'service.overview.view',
  '/service/orders': 'service.order.view',
  '/service/inquiries': 'service.inquiry.view',
  '/service/tickets': 'service.ticket.view',
  '/service/self-service': 'service.selfservice.view',
  '/service/instances': 'service.instance.view',
  '/service/changes': 'service.change.view',
  '/product/templates': 'product.template.view',
  '/product/products': 'product.product.view',
  '/product/quick-countries': 'product.product.view',
  '/supplier/list': 'supplier.list.view',
  '/supplier/resources': 'supplier.resource.view',
  '/supplier/bills': 'supplier.billing.view',
  '/system/settings': 'system.settings.view',
  '/system/notifications': 'system.notify.view',
  '/system/logs': 'system.log.view',
  '/system/maintenance': 'system.maintenance.view',
  '/system/os-templates': 'system.os_template.view',
}

export const SETTING_KEY_CATEGORIES = {
  site_name: 'general',
  company_name: 'general',
  support_email: 'general',
  support_phone: 'general',
  default_credit_limit: 'billing',
  billing_due_days: 'billing',
  auto_suspend_overdue: 'billing',
  default_currency: 'billing',
  exchange_rate_usd_cny: 'billing',
  exchange_rate_eur_cny: 'billing',
  exchange_rate_gbp_cny: 'billing',
  exchange_rate_hkd_cny: 'billing',
  exchange_rate_jpy_cny: 'billing',
  exchange_rate_aud_cny: 'billing',
  exchange_rate_sgd_cny: 'billing',
  credit_rate_cny: 'billing',
  password_min_length: 'security',
  session_timeout_hours: 'security',
  enable_customer_portal: 'security',
  notify_billing_overdue: 'notification',
  notify_email_enabled: 'notification',
  notify_im_enabled: 'notification',
  notify_email_recipients: 'notification',
  notify_email_from: 'notification',
  smtp_host: 'notification',
  smtp_port: 'notification',
  smtp_user: 'notification',
  smtp_password: 'notification',
  smtp_use_ssl: 'notification',
  smtp_use_tls: 'notification',
  change_followup_ticket: 'billing',
  zabbix_default_sync_interval: 'integration',
  support_sla_hours: 'support',
  support_sla_notify_enabled: 'support',
  support_im_timeout_minutes: 'support',
  vps_snapshot_retain_count: 'billing',
}

export const ACTION_LABELS = {
  view: '查看',
  manage: '管理',
}

export const ACTION_META = {
  view: {
    key: 'view',
    label: '查看',
    short_label: '查',
    description: '只读：可浏览列表与详情，不可新增、修改、删除或执行操作',
  },
  manage: {
    key: 'manage',
    label: '管理',
    short_label: '管',
    description: '写操作：新增、修改、删除、审批、下发、执行等高风险操作',
  },
}

export function pagePermission(module, page, action = 'view') {
  return `${module}.${page}.${action}`
}

export function settingKeyPermission(key, action = 'manage') {
  return `system.setting.${key}.${action}`
}

export function settingCategoryPermission(category, action = 'manage') {
  return `system.settings.${category}.${action}`
}

export function moduleFromPermission(code) {
  if (!code || !code.includes('.')) return null
  return code.split('.')[0]
}

export function actionFromPermission(code) {
  if (!code || !code.includes('.')) return null
  return code.split('.').at(-1)
}

/** 判断 held 权限是否满足 required（与后端 permission_satisfies 一致） */
export function permissionSatisfies(held, required) {
  if (!held || !required) return false
  if (held === required) return true
  if (required.endsWith('.view') && held === `${required.slice(0, -5)}.manage`) return true

  const h = held.split('.')
  const r = required.split('.')

  if (h.length === 2 && r.length >= 3 && h[0] === r[0] && h[1] === r.at(-1)) return true
  if (h.length === 3 && r.length === 2 && h[0] === r[0] && h[2] === r[1]) return true
  if (h.length === 3 && h[0] === 'system' && h[1] === 'settings' && r.length === 4) {
    if (r[1] === 'settings' && h[2] === r[3]) return true
    if (r[1] === 'setting' && h[2] === r[3]) return true
  }
  if (h.length === 4 && h[1] === 'settings' && r.length === 4 && r[1] === 'setting') {
    const cat = SETTING_KEY_CATEGORIES[r[2]]
    return cat === h[2] && h[3] === r[3]
  }
  return false
}

export function userHasPermission(user, required) {
  if (!required) return true
  if (user?.is_superuser) return true
  const perms = user?.permissions || []
  return perms.some((held) => permissionSatisfies(held, required))
}

/** 路由访问：manage 权限也可进入 view 路由 */
export function canAccessPermission(user, required) {
  return userHasPermission(user, required)
}

export function hasModuleView(user, moduleKey) {
  const mod = MODULE_PERMISSIONS[moduleKey]
  if (!mod) return false
  return canAccessPermission(user, mod.view)
}

export function hasModuleManage(user, moduleKey) {
  if (user?.is_superuser) return true
  const mod = MODULE_PERMISSIONS[moduleKey]
  if (!mod) return false
  return userHasPermission(user, mod.manage)
}

export function canViewPage(user, module, page) {
  return userHasPermission(user, pagePermission(module, page, 'view'))
}

export function canManagePage(user, module, page) {
  return userHasPermission(user, pagePermission(module, page, 'manage'))
}

export function canManageSetting(user, key) {
  return userHasPermission(user, settingKeyPermission(key, 'manage'))
}

export function canViewSetting(user, key) {
  return userHasPermission(user, settingKeyPermission(key, 'view'))
}
