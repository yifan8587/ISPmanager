<template>
  <div class="page">
    <div class="page-toolbar">
      <div class="pt-left">
        <span class="pt-title">监控主机</span>
        <el-text type="info" size="small">向 Zabbix 添加 POP / 通用主机监控，并查看其监控项与图形（VPS / 边缘加速请在各自详情页关联）。</el-text>
      </div>
      <el-button type="primary" :icon="Plus" @click="openEdit()">新增监控主机</el-button>
    </div>

    <div class="filters">
      <el-radio-group v-model="filterType" size="small" @change="load">
        <el-radio-button value="">全部</el-radio-button>
        <el-radio-button value="pop">POP 节点</el-radio-button>
        <el-radio-button value="generic">通用主机</el-radio-button>
      </el-radio-group>
      <el-select v-model="filterServer" placeholder="全部 Zabbix" clearable size="small"
        style="width:170px;margin-left:12px;" @change="load">
        <el-option v-for="s in zabbixServers" :key="s.id" :label="s.name" :value="s.id" />
      </el-select>
      <el-input v-model="search" placeholder="搜索主机名 / IP" :prefix-icon="Search" clearable
        style="width:240px;margin-left:auto;" @keyup.enter="load" @clear="load" />
    </div>

    <el-table v-loading="loading" :data="list" border stripe>
      <el-table-column prop="host_kind_label" label="类型" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="typeTag[row.host_kind]">{{ row.host_kind_label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="resource_label" label="POP / 名称" min-width="160" show-overflow-tooltip />
      <el-table-column prop="hostname" label="Zabbix 主机" min-width="150">
        <template #default="{ row }">
          {{ row.visible_name || row.hostname }}
          <el-tag v-if="row.zabbix_hostid" size="small" effect="plain" style="margin-left:4px;">#{{ row.zabbix_hostid }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="monitored_ip" label="监控IP" width="130" />
      <el-table-column prop="zabbix_server_name" label="Zabbix" width="120" show-overflow-tooltip />
      <el-table-column label="监控" width="110">
        <template #default="{ row }">
          <el-tag v-if="row.enable_icmp" size="small" type="success" effect="plain">ICMP</el-tag>
          <el-tag v-if="row.enable_snmp" size="small" type="primary" effect="plain" style="margin-left:2px;">SNMP</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="项/图" width="90" align="center">
        <template #default="{ row }">
          <el-text size="small">{{ row.item_count }} / {{ row.graph_count }}</el-text>
        </template>
      </el-table-column>
      <el-table-column label="同步状态" width="100">
        <template #default="{ row }">
          <el-tag :type="syncTag[row.sync_status] || 'info'" size="small">{{ row.sync_status_label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="310" fixed="right">
        <template #default="{ row }">
          <el-button link type="success" size="small" :loading="syncingId === row.id" @click="doSync(row)">同步</el-button>
          <el-button link type="primary" size="small" @click="openGraphs(row)">图形</el-button>
          <el-button link type="primary" size="small" @click="openWebSSH(row)">终端</el-button>
          <el-button link type="warning" size="small" @click="openEdit(row)">编辑</el-button>
          <el-button link type="danger" size="small" @click="doDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 编辑/新增弹窗 -->
    <el-dialog v-model="dialogVisible" :title="editRow ? '编辑监控主机' : '新增监控主机'" width="680px" destroy-on-close>
      <el-form :model="form" label-width="120px" class="host-form">
        <el-form-item label="主机类型" required>
          <el-select v-model="form.host_kind" style="width:100%" :disabled="!!editRow" @change="onTypeChange">
            <el-option label="POP 节点" value="pop" />
            <el-option label="通用主机" value="generic" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.host_kind === 'pop'" label="关联 POP">
          <el-select v-model="form.pop_point" filterable remote :remote-method="searchResources"
            :loading="resourceLoading" placeholder="搜索并选择 POP（可选）" clearable style="width:100%" :disabled="!!editRow">
            <el-option v-for="r in resourceOptions" :key="r.id" :label="r.label" :value="r.id" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.host_kind === 'generic'" label="服务 POP">
          <el-select v-model="form.service_pop_point" filterable remote :remote-method="searchResources"
            :loading="resourceLoading" placeholder="归属 POP（VPS/边缘加速关联时按 POP 过滤）" clearable style="width:100%">
            <el-option v-for="r in resourceOptions" :key="r.id" :label="r.label" :value="r.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="Zabbix 服务器" required>
          <el-select v-model="form.zabbix_server" style="width:100%" @change="onServerChange">
            <el-option v-for="s in zabbixServers" :key="s.id"
              :label="s.name + (s.is_default ? '（默认）' : '')" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="主机群组" required>
          <el-select v-model="form.zabbix_group_ids" multiple filterable
            :loading="metaLoading" placeholder="从 Zabbix 拉取的主机群组" style="width:100%"
            no-data-text="请先选择 Zabbix 服务器">
            <el-option v-for="g in hostGroups" :key="g.groupid" :label="g.name" :value="g.groupid" />
          </el-select>
        </el-form-item>
        <el-form-item label="监控模板">
          <el-select v-model="form.zabbix_template_ids" multiple filterable
            :loading="metaLoading" placeholder="如 Linux by SNMP / ICMP Ping" style="width:100%"
            no-data-text="请先选择 Zabbix 服务器">
            <el-option v-for="t in templates" :key="t.templateid" :label="t.name" :value="t.templateid" />
          </el-select>
        </el-form-item>
        <el-form-item label="主机名" required>
          <el-input v-model="form.hostname" />
        </el-form-item>
        <el-form-item label="显示名">
          <el-input v-model="form.visible_name" />
        </el-form-item>
        <el-form-item label="监控 IP" required>
          <el-input v-model="form.monitored_ip" placeholder="ICMP/SNMP 探测地址" />
        </el-form-item>
        <el-form-item label="监控方式">
          <el-checkbox v-model="form.enable_icmp">ICMP</el-checkbox>
          <el-checkbox v-model="form.enable_snmp">SNMP</el-checkbox>
        </el-form-item>
        <template v-if="form.enable_snmp">
          <el-divider content-position="left">SNMP 配置</el-divider>
          <el-form-item label="Community">
            <el-input v-model="form.snmp_community" placeholder="如 public 或 10ghk" />
          </el-form-item>
          <el-row :gutter="20">
            <el-col :span="12">
              <el-form-item label="SNMP 版本">
                <el-select v-model="form.snmp_version" placeholder="选择版本" style="width:100%">
                  <el-option label="SNMPv1" value="1" />
                  <el-option label="SNMPv2c" value="2" />
                  <el-option label="SNMPv3" value="3" />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="SNMP 端口">
                <el-input-number
                  v-model="form.snmp_port"
                  :min="1"
                  :max="65535"
                  controls-position="right"
                  style="width:100%"
                />
              </el-form-item>
            </el-col>
          </el-row>
        </template>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="doSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search } from '@element-plus/icons-vue'
import {
  getMonitoringHosts, createMonitoringHost,
  updateMonitoringHost, deleteMonitoringHost, getZabbixServers,
  getZabbixHostGroups, getZabbixTemplates, syncMonitoringHost,
} from '@/api/operation'
import { getPopPoints } from '@/api/resource'

const router = useRouter()

const loading = ref(false)
const list = ref([])
const filterType = ref('')
const filterServer = ref(null)
const search = ref('')

const typeTag = { pop: 'warning', generic: 'info' }
const syncTag = { synced: 'success', error: 'danger', syncing: 'warning', pending: 'info', disabled: 'info' }

// 将 DRF 校验错误（含全局 {message, detail} 包装）展开为可读字符串
function extractError(e) {
  const data = e?.response?.data
  if (!data) return e?.message || '未知错误'
  const detail = data.detail ?? data
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.join('；')
  if (typeof detail === 'object') {
    const parts = []
    for (const [k, v] of Object.entries(detail)) {
      const val = Array.isArray(v) ? v.join('，') : (typeof v === 'object' ? JSON.stringify(v) : v)
      parts.push(k === 'non_field_errors' ? val : `${k}: ${val}`)
    }
    if (parts.length) return parts.join('；')
  }
  return data.message || e?.message || '请求失败'
}

async function load() {
  loading.value = true
  try {
    const params = {}
    if (filterType.value) params.host_kind = filterType.value
    if (filterServer.value) params.zabbix_server = filterServer.value
    if (search.value) params.search = search.value
    const res = await getMonitoringHosts(params)
    list.value = res?.results || res || []
  } catch (e) {
    ElMessage.error('加载失败：' + (e?.response?.data?.detail || e.message))
  } finally { loading.value = false }
}

// ── 编辑 ──────────────────────────────────────────────────────
const dialogVisible = ref(false)
const saving = ref(false)
const editRow = ref(null)
const zabbixServers = ref([])
const resourceOptions = ref([])
const resourceLoading = ref(false)
const hostGroups = ref([])
const templates = ref([])
const metaLoading = ref(false)
const syncingId = ref(null)

const defaultForm = () => ({
  host_kind: 'pop', pop_point: null, service_pop_point: null, zabbix_server: null,
  hostname: '', visible_name: '', monitored_ip: '',
  enable_icmp: true, enable_snmp: true, snmp_community: 'public',
  snmp_version: '2', snmp_port: 161,
  zabbix_group_ids: [], zabbix_template_ids: [], remark: '',
})
const form = reactive(defaultForm())

// 选择 Zabbix 服务器后拉取群组与模板
async function loadServerMeta(serverId) {
  if (!serverId) { hostGroups.value = []; templates.value = []; return }
  metaLoading.value = true
  try {
    const [groups, tpls] = await Promise.all([
      getZabbixHostGroups(serverId),
      getZabbixTemplates(serverId),
    ])
    hostGroups.value = groups || []
    templates.value = tpls || []
  } catch (e) {
    ElMessage.warning('拉取 Zabbix 群组/模板失败：' + extractError(e))
    hostGroups.value = []
    templates.value = []
  } finally { metaLoading.value = false }
}

function onServerChange(serverId) {
  loadServerMeta(serverId)
}

async function openEdit(row = null) {
  editRow.value = row
  Object.assign(form, defaultForm())
  if (!zabbixServers.value.length) {
    const res = await getZabbixServers({ is_active: true })
    zabbixServers.value = res?.results || res || []
  }
  if (row) {
    Object.assign(form, {
      host_kind: row.host_kind,
      pop_point: row.pop_point || null,
      service_pop_point: row.service_pop_point || null,
      zabbix_server: row.zabbix_server,
      hostname: row.hostname, visible_name: row.visible_name || '',
      monitored_ip: row.monitored_ip || '',
      enable_icmp: row.enable_icmp, enable_snmp: row.enable_snmp,
      snmp_community: row.snmp_community || 'public',
      snmp_version: row.snmp_version || '2', snmp_port: row.snmp_port || 161,
      zabbix_group_ids: (row.zabbix_group_ids || []).map(String),
      zabbix_template_ids: (row.zabbix_template_ids || []).map(String),
      remark: row.remark || '',
    })
    if (form.pop_point) resourceOptions.value = [{ id: form.pop_point, label: row.pop_point_label || row.resource_label }]
    else if (form.service_pop_point) resourceOptions.value = [{ id: form.service_pop_point, label: row.service_pop_point_label || '' }]
    loadServerMeta(form.zabbix_server)
  } else {
    const def = zabbixServers.value.find(s => s.is_default) || zabbixServers.value[0]
    form.zabbix_server = def?.id || null
    loadServerMeta(form.zabbix_server)
    searchResources('')
  }
  dialogVisible.value = true
}

function onTypeChange() {
  form.pop_point = null
  form.service_pop_point = null
  resourceOptions.value = []
  if (form.host_kind === 'pop') searchResources('')
}

async function searchResources(q) {
  resourceLoading.value = true
  try {
    const params = { search: q || '', page_size: 30 }
    const res = await getPopPoints(params)
    const items = res?.results || res || []
    resourceOptions.value = items.map((r) => ({ id: r.id, label: `[${r.code}] ${r.name}` }))
  } catch { /* ignore */ } finally { resourceLoading.value = false }
}

async function doSave() {
  if (!form.zabbix_server) { ElMessage.warning('请选择 Zabbix 服务器'); return }
  if (!form.hostname) { ElMessage.warning('请填写主机名'); return }
  saving.value = true
  try {
    const payload = {
      host_kind: form.host_kind,
      pop_point: form.host_kind === 'pop' ? (form.pop_point || null) : null,
      service_pop_point: form.host_kind === 'generic' ? (form.service_pop_point || null) : null,
      zabbix_server: form.zabbix_server,
      hostname: form.hostname, visible_name: form.visible_name,
      monitored_ip: form.monitored_ip || null,
      enable_icmp: form.enable_icmp, enable_snmp: form.enable_snmp,
      snmp_community: form.snmp_community,
      snmp_version: form.snmp_version, snmp_port: form.snmp_port,
      zabbix_group_ids: form.zabbix_group_ids,
      zabbix_template_ids: form.zabbix_template_ids,
      remark: form.remark,
    }
    if (editRow.value) await updateMonitoringHost(editRow.value.id, payload)
    else await createMonitoringHost(payload)
    ElMessage.success(editRow.value ? '已更新' : '已创建')
    dialogVisible.value = false
    load()
  } catch (e) {
    ElMessage.error('保存失败：' + extractError(e))
  } finally { saving.value = false }
}

async function doDelete(row) {
  await ElMessageBox.confirm(`确定删除监控映射「${row.hostname}」？`, '警告', { type: 'warning' })
  await deleteMonitoringHost(row.id)
  ElMessage.success('已删除')
  load()
}

async function doSync(row) {
  // 通用主机（已在 Zabbix 存在）只重新拉取监控项/图形，不下发；POP 主机下发创建/更新
  const push = row.host_kind === 'pop'
  if (push && !row.zabbix_group_ids?.length) {
    try {
      await ElMessageBox.confirm(
        '该主机尚未选择 Zabbix 主机群组，无法在 Zabbix 创建主机。是否先去编辑补充群组/模板？',
        '提示', { type: 'warning', confirmButtonText: '去编辑' },
      )
      openEdit(row)
    } catch { /* cancel */ }
    return
  }
  syncingId.value = row.id
  try {
    const res = await syncMonitoringHost(row.id, { push, async: true })
    if (res?.status === 'queued') {
      ElMessage.success(res.message || '已在后台排队同步，请稍后刷新查看')
    } else {
      const h = res.host || {}
      const items = res.items || {}
      const graphs = res.graphs || {}
      ElMessage.success(
        `同步成功（${h.action === 'create' ? '新建' : '更新'} hostid=${res.zabbix_hostid}）：` +
        `监控项 ${items.total ?? 0}，图形 ${graphs.total ?? 0}`,
      )
    }
    load()
  } catch (e) {
    ElMessage.error('同步失败：' + extractError(e))
  } finally { syncingId.value = null }
}

function openGraphs(row) {
  router.push({ name: 'MonitoringHostGraphs', params: { id: row.id } })
}

function openWebSSH(row) {
  router.push({
    name: 'OperationWebSSH',
    query: { target_type: 'monitoring_host', target_id: row.id },
  })
}

async function loadServers() {
  try {
    const res = await getZabbixServers({ is_active: true })
    zabbixServers.value = res?.results || res || []
  } catch { /* ignore */ }
}

onMounted(() => { loadServers(); load() })
</script>

<style scoped>
.page { background: #fff; border-radius: 8px; padding: 16px 20px; }
.page-toolbar { display: flex; align-items: center; margin-bottom: 14px; }
.pt-left { display: flex; flex-direction: column; gap: 2px; }
.pt-title { font-size: 16px; font-weight: 700; color: #1a1a2e; }
.page-toolbar > .el-button { margin-left: auto; }
.filters { display: flex; align-items: center; margin-bottom: 14px; }
/* 弹窗内表单项：栅格列内 label + 控件完整显示 */
.host-form :deep(.el-col .el-form-item) { margin-bottom: 18px; }
.host-form :deep(.el-input-number) { width: 100%; }
.host-form :deep(.el-select) { width: 100%; }
</style>
