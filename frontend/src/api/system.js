import request from './request'

export const getSystemOverview = () => request.get('/system/overview/')
export const getSystemSettings = (params) => request.get('/system/settings/', { params })
export const getSystemSettingsGrouped = () => request.get('/system/settings/grouped/')
export const getSystemSettingCategories = () => request.get('/system/settings/categories/')
export const getPublicSystemSettings = () => request.get('/system/settings/public/')
export const getCurrencies = () => request.get('/system/settings/currencies/')
export const updateSystemSetting = (id, data) => request.patch(`/system/settings/${id}/`, data)
export const bulkUpdateSystemSettings = (items) =>
  request.post('/system/settings/bulk_update/', { items })

// ── 在线支付渠道 ─────────────────────────────────────────────
export const getOsTemplates = (params) => request.get('/system/os-templates/', { params })
export const getOsTemplateTypes = () => request.get('/system/os-templates/os_types/')
export const createOsTemplate = (data) => request.post('/system/os-templates/', data)
export const updateOsTemplate = (id, data) => request.patch(`/system/os-templates/${id}/`, data)
export const deleteOsTemplate = (id) => request.delete(`/system/os-templates/${id}/`)
export const syncOsTemplatesFromPop = (data) => request.post('/system/os-templates/sync_from_pop/', data, { timeout: 0 })
export const seedOsTemplates = () => request.post('/system/os-templates/seed_defaults/')

export const getPaymentChannels = (params) => request.get('/system/payment-channels/', { params })
export const getPaymentProviderSchemas = () => request.get('/system/payment-channels/providers/')
export const createPaymentChannel = (data) => request.post('/system/payment-channels/', data)
export const updatePaymentChannel = (id, data) => request.patch(`/system/payment-channels/${id}/`, data)
export const deletePaymentChannel = (id) => request.delete(`/system/payment-channels/${id}/`)

// ── IM 通道 ───────────────────────────────────────────────
export const getIMChannels = (params) => request.get('/system/im-channels/', { params })
export const getIMChannelTypes = () => request.get('/system/im-channels/channel_types/')
export const createIMChannel = (data) => request.post('/system/im-channels/', data)
export const updateIMChannel = (id, data) => request.patch(`/system/im-channels/${id}/`, data)
export const deleteIMChannel = (id) => request.delete(`/system/im-channels/${id}/`)
export const testIMChannel = (id) => request.post(`/system/im-channels/${id}/test/`, {}, { timeout: 0 })
export const sendIMChannel = (id, data) => request.post(`/system/im-channels/${id}/send/`, data, { timeout: 0 })

// ── 通知规则 ──────────────────────────────────────────────
export const getNotificationRules = (params) => request.get('/system/notification-rules/', { params })
export const getNotificationEvents = () => request.get('/system/notification-rules/events/')
export const createNotificationRule = (data) => request.post('/system/notification-rules/', data)
export const updateNotificationRule = (id, data) => request.patch(`/system/notification-rules/${id}/`, data)
export const deleteNotificationRule = (id) => request.delete(`/system/notification-rules/${id}/`)
export const previewNotification = (data) => request.post('/system/notification-rules/preview/', data)
export const dispatchNotification = (data) => request.post('/system/notification-rules/dispatch/', data, { timeout: 0 })
export const testNotificationEmail = (data) => request.post('/system/notification-rules/test-email/', data || {}, { timeout: 0 })

// ── IM 发送日志 ───────────────────────────────────────────
export const getIMLogs = (params) => request.get('/system/im-logs/', { params })

// ── 系统版本 / 备份 / 升级 ────────────────────────────────
export const getSystemVersion = () => request.get('/system/version/')
export const getSystemBackups = (params) => request.get('/system/backups/', { params })
export const getSystemBackup = (id) => request.get(`/system/backups/${id}/`)
export const createSystemBackup = (data) => request.post('/system/backups/', data || {}, { timeout: 0 })
export const deleteSystemBackup = (id) => request.delete(`/system/backups/${id}/`)
export const restoreSystemBackup = (id, data) =>
  request.post(`/system/backups/${id}/restore/`, data || {}, { timeout: 0 })
export const uploadSystemBackup = (formData, onUploadProgress) =>
  request.post('/system/backups/upload/', formData, {
    timeout: 0,
    // 不要手写 multipart/form-data，否则缺少 boundary，后端收不到文件
    onUploadProgress,
  })

export const getSystemUpgrades = (params) => request.get('/system/upgrades/', { params })
export const getSystemUpgrade = (id) => request.get(`/system/upgrades/${id}/`)
export const uploadSystemUpgrade = (formData, onUploadProgress) =>
  request.post('/system/upgrades/upload/', formData, {
    timeout: 0,
    onUploadProgress,
  })
export const applySystemUpgrade = (id, data) =>
  request.post(`/system/upgrades/${id}/apply/`, data || {}, { timeout: 0 })
export const deleteSystemUpgrade = (id) => request.delete(`/system/upgrades/${id}/`)

export async function downloadSystemBackup(id, filename) {
  const token = localStorage.getItem('access_token')
  const axios = (await import('axios')).default
  const res = await axios.get(`/api/system/backups/${id}/download/`, {
    responseType: 'blob',
    timeout: 0,
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  const type = res.headers['content-type'] || ''
  if (type.includes('application/json')) {
    const text = await res.data.text()
    let detail = '下载失败'
    try { detail = JSON.parse(text).detail || detail } catch { /* ignore */ }
    throw new Error(detail)
  }
  const url = URL.createObjectURL(res.data)
  const a = document.createElement('a')
  a.href = url
  a.download = filename || 'ispmanager-backup.tar.gz'
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
