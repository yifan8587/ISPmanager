import request from './request'

// ═══════════════════════════════════════════════════════════════
// 运维监控 API（对接 backend/operation_manager）
// ═══════════════════════════════════════════════════════════════

// ── Zabbix 服务器 ─────────────────────────────────────────────
export const getZabbixServers = (params) => request.get('/operation/zabbix-servers/', { params })
export const getZabbixServer = (id) => request.get(`/operation/zabbix-servers/${id}/`)
export const createZabbixServer = (data) => request.post('/operation/zabbix-servers/', data)
export const updateZabbixServer = (id, data) => request.patch(`/operation/zabbix-servers/${id}/`, data)
export const deleteZabbixServer = (id) => request.delete(`/operation/zabbix-servers/${id}/`)
export const testZabbixConnection = (id) =>
  request.post(`/operation/zabbix-servers/${id}/test-connection/`, {}, { timeout: 0 })
// 拉取 Zabbix 主机群组 / 模板（供新增监控主机时选择）
export const getZabbixHostGroups = (id, params) =>
  request.get(`/operation/zabbix-servers/${id}/host-groups/`, { params, timeout: 0 })
export const getZabbixTemplates = (id, params) =>
  request.get(`/operation/zabbix-servers/${id}/templates/`, { params, timeout: 0 })
// 同步该 Zabbix 服务器现有的主机 / 监控项 / 图形到本地
export const syncZabbixServer = (id) =>
  request.post(`/operation/zabbix-servers/${id}/sync/`, {}, { timeout: 0 })

// ── 图形 / 计费规则模板（95计费等） ───────────────────────────
export const getGraphProfiles = (params) => request.get('/operation/graph-profiles/', { params })
export const createGraphProfile = (data) => request.post('/operation/graph-profiles/', data)
export const updateGraphProfile = (id, data) => request.patch(`/operation/graph-profiles/${id}/`, data)
export const deleteGraphProfile = (id) => request.delete(`/operation/graph-profiles/${id}/`)

// ── Host 模板 ─────────────────────────────────────────────────
export const getHostTemplates = (params) => request.get('/operation/host-templates/', { params })
export const createHostTemplate = (data) => request.post('/operation/host-templates/', data)
export const updateHostTemplate = (id, data) => request.patch(`/operation/host-templates/${id}/`, data)
export const deleteHostTemplate = (id) => request.delete(`/operation/host-templates/${id}/`)

// ── 监控 Host（资源 ⇆ Zabbix 主机映射） ──────────────────────
export const getMonitoringHosts = (params) => request.get('/operation/monitoring-hosts/', { params })
export const getMonitoringHost = (id) => request.get(`/operation/monitoring-hosts/${id}/`)
export const createMonitoringHost = (data) => request.post('/operation/monitoring-hosts/', data)
export const updateMonitoringHost = (id, data) => request.patch(`/operation/monitoring-hosts/${id}/`, data)
export const deleteMonitoringHost = (id) => request.delete(`/operation/monitoring-hosts/${id}/`)
// 详情页监控面板（按 panel_slot 分组的图形 + 主要流量项）
export const getMonitoringPanel = (id) =>
  request.get(`/operation/monitoring-hosts/${id}/monitoring-panel/`)
// 将监控主机下发到 Zabbix 并同步 items/graphs
export const syncMonitoringHost = (id, data = { push: true }) =>
  request.post(`/operation/monitoring-hosts/${id}/sync/`, data, { timeout: 0 })

// ── POP 品质监控（管理 IP / 管理 URL 的 ICMP 与 HTTP 探测） ─────
export const getPopQualityMonitors = (params) =>
  request.get('/operation/pop-quality-monitors/', { params })
export const getPopQualityMonitor = (id) =>
  request.get(`/operation/pop-quality-monitors/${id}/`)
export const createPopQualityMonitor = (data) =>
  request.post('/operation/pop-quality-monitors/', data)
export const updatePopQualityMonitor = (id, data) =>
  request.patch(`/operation/pop-quality-monitors/${id}/`, data)
export const deletePopQualityMonitor = (id) =>
  request.delete(`/operation/pop-quality-monitors/${id}/`)
export const discoverPopQualityMonitors = () =>
  request.post('/operation/pop-quality-monitors/discover/', {}, { timeout: 0 })
export const probePopQualityMonitor = (id) =>
  request.post(`/operation/pop-quality-monitors/${id}/probe-now/`, {}, { timeout: 0 })
export const getPopQualityHistory = (id, params) =>
  request.get(`/operation/pop-quality-monitors/${id}/history/`, { params, timeout: 0 })

