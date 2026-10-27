import request from './request'

// ── 产品模版 ──────────────────────────────────────────────
export const getTemplates = (params) => request.get('/product/templates/', { params })
export const getTemplatesBrief = (params) => request.get('/product/templates/brief/', { params })
export const createTemplate = (data) => request.post('/product/templates/', data)
export const updateTemplate = (id, data) => request.patch(`/product/templates/${id}/`, data)
export const deleteTemplate = (id) => request.delete(`/product/templates/${id}/`)

// ── 模版定价（按 POP） ────────────────────────────────────
export const getTemplatePricings = (params) => request.get('/product/template-pricings/', { params })
export const createTemplatePricing = (data) => request.post('/product/template-pricings/', data)
export const updateTemplatePricing = (id, data) => request.patch(`/product/template-pricings/${id}/`, data)
export const deleteTemplatePricing = (id) => request.delete(`/product/template-pricings/${id}/`)

// ── 产品 ──────────────────────────────────────────────────
export const getProductOsTemplates = (params) => request.get('/product/products/os-templates/', { params })
export const getProducts = (params) => request.get('/product/products/', { params })
export const getProductsBrief = (params) => request.get('/product/products/brief/', { params })
export const getProduct = (id) => request.get(`/product/products/${id}/`)
export const createProduct = (data) => request.post('/product/products/', data)
export const updateProduct = (id, data) => request.patch(`/product/products/${id}/`, data)
export const deleteProduct = (id) => request.delete(`/product/products/${id}/`)
export const getProductStatistics = () => request.get('/product/products/statistics/')
export const getProductCapacity = (id) => request.get(`/product/products/${id}/capacity/`)
export const getProductStock = (id) => request.get(`/product/products/${id}/stock/`)
export const quoteProduct = (id, data) => request.post(`/product/products/${id}/quote/`, data)
export const publishProduct = (id, data) => request.post(`/product/products/${id}/publish/`, data)
export const refreshProductNetwork = (id) => request.post(`/product/products/${id}/refresh-network/`)
export const getProductPublishLogs = (id) => request.get(`/product/products/${id}/publish-logs/`)
export const productFromTemplate = (data) => request.post('/product/products/from-template/', data)

export const getCountryShortcuts = (params) => request.get('/product/country-shortcuts/', { params })
export const createCountryShortcut = (data) => request.post('/product/country-shortcuts/', data)
export const updateCountryShortcut = (id, data) => request.patch(`/product/country-shortcuts/${id}/`, data)
export const deleteCountryShortcut = (id) => request.delete(`/product/country-shortcuts/${id}/`)

// ── 阶梯升级规则 ──────────────────────────────────────────
export const getUpgradeRules = (params) => request.get('/product/upgrade-rules/', { params })
export const createUpgradeRule = (data) => request.post('/product/upgrade-rules/', data)
export const updateUpgradeRule = (id, data) => request.patch(`/product/upgrade-rules/${id}/`, data)
export const deleteUpgradeRule = (id) => request.delete(`/product/upgrade-rules/${id}/`)
