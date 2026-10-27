import request from './request'

export const getRegions = (params) => request.get('/resource/regions/', { params })
export const createRegion = (data) => request.post('/resource/regions/', data)
export const updateRegion = (id, data) => request.patch(`/resource/regions/${id}/`, data)
export const deleteRegion = (id) => request.delete(`/resource/regions/${id}/`)

export const getCountries = (params) => request.get('/resource/countries/', { params })
export const createCountry = (data) => request.post('/resource/countries/', data)
export const updateCountry = (id, data) => request.patch(`/resource/countries/${id}/`, data)
export const deleteCountry = (id) => request.delete(`/resource/countries/${id}/`)

export const getCities = (params) => request.get('/resource/cities/', { params })
export const createCity = (data) => request.post('/resource/cities/', data)
export const updateCity = (id, data) => request.patch(`/resource/cities/${id}/`, data)
export const deleteCity = (id) => request.delete(`/resource/cities/${id}/`)

export const getAvailabilityZones = (params) => request.get('/resource/availability-zones/', { params })
export const createAvailabilityZone = (data) => request.post('/resource/availability-zones/', data)
export const updateAvailabilityZone = (id, data) => request.patch(`/resource/availability-zones/${id}/`, data)
export const deleteAvailabilityZone = (id) => request.delete(`/resource/availability-zones/${id}/`)

export const getCarriers = (params) => request.get('/resource/carriers/', { params })
export const createCarrier = (data) => request.post('/resource/carriers/', data)
export const updateCarrier = (id, data) => request.patch(`/resource/carriers/${id}/`, data)
export const deleteCarrier = (id) => request.delete(`/resource/carriers/${id}/`)

export const getNetworkTypes = (params) => request.get('/resource/network-types/', { params })
export const createNetworkType = (data) => request.post('/resource/network-types/', data)
export const updateNetworkType = (id, data) => request.patch(`/resource/network-types/${id}/`, data)
export const deleteNetworkType = (id) => request.delete(`/resource/network-types/${id}/`)

export const getPopPoints = (params) => request.get('/resource/pop-points/', { params })
export const getPopStatistics = () => request.get('/resource/pop-points/statistics/')
export const getPopMapOverview = () => request.get('/resource/pop-points/map-overview/')
export const createPopPoint = (data) => request.post('/resource/pop-points/', data)
export const updatePopPoint = (id, data) => request.patch(`/resource/pop-points/${id}/`, data)
export const deletePopPoint = (id) => request.delete(`/resource/pop-points/${id}/`)

export const getIPBlocks = (params) => request.get('/resource/ip-blocks/', { params })
export const createIPBlock = (data) => request.post('/resource/ip-blocks/', data)
export const updateIPBlock = (id, data) => request.patch(`/resource/ip-blocks/${id}/`, data)
export const deleteIPBlock = (id) => request.delete(`/resource/ip-blocks/${id}/`)
// 预览：查看 CIDR 会生成多少 IP（不写库）
export const previewBlockIPs = (id) => request.get(`/resource/ip-blocks/${id}/preview_ips/`)
// 正式解析并写入该地址块内所有 IP 地址
export const generateBlockIPs = (id, data) => request.post(`/resource/ip-blocks/${id}/generate_ips/`, data)

export const getIPAddresses = (params) => request.get('/resource/ip-addresses/', { params })
export const createIPAddress = (data) => request.post('/resource/ip-addresses/', data)
export const updateIPAddress = (id, data) => request.patch(`/resource/ip-addresses/${id}/`, data)
export const deleteIPAddress = (id) => request.delete(`/resource/ip-addresses/${id}/`)
// 分配 IP 给 VPS 或边缘加速：{ target_type, target_id, is_primary, push_to_node, create_route?, route? }
export const assignIPAddress = (id, data) =>
  request.post(`/resource/ip-addresses/${id}/assign/`, data, { timeout: 0 })
// 回收 IP（释放回可用状态，同步节点）
export const releaseIPAddress = (id, data = {}) =>
  request.post(`/resource/ip-addresses/${id}/release/`, data, { timeout: 0 })