// ── 监控项 ────────────────────────────────────────────────────
export const getMonitoringItems = (params) => request.get('/operation/monitoring-items/', { params })
export const updateMonitoringItem = (id, data) => request.patch(`/operation/monitoring-items/${id}/`, data)
// 取监控项时序数据（资源详情页按监控项关联展示）
export const getItemChartData = (id, params) =>
  request.get(`/operation/monitoring-items/${id}/chart-data/`, { params, timeout: 0 })

// ── 监控图形（含定制图形） ─────────────────────────────────────
export const getMonitoringGraphs = (params) => request.get('/operation/monitoring-graphs/', { params })
export const getMonitoringGraph = (id) => request.get(`/operation/monitoring-graphs/${id}/`)
export const createMonitoringGraph = (data) => request.post('/operation/monitoring-graphs/', data)
export const updateMonitoringGraph = (id, data) => request.patch(`/operation/monitoring-graphs/${id}/`, data)
export const deleteMonitoringGraph = (id) => request.delete(`/operation/monitoring-graphs/${id}/`)
// 取图形时序/计费数据：params 支持 period 或 time_from/time_till
export const getGraphChartData = (id, params) =>
  request.get(`/operation/monitoring-graphs/${id}/chart-data/`, { params, timeout: 0 })

// ── 图形数据系列 ──────────────────────────────────────────────
export const getGraphSeries = (params) => request.get('/operation/graph-series/', { params })
export const createGraphSeries = (data) => request.post('/operation/graph-series/', data)
export const updateGraphSeries = (id, data) => request.patch(`/operation/graph-series/${id}/`, data)
export const deleteGraphSeries = (id) => request.delete(`/operation/graph-series/${id}/`)

// ── 图形绑定（详情页面板插槽） ─────────────────────────────────
export const getGraphBindings = (params) => request.get('/operation/graph-bindings/', { params })
export const createGraphBinding = (data) => request.post('/operation/graph-bindings/', data)
export const updateGraphBinding = (id, data) => request.patch(`/operation/graph-bindings/${id}/`, data)
export const deleteGraphBinding = (id) => request.delete(`/operation/graph-bindings/${id}/`)
// 资源详情页监控面板：按 (target_type, resource_id) 取绑定的图形/监控项卡片
export const getResourceMonitorPanel = (params) =>
  request.get('/operation/graph-bindings/resource-panel/', { params })

// ── 流量报告 / 95 计费 ────────────────────────────────────────
export const getTrafficReports = (params) => request.get('/operation/traffic-reports/', { params })
export const getTrafficReport = (id) => request.get(`/operation/traffic-reports/${id}/`)
export const deleteTrafficReport = (id) => request.delete(`/operation/traffic-reports/${id}/`)
export const archiveTrafficReport = (id) => request.post(`/operation/traffic-reports/${id}/archive/`)
export const getHostTrafficGraphs = (hostId) =>
  request.get('/operation/traffic-reports/host-graphs/', { params: { monitoring_host: hostId } })
// 按时间窗口截取接口流量并计算分位计费：data 见 TrafficQuerySerializer
export const queryTraffic = (data) =>
  request.post('/operation/traffic-reports/query/', data, { timeout: 0 })
export const getTrafficResourceContext = (params) =>
  request.get('/operation/traffic-reports/resource-context/', { params })
// 快捷 95 计费（自动持久化为报告）
export const computePercentile = (data) =>
  request.post('/operation/traffic-reports/percentile/', data, { timeout: 0 })

// ── 同步日志 ──────────────────────────────────────────────────
export const getSyncLogs = (params) => request.get('/operation/sync-logs/', { params })

// ── WebSSH ────────────────────────────────────────────────────
export const getWebSSHCommandTemplates = (params) =>
  request.get('/operation/webssh-templates/', { params })
export const getWebSSHCommandTemplate = (id) => request.get(`/operation/webssh-templates/${id}/`)
export const createWebSSHCommandTemplate = (data) => request.post('/operation/webssh-templates/', data)
export const updateWebSSHCommandTemplate = (id, data) =>
  request.patch(`/operation/webssh-templates/${id}/`, data)
export const deleteWebSSHCommandTemplate = (id) => request.delete(`/operation/webssh-templates/${id}/`)
export const resolveWebSSHTarget = (targetType, targetId) =>
  request.get('/operation/webssh/resolve/', { params: { target_type: targetType, target_id: targetId } })
export const operationWebSSHWebSocketURL = (targetType, targetId) => {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${window.location.host}/ws/operation/webssh/${targetType}/${targetId}/`
}

// 兼容旧接口名
export const getWebSSHTemplates = (params) => getWebSSHCommandTemplates({ ...params, active_only: 1 })
