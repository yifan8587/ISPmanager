import request from './request'

// 用户管理
export const getUsers = (params) => request.get('/accounts/users/', { params })
export const getUser = (id) => request.get(`/accounts/users/${id}/`)
export const createUser = (data) => request.post('/accounts/users/', data)
export const updateUser = (id, data) => request.patch(`/accounts/users/${id}/`, data)
export const deleteUser = (id) => request.delete(`/accounts/users/${id}/`)
export const resetPassword = (id, data) => request.post(`/accounts/users/${id}/reset_password/`, data)
export const toggleActive = (id, data) => request.post(`/accounts/users/${id}/toggle_active/`, data)

// 角色 / 权限
export const getRoleCatalog = () => request.get('/accounts/roles/')
export const getMyPermissions = () => request.get('/accounts/me/permissions/')
export const getRolePermissions = (role) => request.get(`/accounts/roles/${role}/permissions/`)
export const updateRolePermissions = (role, permissions) =>
  request.put(`/accounts/roles/${role}/permissions/`, { permissions })
export const resetPermissionDefaults = () => request.post('/accounts/permissions/reset-defaults/')
export const updatePermissionGroup = (key, data) => request.patch(`/accounts/permission-groups/${key}/`, data)
export const createPermissionPage = (data) => request.post('/accounts/permission-pages/', data)
export const updatePermissionPage = (groupKey, pageKey, data) =>
  request.patch(`/accounts/permission-pages/${groupKey}/${pageKey}/`, data)
export const updatePermissionDefinition = (code, data) => request.patch(`/accounts/permissions/${code}/`, data)
export const createCustomPermission = (data) => request.post('/accounts/permissions/', data)
export const deleteCustomPermission = (code) => request.delete(`/accounts/permissions/${code}/`)

// API Token 管理
export const getMyTokens = (params) => request.get('/accounts/tokens/', { params })
export const createToken = (data) => request.post('/accounts/tokens/', data)
export const getToken = (id) => request.get(`/accounts/tokens/${id}/`)
export const updateToken = (id, data) => request.patch(`/accounts/tokens/${id}/`, data)
export const deleteToken = (id) => request.delete(`/accounts/tokens/${id}/`)
export const revokeAllTokens = (data) => request.post('/accounts/tokens/revoke_all/', data || {})
export const verifyToken = (data) => request.post('/accounts/tokens/verify/', data)
export const getAllTokens = () => request.get('/accounts/admin/tokens/')