// 批量录入 IP（对齐 vpop bulk-create）
export const bulkCreateIPs = (data) =>
  request.post('/resource/ip-addresses/bulk_create/', data, { timeout: 0 })
// IP 地址操作日志：过滤 target_model=IPAddress&target_id=<id>
export const getIPAddressLogs = (ipId, params) => request.get('/resource/operation-logs/', {
  params: { target_model: 'IPAddress', target_id: ipId, ...params }
})

// ── 隧道 IP 地址段（p2p 池）─────────────────────────────────
export const getTunnelIPPools = (params) => request.get('/resource/tunnel-ip-pools/', { params })
export const createTunnelIPPool = (data, pushToNode = true) =>
  request.post('/resource/tunnel-ip-pools/', data, { params: { push_to_node: pushToNode } })
export const updateTunnelIPPool = (id, data, pushToNode = true) =>
  request.patch(`/resource/tunnel-ip-pools/${id}/`, data, { params: { push_to_node: pushToNode } })
export const deleteTunnelIPPool = (id, pushToNode = true) =>
  request.delete(`/resource/tunnel-ip-pools/${id}/`, { params: { push_to_node: pushToNode } })
export const getTunnelIPPoolUsage = (id) => request.get(`/resource/tunnel-ip-pools/${id}/usage/`)
export const getTunnelIPPoolNextFree = (id) => request.get(`/resource/tunnel-ip-pools/${id}/next-free/`)
// 下发到所属 POP 节点；dryRun 默认 true 仅返回计划
export const pushTunnelIPPoolToNode = (id, dryRun = true) =>
  request.post(`/resource/tunnel-ip-pools/${id}/push_to_node/`, { dry_run: dryRun }, { timeout: 0 })

export const getVPS = (params) => request.get('/resource/vps/', { params })
export const getVPSDetail = (id) => request.get(`/resource/vps/${id}/`)
export const createVPS = (data) => request.post('/resource/vps/', data)
export const updateVPS = (id, data, pushToNode = false) =>
  request.patch(`/resource/vps/${id}/`, data, { params: pushToNode ? { push_to_node: true } : {} })
export const pushVPSBandwidth = (id) =>
  request.post(`/resource/vps/${id}/push_bandwidth/`, {}, { timeout: 0 })
export const deleteVPS = (id, deleteOnNode = false) => request.delete(`/resource/vps/${id}/`, {
  params: deleteOnNode ? { delete_on_node: true } : {},
  timeout: 0,
})
export const changeVPSStatus = (id, data) => request.post(`/resource/vps/${id}/change_status/`, data)
// PVE 生命周期：电源（start/stop/restart/shutdown）、克隆、即时同步
export const vpsPower = (id, action, dryRun = true) => request.post(`/resource/vps/${id}/power/`, { action, dry_run: dryRun }, { timeout: 0 })
export const cloneVPS = (id, data) => request.post(`/resource/vps/${id}/clone/`, data, { timeout: 0 })
export const syncVPSNow = (id) => request.post(`/resource/vps/${id}/sync_now/`, {}, { timeout: 0 })
export const getVPSStatistics = () => request.get('/resource/vps/statistics/')
export const getVPSBillingPreview = (id, productId) =>
  request.get(`/resource/vps/${id}/billing_preview/`, { params: productId ? { product: productId } : {} })
// noVNC 远程控制台：先取票据（触发节点 vncproxy），再用 ws_path 立即建立连接
export const getVPSVncTicket = (id) => request.get(`/resource/vps/${id}/vnc_ticket/`, { timeout: 0 })
export const vpsVncWebSocketURL = (id, { grant } = {}) => {
  // noVNC 子协议固定 binary，JWT 只能走 query。PVE 票据留在管理端，URL 里只带短编号。
  const token = localStorage.getItem('access_token') || ''
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const params = new URLSearchParams({ token })
  if (grant) params.set('grant', grant)
  return `${proto}://${window.location.host}/ws/resource/vnc/${id}/?${params.toString()}`
}
export const getVPSInterfaces = (params) => request.get('/resource/vps-interfaces/', { params })
export const createVPSInterface = (data) => request.post('/resource/vps-interfaces/', data)
export const updateVPSInterface = (id, data) => request.patch(`/resource/vps-interfaces/${id}/`, data)
export const deleteVPSInterface = (id) => request.delete(`/resource/vps-interfaces/${id}/`)

