import { ElMessage, ElMessageBox } from 'element-plus'

/**
 * dry-run 预览 → 二次确认 → 正式下发
 * @param {() => Promise<{ok?: boolean, errors?: object, summary?: object}>} previewFn
 * @param {() => Promise<{ok?: boolean}>} pushFn
 * @param {{ title?: string, previewOk?: string, pushOk?: string, pushFail?: string }} labels
 */
export async function confirmAndPush(previewFn, pushFn, labels = {}) {
  const {
    title = '确认下发到节点',
    previewOk = '已生成下发计划',
    pushOk = '配置已下发到节点并生效',
    pushFail = '下发完成，部分失败',
  } = labels

  let preview
  try {
    preview = await previewFn()
  } catch (e) {
    ElMessage.error('预览失败：' + (e?.response?.data?.detail || e.message))
    return { ok: false, stage: 'preview' }
  }
  if (!preview?.ok) {
    const errs = preview?.errors ? Object.values(preview.errors).join('；') : ''
    ElMessage.warning('预览未通过：' + (errs || '详见返回结果'))
    return { ok: false, stage: 'preview', result: preview }
  }
  ElMessage.success(previewOk)
  try {
    await ElMessageBox.confirm(
      '预览已通过，将把配置真正写入 POP 节点并即时生效。确定继续？',
      title,
      { type: 'warning', confirmButtonText: '确认下发', cancelButtonText: '取消' },
    )
  } catch {
    return { ok: false, stage: 'cancel', result: preview }
  }
  try {
    const res = await pushFn()
    if (res?.ok && res?.apply_ok !== false) {
      ElMessage.success(pushOk)
    } else if (res?.rest_ok && res?.apply_ok === false) {
      const applyErrs = res.apply_errors
        ? Object.values(res.apply_errors).flat().join('；')
        : ''
      ElMessage.warning(
        '节点已收到配置，但系统未确认生效'
        + (applyErrs ? `：${applyErrs}` : '（请检查节点 WRITE_ENABLED / APPLY_ENABLED 开关）'),
      )
    } else {
      ElMessage.warning(pushFail)
    }
    return { ok: !!res?.ok && res?.apply_ok !== false, stage: 'push', result: res }
  } catch (e) {
    ElMessage.error('下发失败：' + (e?.response?.data?.detail || e.message))
    return { ok: false, stage: 'push' }
  }
}

/** 构建 POP 接口 netplan_config（对齐 vpop InterfaceLiveDetail） */
export function buildIfaceNetplanConfig(form) {
  const kindMap = {
    physical: 'ether', vlan: 'vlan', bond: 'bond', bridge: 'bridge',
    loopback: 'ether', tunnel: 'gre', dummy: 'ether',
  }
  const kind = kindMap[form.interface_type] || 'ether'
  const spec = {}
  const addrs = String(form.addressesText || '')
    .split(/[,\s]+/).map(s => s.trim()).filter(Boolean)
  if (addrs.length) spec.addresses = addrs
  if (form.mtu) spec.mtu = form.mtu
  if (form.dhcp4) spec.dhcp4 = true
  if (form.gateway4) spec.gateway4 = form.gateway4
  if (form.interface_type === 'vlan') {
    if (form.vlan_id) spec.id = form.vlan_id
    if (form.vlan_link) spec.link = form.vlan_link
  }
  if (form.interface_type === 'bridge' && form.bridge_ports) {
    spec.interfaces = String(form.bridge_ports).split(/[,\s]+/).map(s => s.trim()).filter(Boolean)
  }
  return { kind, spec }
}
