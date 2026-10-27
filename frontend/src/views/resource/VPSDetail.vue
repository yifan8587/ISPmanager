<template>
  <div v-loading="loading && !detail" class="vps-detail-page">
    <!-- ── 标题栏 ── -->
    <div class="page-header">
      <div class="dh" v-if="detail">
        <div class="dh-left">
          <el-button :icon="ArrowLeft" @click="goBack" class="dh-back">返回</el-button>
          <el-tag :type="statusMap[detail.status]?.type" size="large">
            {{ statusMap[detail.status]?.label }}
          </el-tag>
          <span class="dh-no">{{ detail.vps_no }}</span>
          <span class="dh-name">{{ detail.name }}</span>
          <el-tag type="info" size="small">{{ detail.pop_name }} · {{ detail.pop_city }}</el-tag>
        </div>
        <div class="dh-specs">
          <span class="spec">{{ detail.vcpu }}<em>vCPU</em></span>
          <span class="spec">{{ (detail.ram_mb/1024).toFixed(0) }}<em>G RAM</em></span>
          <span class="spec">{{ detail.disk_gb }}<em>G Disk</em></span>
          <span class="spec">{{ detail.bandwidth_mbps }}<em>Mbps</em></span>
        </div>
        <div class="dh-actions">
          <el-button size="small" :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
          <el-button v-if="detail.is_synced && detail.source_ref" size="small" type="warning"
            :icon="Monitor" @click="openConsole">控制台</el-button>
          <el-button v-if="detail.is_synced && detail.source_ref" size="small" type="warning"
            @click="openBwDlg">调整带宽</el-button>
          <el-button size="small" type="primary" @click="goEdit">编辑</el-button>
        </div>
      </div>
      <div v-else class="loading-tip">
        <el-button :icon="ArrowLeft" @click="goBack">返回</el-button>
        <span v-if="loading" style="margin-left:12px;"><el-icon class="is-loading"><Loading/></el-icon> 加载中…</span>
      </div>
    </div>

    <div v-if="loading && !detail" v-loading="true" style="height:280px;"/>

    <el-tabs v-else-if="detail" v-model="tab" class="dtabs">

      <!-- ★ 概览 ★ -->
      <el-tab-pane label="概览" name="overview">
        <el-row :gutter="14">
          <!-- 基础信息 -->
          <el-col :xs="24" :sm="12">
            <el-card shadow="never" class="ic" header="基础信息">
              <el-descriptions :column="1" border size="small">
                <el-descriptions-item label="VPS编号">
                  <el-tag type="primary" size="small">{{ detail.vps_no }}</el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="状态">
                  <el-tag :type="statusMap[detail.status]?.type" size="small">{{ statusMap[detail.status]?.label }}</el-tag>
                  <el-button link type="primary" size="small" style="margin-left:8px;" @click="openStatus">变更</el-button>
                </el-descriptions-item>
                <el-descriptions-item label="POP节点">{{ detail.pop_name }} ({{ detail.pop_city }})</el-descriptions-item>
                <el-descriptions-item label="关联客户">{{ detail.customer_name || '-' }}</el-descriptions-item>
                <el-descriptions-item label="开通时间">{{ fmt(detail.start_time) }}</el-descriptions-item>
                <el-descriptions-item label="到期时间">
                  <span :class="expCls(detail.expire_time)">{{ fmt(detail.expire_time) || '无期限' }}</span>
                </el-descriptions-item>
                <el-descriptions-item label="创建时间">{{ fmt(detail.created_at) }}</el-descriptions-item>
                <el-descriptions-item label="备注">{{ detail.remark || '-' }}</el-descriptions-item>
              </el-descriptions>
            </el-card>
          </el-col>

          <!-- 硬件与系统 -->
          <el-col :xs="24" :sm="12">
            <el-card shadow="never" class="ic" header="硬件与系统">
              <el-descriptions :column="1" border size="small">
                <el-descriptions-item label="vCPU">
                  <el-tag type="warning" size="small">{{ detail.vcpu }} 核</el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="内存">
                  <el-tag type="success" size="small">{{ (detail.ram_mb/1024).toFixed(0) }} GB</el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="磁盘">
                  <el-tag size="small">{{ detail.disk_gb }} GB</el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="网卡数">{{ detail.nic_count || 1 }}</el-descriptions-item>
                <el-descriptions-item label="带宽限制">{{ detail.bandwidth_mbps }} Mbps</el-descriptions-item>
                <el-descriptions-item label="操作系统">
                  {{ osMap[detail.os_type] || detail.os_type || '-' }}
                  <span v-if="detail.os_version" style="color:#909399;"> {{ detail.os_version }}</span>
                </el-descriptions-item>
                <el-descriptions-item label="内核版本">{{ detail.kernel_version || '-' }}</el-descriptions-item>
              </el-descriptions>
            </el-card>
          </el-col>

          <!-- 管理信息 -->
          <el-col :xs="24" :sm="12" style="margin-top:14px;">
            <el-card shadow="never" class="ic" header="管理信息">
              <el-descriptions :column="1" border size="small">
                <el-descriptions-item label="管理IP">
                  <el-tag type="info" size="small">{{ detail.mgmt_ip || '-' }}</el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="SSH端口">{{ detail.mgmt_port }}</el-descriptions-item>
                <el-descriptions-item label="管理用户">{{ detail.mgmt_username || 'root' }}</el-descriptions-item>
              </el-descriptions>
            </el-card>
          </el-col>

          <!-- IP 汇总 -->
          <el-col :xs="24" :sm="12" style="margin-top:14px;">
            <el-card shadow="never" class="ic" header="IP地址汇总">
              <div v-if="detail.ip_list?.length" style="display:flex;flex-wrap:wrap;gap:6px;">
                <div v-for="ip in detail.ip_list" :key="ip.id" class="ip-chip">
                  <el-tag
                    :type="ip.is_primary?'primary':ip.status==='assigned'?'success':'info'"
                    size="small"
                  >
                    {{ ip.address }}/{{ ip.prefix_len }}
                    <span v-if="ip.gateway" style="margin-left:4px;font-weight:400">gw {{ ip.gateway }}</span>
                  </el-tag>
                  <el-tag v-if="ip.is_primary" size="small" type="primary" style="margin-left:2px;">主</el-tag>
                </div>
              </div>
              <el-empty v-else description="暂无IP" :image-size="48"/>
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>

      <!-- ★ 监控 ★ -->
      <el-tab-pane label="监控" name="monitor">
        <MonitoringPanel
          target-type="vps"
          :resource-id="detail.id"
          :resource-name="detail.name"
          :pop-point-id="detail.pop_point"
          :monitored-ip="detail.mgmt_ip"
          :active="tab === 'monitor'"
        />
      </el-tab-pane>

      <!-- ★ 网络接口 ★ -->
      <el-tab-pane name="iface">
        <template #label>
          网络接口 <el-badge :value="detail.vps_interfaces?.length" type="primary"/>
        </template>
        <div style="display:flex;justify-content:flex-end;margin-bottom:10px;">
          <el-button type="primary" size="small" :icon="Plus" @click="openIface()">新增接口</el-button>
        </div>
        <el-table :data="detail.vps_interfaces" border stripe size="small" empty-text="暂无网络接口">
          <el-table-column prop="ifname" label="接口名" width="110"/>
          <el-table-column prop="mac_address" label="MAC地址" width="160">
            <template #default="{row}"><el-text type="info" size="small">{{ row.mac_address || '-' }}</el-text></template>
          </el-table-column>
          <el-table-column prop="mtu" label="MTU" width="70" align="center"/>
          <el-table-column prop="bandwidth_mbps" label="带宽(Mbps)" width="105" align="right">
            <template #default="{row}">{{ row.bandwidth_mbps || '-' }}</template>
          </el-table-column>
          <el-table-column label="主接口" width="72" align="center">
            <template #default="{row}">
              <el-icon v-if="row.is_primary" color="#67C23A"><Select/></el-icon>
              <span v-else style="color:#C0C4CC;">-</span>
            </template>
          </el-table-column>
          <el-table-column prop="description" label="描述" min-width="130"/>
          <el-table-column label="操作" width="100" fixed="right">
            <template #default="{row}">
              <el-button link type="primary" size="small" @click="openIface(row)">编辑</el-button>
              <el-button link type="danger" size="small" @click="handleDelIface(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- ★ IP地址 ★ -->
      <el-tab-pane name="ips">
        <template #label>
          IP地址 <el-badge :value="detail.ip_list?.length" type="success"/>
        </template>
        <el-table :data="detail.ip_list" border stripe size="small" empty-text="暂无IP地址">
          <el-table-column label="IP地址" width="160">
            <template #default="{row}">
              <el-tag :type="row.is_primary?'primary':'info'" size="small">{{ row.address }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="prefix_len" label="/前缀" width="65" align="center"/>
          <el-table-column label="网关" width="140">
            <template #default="{row}">{{ row.gateway || '-' }}</template>
          </el-table-column>
          <el-table-column label="掩码" width="140">
            <template #default="{row}">{{ row.netmask || '-' }}</template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{row}">
              <el-tag :type="ipStatusMap[row.status]?.type" size="small">
                {{ ipStatusMap[row.status]?.label || row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="主IP" width="65" align="center">
            <template #default="{row}">
              <el-icon v-if="row.is_primary" color="#409EFF"><Select/></el-icon>
              <span v-else style="color:#C0C4CC;">-</span>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- ★ 路由表 ★ -->
      <el-tab-pane name="routes">
        <template #label>
          路由表 <el-badge :value="detail.route_tables?.length"/>
        </template>
        <div v-if="detail.route_tables?.length">
          <div v-for="rt in detail.route_tables" :key="rt.id" class="rt-chip">
            <el-tag type="primary" size="small">Table {{ rt.table_id }}</el-tag>
            <span class="rt-name">{{ rt.name || '(未命名)' }}</span>
          </div>
        </div>
        <el-empty v-else description="暂无路由表" :image-size="60"/>
      </el-tab-pane>

      <!-- ★ 操作日志 ★ -->
      <el-tab-pane name="logs" label="操作日志">
        <el-timeline style="padding:8px 0 0;">
          <el-timeline-item
            v-for="log in detail.operation_logs"
            :key="log.id"
            :timestamp="fmt(log.created_at)"
            :type="logType(log.action_type)"
            placement="top"
          >
            <div class="log-row">
              <el-tag size="small" :type="logType(log.action_type)" style="margin-right:6px;">
                {{ log.action_type_label || log.action_type }}
              </el-tag>
              {{ log.description }}
              <el-text type="info" size="small" style="margin-left:8px;">{{ log.operator_name || '系统' }}</el-text>
              <div v-if="formatLogDiff(log.old_value, log.new_value)" class="log-diff">
                {{ formatLogDiff(log.old_value, log.new_value) }}
              </div>
            </div>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-if="!detail.operation_logs?.length" description="暂无操作日志" :image-size="60"/>
      </el-tab-pane>
    </el-tabs>
  </div>

  <!-- 状态变更 -->
  <el-dialog v-model="statusDlg" title="变更VPS状态" width="380px" destroy-on-close append-to-body>
    <el-form :model="statusForm" label-width="80px">
      <el-form-item label="新状态">
        <el-select v-model="statusForm.status" style="width:100%">
          <el-option v-for="s in statusOptions" :key="s.value" :label="s.label" :value="s.value"/>
        </el-select>
      </el-form-item>
      <el-form-item label="原因">
        <el-input v-model="statusForm.reason" type="textarea" :rows="2"/>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="statusDlg=false">取消</el-button>
      <el-button type="primary" :loading="statusLoading" @click="doStatus">确认</el-button>
    </template>
  </el-dialog>

  <!-- 网络接口 -->
  <el-dialog v-model="ifaceDlg" :title="editIface?'编辑接口':'新增接口'" width="460px" destroy-on-close append-to-body>
    <el-form :model="ifaceForm" label-width="100px">
      <el-form-item label="接口名称" required>
        <el-input v-model="ifaceForm.ifname" placeholder="如 eth0, eth1"/>
      </el-form-item>
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="MAC地址">
            <el-input v-model="ifaceForm.mac_address" placeholder="如 00:11:22:33:44:55"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="MTU">
            <el-input-number v-model="ifaceForm.mtu" :min="576" :max="9000" style="width:100%"/>
          </el-form-item>
        </el-col>
      </el-row>
      <el-form-item label="带宽(Mbps)">
        <el-input-number v-model="ifaceForm.bandwidth_mbps" :min="1" style="width:100%"/>
      </el-form-item>
      <el-form-item label="是否主接口">
        <el-switch v-model="ifaceForm.is_primary"/>
      </el-form-item>
      <el-form-item label="描述">
        <el-input v-model="ifaceForm.description"/>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="ifaceDlg=false">取消</el-button>
      <el-button type="primary" :loading="ifaceLoading" @click="saveIface">确认</el-button>
    </template>
  </el-dialog>

  <!-- ── 同步 VPS 带宽调整（对齐 vpop PVEVMs rate_mb） ── -->
  <el-dialog v-model="bwDlg" title="调整带宽并下发到节点" width="420px" destroy-on-close append-to-body>
    <el-form label-width="110px">
      <el-form-item label="当前带宽">
        <el-text>{{ detail?.bandwidth_mbps || 0 }} Mbps</el-text>
      </el-form-item>
      <el-form-item label="新带宽(Mbps)" required>
        <el-input-number v-model="bwForm.mbps" :min="1" :max="100000" style="width:100%"/>
      </el-form-item>
      <el-alert type="info" :closable="false" show-icon
        title="将更新 ISPmanager 记录并通过节点 PVE 网卡 rate 即时生效（net0）。"/>
    </el-form>
    <template #footer>
      <el-button @click="bwDlg=false">取消</el-button>
      <el-button type="primary" :loading="bwSaving" @click="saveBw(false)">仅保存</el-button>
      <el-button type="warning" :loading="bwSaving" @click="saveBw(true)">保存并下发</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Select, Loading, Monitor, ArrowLeft } from '@element-plus/icons-vue'
import {
  getVPSDetail, changeVPSStatus, updateVPS,
  createVPSInterface, updateVPSInterface, deleteVPSInterface,
} from '@/api/resource'
import MonitoringPanel from '@/components/MonitoringPanel.vue'
import { formatLogDiff } from '@/utils/resourceLog'

const route = useRoute()
const router = useRouter()
const vpsId = computed(() => route.params.id)

function goBack() {
  router.push('/resource/vps')
}
function goEdit() {
  if (detail.value) router.push({ path: '/resource/vps', query: { edit: detail.value.id } })
}

function openConsole() {
  if (!detail.value) return
  const r = router.resolve({ name: 'VPSVncConsole', params: { id: detail.value.id } })
  window.open(r.href, '_blank')
}

const detail  = ref(null)
const loading = ref(false)
const tab     = ref('overview')

async function load() {
  if (!vpsId.value) return
  loading.value = true
  try { detail.value = await getVPSDetail(vpsId.value) }
  catch (e) { ElMessage.error('加载失败：' + (e?.response?.data?.detail || e.message)) }
  finally { loading.value = false }
}
onMounted(load)

// ── 常量 ─────────────────────────────────────────────────────
const statusMap = {
  running:   { label:'运行中', type:'success' }, stopped:   { label:'已停止', type:'info'    },
  suspended: { label:'已暂停', type:'warning' }, creating:  { label:'创建中', type:'primary' },
  deleting:  { label:'删除中', type:'warning' }, error:     { label:'故障',   type:'danger'  }
}
const statusOptions = [
  { value:'running', label:'运行中' }, { value:'stopped', label:'已停止' },
  { value:'suspended', label:'已暂停' }, { value:'error', label:'故障' }
]
const ipStatusMap = {
  available: { label:'可用',   type:'success' }, assigned: { label:'已分配', type:'primary' },
  reserved:  { label:'预留',   type:'warning' }, blocked:  { label:'封禁',   type:'danger'  }
}
const osMap = { debian:'Debian', ubuntu:'Ubuntu', centos:'CentOS', rhel:'RHEL', windows:'Windows Server', other:'其他' }

// ── 工具 ─────────────────────────────────────────────────────
function fmt(dt) {
  if (!dt) return '-'
  return new Date(dt).toLocaleString('zh-CN',{hour12:false}).replace(/\//g,'-').replace(',','')
}
function expCls(dt) {
  if (!dt) return ''
  const d = (new Date(dt) - Date.now()) / 86400000
  return d < 0 ? 'expired' : d < 30 ? 'expiring' : ''
}
function logType(a) {
  if (a === 'create') return 'success'
  if (['delete','ip_release'].includes(a)) return 'danger'
  if (a === 'status_change') return 'warning'
  if (['ip_assign','bw_alloc'].includes(a)) return 'primary'
  return ''
}

// ── 状态变更 ─────────────────────────────────────────────────
const statusDlg     = ref(false)
const statusLoading = ref(false)
const statusForm    = ref({ status:'running', reason:'' })
function openStatus() {
  statusForm.value = { status: detail.value?.status || 'running', reason:'' }
  statusDlg.value = true
}
async function doStatus() {
  statusLoading.value = true
  try {
    await changeVPSStatus(detail.value.id, statusForm.value)
    ElMessage.success('状态已更新')
    statusDlg.value = false
    load()
  } catch (e) { ElMessage.error('更新失败：' + (e?.response?.data?.detail || e.message)) }
  finally { statusLoading.value = false }
}

// ── 网络接口 ─────────────────────────────────────────────────
const ifaceDlg     = ref(false)
const ifaceLoading = ref(false)
const editIface    = ref(null)
const ifaceForm    = ref({ ifname:'', mac_address:'', mtu:1500, bandwidth_mbps:100, is_primary:false, description:'' })

function openIface(row = null) {
  editIface.value = row
  ifaceForm.value = row
    ? { ifname:row.ifname, mac_address:row.mac_address||'', mtu:row.mtu||1500, bandwidth_mbps:row.bandwidth_mbps||100, is_primary:!!row.is_primary, description:row.description||'' }
    : { ifname:'', mac_address:'', mtu:1500, bandwidth_mbps:100, is_primary:false, description:'' }
  ifaceDlg.value = true
}
async function saveIface() {
  if (!ifaceForm.value.ifname) { ElMessage.warning('请输入接口名称'); return }
  ifaceLoading.value = true
  try {
    const payload = { ...ifaceForm.value, vps: detail.value.id }
    if (editIface.value) await updateVPSInterface(editIface.value.id, payload)
    else await createVPSInterface(payload)
    ElMessage.success(editIface.value ? '已更新' : '接口已添加')
    ifaceDlg.value = false; load()
  } catch (e) {
    ElMessage.error('保存失败：' + (e?.response?.data?.detail || JSON.stringify(e?.response?.data) || e.message))
  } finally { ifaceLoading.value = false }
}
async function handleDelIface(row) {
  await ElMessageBox.confirm(`确定删除接口 "${row.ifname}"？`, '警告', { type:'warning' })
  await deleteVPSInterface(row.id)
  ElMessage.success('已删除'); load()
}

// ── 带宽调整（同步 VPS，对齐 vpop PVEVMs rate_mb） ─────────────
const bwDlg = ref(false)
const bwSaving = ref(false)
const bwForm = ref({ mbps: 100 })

function openBwDlg() {
  bwForm.value = { mbps: detail.value?.bandwidth_mbps || 100 }
  bwDlg.value = true
}

async function saveBw(andPush = false) {
  if (!detail.value || bwForm.value.mbps <= 0) {
    ElMessage.warning('带宽必须大于 0')
    return
  }
  bwSaving.value = true
  try {
    await updateVPS(detail.value.id, { bandwidth_mbps: bwForm.value.mbps }, andPush)
    ElMessage.success(andPush ? '带宽已保存并下发到节点' : '带宽已保存')
    bwDlg.value = false
    load()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '操作失败')
  } finally { bwSaving.value = false }
}
</script>

<style scoped>
/* 整页容器 */
.vps-detail-page { background:#fff; border-radius:8px; padding:16px 20px 24px; min-height:calc(100vh - 120px); }
.page-header     { margin-bottom:16px; padding-bottom:14px; border-bottom:1px solid #ebeef5; }
.dh-back         { margin-right:4px; }
/* 头部 */
.dh        { display:flex; align-items:center; gap:10px; width:100%; flex-wrap:wrap; }
.dh-left   { display:flex; align-items:center; gap:6px; flex:1; min-width:0; }
.dh-no     { font-size:12px; color:#909399; }
.dh-name   { font-size:16px; font-weight:700; color:#1a1a2e; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.dh-specs  { display:flex; gap:0; border:1px solid #ebeef5; border-radius:6px; overflow:hidden; }
.spec      { padding:4px 12px; text-align:center; font-size:15px; font-weight:700; color:#303133; border-right:1px solid #ebeef5; }
.spec:last-child { border-right:none; }
.spec em   { display:block; font-size:10px; font-weight:400; color:#909399; font-style:normal; margin-top:1px; }
.dh-actions{ display:flex; gap:6px; }
.loading-tip { color:#606266; display:flex; align-items:center; gap:6px; }
/* 卡片 */
.ic :deep(.el-card__header) { font-weight:600; font-size:13px; padding:10px 14px; }
.dtabs :deep(.el-tabs__header) { margin-bottom:12px; }
/* IP */
.ip-chip   { display:inline-flex; align-items:center; }
/* 路由表 */
.rt-chip   { display:inline-flex; align-items:center; gap:8px; margin:4px; padding:6px 12px; background:#f5f7fa; border-radius:6px; }
.rt-name   { font-size:13px; color:#303133; }
/* 日志 */
.log-row   { display:flex; align-items:center; flex-wrap:wrap; font-size:13px; }
.log-diff  { width:100%; margin-top:4px; color:#909399; font-size:12px; }
/* 到期 */
.expired   { color:#F56C6C; font-weight:600; }
.expiring  { color:#E6A23C; }
</style>
