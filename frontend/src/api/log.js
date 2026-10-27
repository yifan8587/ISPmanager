import request from './request'

export const getAuditLogs = (params) => request.get('/log/audit-logs/', { params })
export const getAuditLog = (id) => request.get(`/log/audit-logs/${id}/`)
export const getAuditLogMeta = () => request.get('/log/audit-logs/meta/')