export const getEdgeAccelerations = (params) => request.get('/resource/edge-accelerations/', { params })
export const getEdgeDetail = (id) => request.get(`/resource/edge-accelerations/${id}/`)
export const createEdgeAcceleration = (data) => request.post('/resource/edge-accelerations/', data)
export const updateEdgeAcceleration = (id, data) => request.patch(`/resource/edge-accelerations/${id}/`, data)
export const deleteEdgeAcceleration = (id) => request.delete(`/resource/edge-accelerations/${id}/`)
export const changeEdgeStatus = (id, data) => request.post(`/resource/edge-accelerations/${id}/change_status/`, data)
export const updateEdgeProtocolConfig = (id, data) => request.patch(`/resource/edge-accelerations/${id}/protocol_config/`, data)
// 边缘隧道下发到节点（GRE/WireGuard）；dryRun 默认 true 仅返回计划
export const pushEdgeToNode = (id, dryRun = true) => request.post(`/resource/edge-accelerations/${id}/push_to_node/`, { dry_run: dryRun }, { timeout: 0 })
export const getEdgeStatistics = () => request.get('/resource/edge-accelerations/statistics/')
export const getEdgeBillingPreview = (id, productId) =>
  request.get(`/resource/edge-accelerations/${id}/billing_preview/`, { params: productId ? { product: productId } : {} })
// 通过所属 POP 节点生成 WireGuard 密钥对
export const genEdgeWgKey = (popPointId) => request.post('/resource/edge-accelerations/wg_genkey/', { pop_point: popPointId }, { timeout: 0 })

// 别名（EdgeList.vue 使用的简短名）
export const getEdge = getEdgeAccelerations
export const createEdge = createEdgeAcceleration
export const updateEdge = updateEdgeAcceleration
export const deleteEdge = deleteEdgeAcceleration

export const getBandwidthAllocations = (params) => request.get('/resource/bandwidth-allocations/', { params })
export const createBandwidthAllocation = (data, pushToNode = true) =>
  request.post('/resource/bandwidth-allocations/', data, { params: { push_to_node: pushToNode } })
export const updateBandwidthAllocation = (id, data, pushToNode = true) =>
  request.patch(`/resource/bandwidth-allocations/${id}/`, data, { params: { push_to_node: pushToNode } })
export const deleteBandwidthAllocation = (id, pushToNode = true) =>
  request.delete(`/resource/bandwidth-allocations/${id}/`, { params: { push_to_node: pushToNode } })
export const getBandwidthStatistics = (params) => request.get('/resource/bandwidth-allocations/statistics/', { params })

export const getInterfaces = (params) => request.get('/resource/interfaces/', { params })
export const createInterface = (data, pushToNode = false) =>
  request.post('/resource/interfaces/', data, { params: pushToNode ? { push_to_node: true } : {} })
export const updateInterface = (id, data, pushToNode = false) =>
  request.patch(`/resource/interfaces/${id}/`, data, { params: pushToNode ? { push_to_node: true } : {} })
export const pushInterfaceToNode = (id) =>
  request.post(`/resource/interfaces/${id}/push_to_node/`, {}, { timeout: 0 })
export const deleteInterface = (id, deleteOnNode = true) =>
  request.delete(`/resource/interfaces/${id}/`, { params: { delete_on_node: deleteOnNode } })

export const getPopPointDetail = (id) => request.get(`/resource/pop-points/${id}/`)
export const changePopPointStatus = (id, data) => request.post(`/resource/pop-points/${id}/change_status/`, data)
export const getPopPointLogs = (id, params) => request.get(`/resource/pop-points/${id}/logs/`, { params })
// 带宽管理
export const getPopPointBandwidth = (id) => request.get(`/resource/pop-points/${id}/bandwidth/`)
export const setPopPointBandwidth = (id, data) => request.patch(`/resource/pop-points/${id}/set_bandwidth/`, data)
// 资源调整完成后，重试该 POP 内此前因容量不足暂停的已付款自动交付订单
export const resumePaidProvisioning = (id) =>
  request.post(`/resource/pop-points/${id}/resume-paid-provisioning/`, {}, { timeout: 0 })

