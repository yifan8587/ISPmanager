const FIELD_LABELS = {
  address: 'IP',
  status: '状态',
  assigned_vps_id: 'VPS',
  assigned_edge_id: '边缘',
  is_primary: '主IP',
  interface_code: '接口',
  node_route_ref: '节点路由',
  assigned_at: '分配时间',
  allocated_mbps: '带宽Mbps',
  target_type: '目标类型',
  vps_id: 'VPS',
  edge_id: '边缘',
  is_active: '有效',
  is_synced: '节点同步',
  bandwidth_mbps: '带宽Mbps',
  vcpu: 'vCPU',
  ram_mb: '内存MB',
  disk_gb: '硬盘GB',
}

export function formatLogDiff(oldValue, newValue) {
  const oldObj = oldValue && typeof oldValue === 'object' ? oldValue : null
  const newObj = newValue && typeof newValue === 'object' ? newValue : null
  if (!oldObj && !newObj) return ''
  const keys = [...new Set([
    ...Object.keys(oldObj || {}),
    ...Object.keys(newObj || {}),
  ])].filter((key) => key !== 'id' && key !== 'updated_at' && key !== 'created_at')
  const parts = []
  for (const key of keys) {
    const before = oldObj ? oldObj[key] : undefined
    const after = newObj ? newObj[key] : undefined
    if (JSON.stringify(before) === JSON.stringify(after)) continue
    const label = FIELD_LABELS[key] || key
    if (oldObj && newObj) {
      parts.push(`${label}: ${fmtVal(before)} → ${fmtVal(after)}`)
    } else if (newObj) {
      parts.push(`${label}: ${fmtVal(after)}`)
    } else {
      parts.push(`${label}: ${fmtVal(before)}`)
    }
  }
  return parts.join('；')
}

function fmtVal(value) {
  if (value === null || value === undefined || value === '') return '空'
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}
