import request from './request'

// ── 供应商 ────────────────────────────────────────────────
export const getSuppliers = (params) => request.get('/supplier/suppliers/', { params })
export const getSuppliersBrief = () => request.get('/supplier/suppliers/brief/')
export const getSupplier = (id) => request.get(`/supplier/suppliers/${id}/`)
export const createSupplier = (data) => request.post('/supplier/suppliers/', data)
export const updateSupplier = (id, data) => request.patch(`/supplier/suppliers/${id}/`, data)
export const deleteSupplier = (id) => request.delete(`/supplier/suppliers/${id}/`)
export const getSupplierStatistics = () => request.get('/supplier/suppliers/statistics/')
export const getSupplierResources = (id, params) => request.get(`/supplier/suppliers/${id}/resources/`, { params })
export const getSupplierSyncLogs = (id) => request.get(`/supplier/suppliers/${id}/sync-logs/`)

// ── 供应商 API ────────────────────────────────────────────
export const getSupplierAPIs = (params) => request.get('/supplier/apis/', { params })
export const createSupplierAPI = (data) => request.post('/supplier/apis/', data)
export const updateSupplierAPI = (id, data) => request.patch(`/supplier/apis/${id}/`, data)
export const deleteSupplierAPI = (id) => request.delete(`/supplier/apis/${id}/`)
export const testSupplierAPI = (id) => request.post(`/supplier/apis/${id}/test/`, {}, { timeout: 0 })
export const pullSupplierResources = (id, data) =>
  request.post(`/supplier/apis/${id}/pull-resources/`, data, { timeout: 0 })

// ── 供应商 POP 关联 ───────────────────────────────────────
export const getPopBindings = (params) => request.get('/supplier/pop-bindings/', { params })
export const createPopBinding = (data) => request.post('/supplier/pop-bindings/', data)
export const updatePopBinding = (id, data) => request.patch(`/supplier/pop-bindings/${id}/`, data)
export const deletePopBinding = (id) => request.delete(`/supplier/pop-bindings/${id}/`)

// ── 供应商资源 ────────────────────────────────────────────
export const getResources = (params) => request.get('/supplier/resources/', { params })
export const updateResource = (id, data) => request.patch(`/supplier/resources/${id}/`, data)
export const deleteResource = (id) => request.delete(`/supplier/resources/${id}/`)
export const mapResource = (id, data) => request.post(`/supplier/resources/${id}/map/`, data)

// ── 同步日志 ──────────────────────────────────────────────
export const getSyncLogs = (params) => request.get('/supplier/sync-logs/', { params })

// ── 供应商账号 ────────────────────────────────────────────
export const getSupplierAccounts = (id) => request.get(`/supplier/suppliers/${id}/accounts/`)
export const createSupplierAccount = (id, data) => request.post(`/supplier/suppliers/${id}/accounts/`, data)
export const bindSupplierAccount = (id, data) => request.post(`/supplier/suppliers/${id}/accounts/bind/`, data)
export const unbindSupplierAccount = (id, data) => request.post(`/supplier/suppliers/${id}/accounts/unbind/`, data)
export const updateSupplierAccount = (id, data) => request.patch(`/supplier/accounts/${id}/update-account/`, data)
export const resetSupplierAccountPassword = (id, data) => request.post(`/supplier/accounts/${id}/reset-password/`, data)
export const disableSupplierAccount = (id) => request.post(`/supplier/accounts/${id}/disable/`)

// ── 账单 / 结算 ───────────────────────────────────────────
export const getSupplierBills = (params) => request.get('/supplier/bills/', { params })
export const getSupplierBillSummary = (params) => request.get('/supplier/bills/summary/', { params })
export const getSupplierFinanceSummary = (id) => request.get(`/supplier/suppliers/${id}/finance-summary/`)
export const getSupplierSettlements = (params) => request.get('/supplier/settlements/', { params })
export const requestSupplierSettlement = (data) => request.post('/supplier/settlements/request/', data)
export const paySupplierSettlement = (id, data) => request.post(`/supplier/settlements/${id}/pay/`, data)
export const cancelSupplierSettlement = (id) => request.post(`/supplier/settlements/${id}/cancel/`)