// ── POP 部署 / 节点数据同步 ──────────────────────────────────
// 测试 SSH 连通性（可传 {host,port,username,password} 覆盖）
export const testPopSSH = (id, data) => request.post(`/resource/pop-points/${id}/test_ssh/`, data)
// 上传部署包（multipart，字段 file）；onProgress 接收 0-100
export const uploadPopPackage = (id, file, onProgress) => {
  const fd = new FormData()
  fd.append('file', file)
  return request.post(`/resource/pop-points/${id}/upload_package/`, fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 0,
    onUploadProgress: (e) => {
      if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100))
    }
  })
}
// 设置部署状态：{status: deployed|failed|deploying|undeployed, agent_version?}
export const setPopDeployStatus = (id, data) => request.post(`/resource/pop-points/${id}/set_deploy_status/`, data)
// 从节点同步系统数据（接口/路由/策略/IP/带宽/PVE虚机）
export const syncPopFromNode = (id) => request.post(`/resource/pop-points/${id}/sync_from_node/`, {}, { timeout: 0 })
// 下发配置到节点：sections 可选（static_routes/policy_rules/edge_tunnels/bandwidth）；dryRun 默认 true 仅返回计划
export const pushPopToNode = (id, sections, dryRun = true) => request.post(
  `/resource/pop-points/${id}/push_to_node/`,
  { ...(sections ? { sections } : {}), dry_run: dryRun },
  { timeout: 0 },
)
// 节点 PVE 资源（主机/节点/模板），创建虚机时下拉用
export const getPopPVEResources = (id) => request.get(`/resource/pop-points/${id}/pve_resources/`, { timeout: 0 })
// 在节点创建虚机：data 含创建参数；from_template / dry_run 由 data 携带
export const createPopVM = (id, data) => request.post(`/resource/pop-points/${id}/create_vm/`, data, { timeout: 0 })
export const getPopPVEJob = (id, jobId) => request.get(`/resource/pop-points/${id}/pve_job/${jobId}/`, { timeout: 0 })

// WebSSH：JWT 走 Sec-WebSocket-Protocol（见 wsAuthProtocols），不再放入 query
export const popSSHWebSocketURL = (id) => {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${window.location.host}/ws/resource/pop-ssh/${id}/`
}

export const getRouteTables = (params) => request.get('/resource/route-tables/', { params })
export const createRouteTable = (data) => request.post('/resource/route-tables/', data)
export const updateRouteTable = (id, data) => request.patch(`/resource/route-tables/${id}/`, data)
export const deleteRouteTable = (id) => request.delete(`/resource/route-tables/${id}/`)

export const getStaticRoutes = (params) => request.get('/resource/static-routes/', { params })
export const createStaticRoute = (data, pushToNode = true) =>
  request.post('/resource/static-routes/', data, { params: { push_to_node: pushToNode } })
export const updateStaticRoute = (id, data, pushToNode = true) =>
  request.patch(`/resource/static-routes/${id}/`, data, { params: { push_to_node: pushToNode } })
export const deleteStaticRoute = (id, pushToNode = true) =>
  request.delete(`/resource/static-routes/${id}/`, { params: { push_to_node: pushToNode } })

export const getPolicyRoutes = (params) => request.get('/resource/policy-routes/', { params })
export const createPolicyRoute = (data, pushToNode = true) =>
  request.post('/resource/policy-routes/', data, { params: { push_to_node: pushToNode } })
export const updatePolicyRoute = (id, data, pushToNode = true) =>
  request.patch(`/resource/policy-routes/${id}/`, data, { params: { push_to_node: pushToNode } })
export const deletePolicyRoute = (id, pushToNode = true) =>
  request.delete(`/resource/policy-routes/${id}/`, { params: { push_to_node: pushToNode } })
