<template>
  <div class="page-container">
    <div class="page-header">
      <h2 class="page-title">VPS管理</h2>
      <div class="header-actions">
        <el-button type="success" :icon="Cpu" @click="provisionVisible = true">节点创建虚机</el-button>
        <el-button type="primary" :icon="Plus" @click="openDialog()">新建VPS档案</el-button>
      </div>
    </div>

    <el-dialog v-model="cloneVisible" title="克隆虚机" width="480px" destroy-on-close>
      <el-form :model="cloneForm" label-width="100px">
        <el-form-item label="源虚机">
          <span>{{ cloneRow?.name }}（PVE #{{ cloneRow?.source_vmid }}）</span>
        </el-form-item>
        <el-form-item label="新虚机名称">
          <el-input v-model="cloneForm.newname" placeholder="留空则自动命名" />
        </el-form-item>
        <el-form-item label="新 VMID">
          <el-input-number v-model="cloneForm.newid" :min="100" :max="999999999" controls-position="right" style="width:100%" />
        </el-form-item>
        <el-form-item label="完整克隆">
          <el-switch v-model="cloneForm.full" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="cloneVisible = false">取消</el-button>
        <el-button type="primary" :loading="cloning" @click="doClone">确认克隆</el-button>
      </template>
    </el-dialog>

    <el-card shadow="never" class="search-card">
      <el-form :model="searchForm" inline>
        <el-form-item label="关键词">
          <el-input v-model="searchForm.search" placeholder="VPS编号/名称" clearable style="width:180px" @keyup.enter="loadData" />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="searchForm.status" clearable placeholder="全部" style="width:110px">
            <el-option v-for="s in statusOptions" :key="s.value" :label="s.label" :value="s.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="来源">
          <el-select v-model="searchForm.is_synced" clearable placeholder="全部" style="width:120px">
            <el-option label="节点同步" :value="true" />
            <el-option label="手工建档" :value="false" />
          </el-select>
        </el-form-item>
        <el-form-item label="资源位置">
          <ResourceLocationFilter
            :location="location"
            :regions="regions"
            :zones="filteredZones"
            :pop-points="filteredPopPoints"
            :pop-label="popLabel"
            :loading="loadingLocation"
            @update="applyLocation"
            @change="reloadForLocation"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="loadData">搜索</el-button>
          <el-button @click="resetSearch">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" style="margin-top:12px;">
      <el-table :data="groupedTableData" v-loading="loading" stripe border :span-method="locationSpanMethod">
        <el-table-column label="VPS编号" width="160">
          <template #default="{ row }">
            <ResourceTableGroupLabel v-if="isLocationGroup(row)" :row="row" />
            <span v-else>{{ row.vps_no }}</span>
          </template>
        </el-table-column>
        <!-- 后端字段名：name -->
        <el-table-column prop="name" label="名称" min-width="150" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusMap[row.status]?.type" size="small">{{ statusMap[row.status]?.label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="规格" width="210">
          <template #default="{ row }">
            <!-- 后端字段：vcpu, ram_mb, disk_gb -->
            <span class="spec-badge">{{ row.vcpu }}C</span>
            <span class="spec-badge">{{ (row.ram_mb / 1024).toFixed(0) }}G RAM</span>
            <span class="spec-badge">{{ row.disk_gb }}G</span>
          </template>
        </el-table-column>
        <el-table-column label="操作系统" width="130">
          <!-- 后端字段：os_type + os_version -->
          <template #default="{ row }">{{ osTypeMap[row.os_type] || row.os_type || '-' }} {{ row.os_version }}</template>
        </el-table-column>
        <!-- 后端字段：mgmt_ip, mgmt_port -->
        <el-table-column prop="mgmt_ip" label="管理IP" width="140" />
        <el-table-column prop="bandwidth_mbps" label="带宽" width="90" align="right">
          <template #default="{ row }">{{ row.bandwidth_mbps }} Mbps</template>
        </el-table-column>
        <el-table-column prop="customer_name" label="所属客户" width="120" />
        <el-table-column label="计费产品" width="200">
          <template #default="{ row }">
            <template v-if="row.product">
              <div class="prod-name">{{ row.product_name }}</div>
              <div v-if="row.product_billing" class="prod-fee">
                <span>{{ row.product_billing.monthly_price }} {{ row.product_billing.currency }}/月</span>
                <el-tag v-if="Number(row.product_billing.overage_monthly) > 0" type="warning" size="small" effect="plain">
                  超出 +{{ row.product_billing.overage_monthly }}
                </el-tag>
              </div>
            </template>
            <el-tag v-else type="info" size="small" effect="plain">未关联</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="POP节点" width="130">
          <template #default="{ row }">{{ row.pop_name || '-' }}</template>
        </el-table-column>
        <el-table-column label="区域 / 城市" min-width="150">
          <template #default="{ row }">
            {{ [row.region_name, row.country_name, row.city_name].filter(Boolean).join(' / ') || '-' }}
          </template>
        </el-table-column>
        <el-table-column label="来源" width="150">
          <template #default="{ row }">
            <el-tag v-if="row.is_synced" type="success" size="small" effect="plain">
              PVE #{{ row.source_vmid }}{{ row.source_node ? ' @' + row.source_node : '' }}
            </el-tag>
            <el-tag v-else type="info" size="small" effect="plain">手工</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="380" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">
              <el-icon><Document/></el-icon> 详情
            </el-button>
            <template v-if="row.is_synced">
              <el-button link type="warning" size="small" @click="openConsole(row)">
                <el-icon><Monitor/></el-icon> 控制台
              </el-button>
              <el-dropdown trigger="click" @command="cmd => handlePower(row, cmd)" style="margin:0 8px;">
                <el-button link type="warning" size="small">
                  电源<el-icon class="el-icon--right"><ArrowDown/></el-icon>
                </el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="start">启动</el-dropdown-item>
                    <el-dropdown-item command="shutdown">优雅关机</el-dropdown-item>
                    <el-dropdown-item command="stop">强制停止</el-dropdown-item>
                    <el-dropdown-item command="restart">重启</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
              <el-button link type="primary" size="small" @click="handleClone(row)">克隆</el-button>
              <el-button link type="success" size="small" :loading="syncingId === row.id" @click="handleSyncNow(row)">同步</el-button>
            </template>
            <el-button link type="primary" size="small" @click="openDialog(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total"
        layout="total, prev, pager, next" class="pagination" @change="loadData" />
    </el-card>

    <el-dialog v-model="dialogVisible" :title="editRow ? '编辑VPS' : '新建VPS'" width="700px" destroy-on-close>
      <el-form ref="formRef" :model="form" :rules="formRules" label-width="100px">
        <el-divider content-position="left">基础信息</el-divider>
        <el-row :gutter="16">
          <!-- 后端写入字段名：name（不是hostname） -->
          <el-col :span="12">
            <el-form-item label="VPS名称" prop="name">
              <el-input v-model="form.name" placeholder="如 Tokyo-VPS-01" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="POP节点" prop="pop_point">
              <el-select v-model="form.pop_point" filterable placeholder="选择POP节点" style="width:100%">
                <el-option v-for="p in popPoints" :key="p.id" :label="popLabel(p)" :value="p.id" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>

        <el-divider content-position="left">硬件规格</el-divider>
        <el-row :gutter="16">
          <!-- 后端字段：vcpu（不是vcpu_count） -->
          <el-col :span="8">
            <el-form-item label="vCPU" prop="vcpu">
              <el-input-number v-model="form.vcpu" :min="1" :max="128" style="width:100%" />
            </el-form-item>
          </el-col>
          <!-- 后端字段：ram_mb（不是memory_mb） -->
          <el-col :span="8">
            <el-form-item label="内存(GB)" prop="ram_mb">
              <el-input-number v-model="ramGb" :min="1" :step="1" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="硬盘(GB)" prop="disk_gb">
              <el-input-number v-model="form.disk_gb" :min="10" style="width:100%" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-divider content-position="left">管理信息</el-divider>
        <el-row :gutter="16">
          <!-- 后端字段：mgmt_ip（不是management_ip） -->
          <el-col :span="12">
            <el-form-item label="管理IP">
              <el-input v-model="form.mgmt_ip" placeholder="如 192.168.1.100" />
            </el-form-item>
          </el-col>
          <!-- 后端字段：mgmt_port（不是ssh_port） -->
          <el-col :span="12">
            <el-form-item label="SSH端口">
              <el-input-number v-model="form.mgmt_port" :min="1" :max="65535" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="管理用户名">
              <el-input v-model="form.mgmt_username" placeholder="root" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="管理密码">
              <el-input v-model="form.mgmt_password" type="password" show-password />
            </el-form-item>
          </el-col>
          <!-- 后端字段：os_type（选择值：debian/ubuntu/centos...）-->
          <el-col :span="12">
            <el-form-item label="操作系统">
              <el-select v-model="form.os_type" clearable placeholder="选择系统" style="width:100%">
                <el-option v-for="o in osOptions" :key="o.value" :label="o.label" :value="o.value" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="系统版本">
              <el-input v-model="form.os_version" placeholder="如 22.04 LTS" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-divider content-position="left">网络/客户</el-divider>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="带宽(Mbps)">
              <el-input-number v-model="form.bandwidth_mbps" :min="1" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="关联客户">
              <el-select v-model="form.customer" filterable clearable placeholder="选择客户" style="width:100%">
                <el-option v-for="c in customers" :key="c.id" :label="c.name" :value="c.id" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>

        <el-divider content-position="left">计费产品</el-divider>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="关联产品">
              <el-select v-model="form.product" filterable clearable placeholder="选择产品（按基础规格+超出计费）"
                style="width:100%" @change="onProductChange">
                <el-option v-for="p in productOptions" :key="p.id"
                  :label="`${p.name}（${p.monthly_price} ${p.currency}/月）`" :value="p.id">
                  <span style="float:left">{{ p.name }}</span>
                  <span style="float:right; color:#909399; font-size:12px; margin-left:12px">
                    {{ p.pop_point_name || '通用' }} · {{ statusTextMap[p.status] || p.status }} · {{ p.monthly_price }}{{ p.currency }}
                  </span>
                </el-option>
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="预计月费">
              <div v-if="billingPreview" class="preview-box">
                <span class="preview-total">{{ billingPreview.monthly_price }} {{ billingPreview.currency }}</span>
                <span class="preview-detail">
                  基础 {{ billingPreview.base_monthly }}
                  <template v-if="Number(billingPreview.overage_monthly) > 0">
                    + 超出 {{ billingPreview.overage_monthly }}
                  </template>
                </span>
              </div>
              <span v-else class="preview-empty">选择产品后预览（保存后按实际规格计费）</span>
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave(false)">确认</el-button>
        <el-button v-if="editRow?.is_synced" type="warning" :loading="saving" @click="handleSave(true)">
          保存并下发带宽
        </el-button>
      </template>
    </el-dialog>

    <VPSProvisionDialog
      v-model="provisionVisible"
      :pop-points="popPoints"
      @created="loadData"
    />
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { gbToMb, mbToGb } from '@/utils/ram'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search, Document, ArrowDown, Monitor, Cpu } from '@element-plus/icons-vue'
import {
  getVPS, createVPS, updateVPS, deleteVPS,
  vpsPower, cloneVPS, syncVPSNow, pushVPSBandwidth, getVPSBillingPreview, getVPSDetail,
} from '@/api/resource'
import { getProductsBrief } from '@/api/product'
import { getCustomers } from '@/api/customer'
import { useRegionGroupedRows, useResourceLocation } from '@/composables/useResourceLocation'
import VPSProvisionDialog from './VPSProvisionDialog.vue'
import ResourceLocationFilter from './components/ResourceLocationFilter.vue'
import ResourceTableGroupLabel from './components/ResourceTableGroupLabel.vue'

const route = useRoute()
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const provisionVisible = ref(false)

function openDetail(row) {
  router.push(`/resource/vps/${row.id}`)
}
const formRef = ref(null)
const editRow = ref(null)
const tableData = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const customers = ref([])
const allProducts = ref([])
const billingPreview = ref(null)
const statusTextMap = { online: '已上线', offline: '已下线', draft: '草稿', archived: '已归档' }

// 展示全部可用 VPS 产品；与当前 POP 匹配的优先排在前面（不做硬过滤，避免空列表）
const productOptions = computed(() => {
  const list = [...allProducts.value]
  if (form.pop_point) {
    list.sort((a, b) => (b.pop_point === form.pop_point ? 1 : 0) - (a.pop_point === form.pop_point ? 1 : 0))
  }
  return list
})

async function onProductChange(productId) {
  billingPreview.value = null
  if (!productId) return
  const prod = allProducts.value.find(p => p.id === productId)
  if (editRow.value) {
    try {
      const res = await getVPSBillingPreview(editRow.value.id, productId)
      if (res && res.monthly_price !== undefined) {
        billingPreview.value = res
        return
      }
    } catch { /* 回退到基础价 */ }
  }
  if (prod) {
    billingPreview.value = {
      base_monthly: prod.monthly_price, overage_monthly: '0',
      monthly_price: prod.monthly_price, currency: prod.currency,
    }
  }
}

const router = useRouter()
const searchForm = reactive({ search: '', status: '', is_synced: '' })
const {
  location, regions, filteredZones, filteredPopPoints,
  popPoints, loadingLocation, popLabel,
  resetLocation, locationParams, loadLocationOptions,
} = useResourceLocation()
const {
  groupedRows: groupedTableData,
  isLocationGroup,
  locationSpanMethod,
} = useRegionGroupedRows(tableData, 13)
const syncingId = ref(null)

function openConsole(row) {
  const r = router.resolve({ name: 'VPSVncConsole', params: { id: row.id } })
  window.open(r.href, '_blank')
}

// 克隆
const cloneVisible = ref(false)
const cloning = ref(false)
const cloneRow = ref(null)
const cloneForm = reactive({ newname: '', newid: null, full: true })

const powerLabels = { start: '启动', shutdown: '优雅关机', stop: '强制停止', restart: '重启' }

async function handlePower(row, action) {
  await ElMessageBox.confirm(
    `确定对虚机 "${row.name}" 执行【${powerLabels[action]}】？`,
    '电源操作', { type: 'warning' },
  )
  try {
    await vpsPower(row.id, action, false)
    ElMessage.success(`已执行${powerLabels[action]}`)
    loadData()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '电源操作失败')
  }
}

function handleClone(row) {
  cloneRow.value = row
  Object.assign(cloneForm, { newname: `${row.name}-clone`, newid: null, full: true })
  cloneVisible.value = true
}

async function doClone() {
  cloning.value = true
  try {
    const payload = { full: cloneForm.full, dry_run: false }
    if (cloneForm.newname) payload.newname = cloneForm.newname
    if (cloneForm.newid) payload.newid = cloneForm.newid
    await cloneVPS(cloneRow.value.id, payload)
    ElMessage.success('克隆已提交')
    cloneVisible.value = false
    loadData()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '克隆失败')
  } finally { cloning.value = false }
}

async function handleSyncNow(row) {
  syncingId.value = row.id
  try {
    const res = await syncVPSNow(row.id)
    if (res?.ok === false) {
      const errMsg = res.detail || Object.values(res.errors || {}).filter(Boolean).join('；')
      ElMessage.error(errMsg || '同步未完成')
      return
    }
    ElMessage.success('已从节点同步')
    loadData()
  } catch (e) {
    const d = e?.response?.data
    ElMessage.error(
      d?.detail
      || Object.values(d?.errors || {}).filter(Boolean).join('；')
      || '同步失败',
    )
  } finally { syncingId.value = null }
}

// 与后端 VPSWriteSerializer.fields 完全对齐
const form = reactive({
  name: '',          // 后端：name（非 hostname）
  status: 'creating',
  pop_point: null,
  vcpu: 2,           // 后端：vcpu（非 vcpu_count）
  ram_mb: 2048,      // 后端：ram_mb（非 memory_mb）
  disk_gb: 40,
  mgmt_ip: '',       // 后端：mgmt_ip（非 management_ip）
  mgmt_username: 'root',
  mgmt_password: '',
  mgmt_port: 22,     // 后端：mgmt_port（非 ssh_port）
  os_type: '',       // 后端：os_type（非 os）
  os_version: '',
  bandwidth_mbps: 100,
  customer: null,
  product: null,
  remark: ''
})
const ramGb = computed({
  get: () => mbToGb(form.ram_mb),
  set: (gb) => { form.ram_mb = gbToMb(gb) },
})

const statusOptions = [
  { value: 'running', label: '运行中' }, { value: 'stopped', label: '已停止' },
  { value: 'suspended', label: '已暂停' }, { value: 'creating', label: '创建中' },
  { value: 'deleting', label: '删除中' }, { value: 'error', label: '故障' }
]
const statusMap = {
  running: { label: '运行中', type: 'success' }, stopped: { label: '已停止', type: 'info' },
  suspended: { label: '已暂停', type: 'warning' }, creating: { label: '创建中', type: 'primary' },
  deleting: { label: '删除中', type: 'warning' }, error: { label: '故障', type: 'danger' }
}
const osOptions = [
  { value: 'debian', label: 'Debian' }, { value: 'ubuntu', label: 'Ubuntu' },
  { value: 'centos', label: 'CentOS' }, { value: 'rhel', label: 'RHEL' },
  { value: 'windows', label: 'Windows Server' }, { value: 'other', label: '其他' }
]
const osTypeMap = { debian: 'Debian', ubuntu: 'Ubuntu', centos: 'CentOS', rhel: 'RHEL', windows: 'Windows', other: '其他' }

const formRules = {
  name: [{ required: true, message: '请输入VPS名称' }],
  pop_point: [{ required: true, message: '请选择POP节点' }],
  vcpu: [{ required: true, type: 'number', message: '请设置vCPU' }],
  ram_mb: [{ required: true, type: 'number', message: '请设置内存' }],
  disk_gb: [{ required: true, type: 'number', message: '请设置硬盘' }]
}

async function loadData() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value, ...locationParams() }
    if (searchForm.search) params.search = searchForm.search
    if (searchForm.status) params.status = searchForm.status
    if (searchForm.is_synced !== '' && searchForm.is_synced !== null) params.is_synced = searchForm.is_synced
    const res = await getVPS(params)
    tableData.value = res?.results || []
    total.value = res?.count || 0
  } finally { loading.value = false }
}

function applyLocation(next) {
  Object.assign(location, next)
}

function reloadForLocation() {
  page.value = 1
  loadData()
}

function resetSearch() {
  Object.assign(searchForm, { search: '', status: '', is_synced: '' })
  resetLocation()
  loadData()
}

function openDialog(row = null) {
  editRow.value = row
  if (row) {
    Object.assign(form, {
      name: row.name,
      status: row.status,
      pop_point: row.pop_point,
      vcpu: row.vcpu,
      ram_mb: row.ram_mb,
      disk_gb: row.disk_gb,
      mgmt_ip: row.mgmt_ip || '',
      mgmt_username: row.mgmt_username || 'root',
      mgmt_password: '',
      mgmt_port: row.mgmt_port || 22,
      os_type: row.os_type || '',
      os_version: row.os_version || '',
      bandwidth_mbps: row.bandwidth_mbps || 100,
      customer: row.customer || null,
      product: row.product || null,
      remark: row.remark || ''
    })
    billingPreview.value = row.product_billing || null
  } else {
    Object.assign(form, {
      name: '', status: 'creating', pop_point: location.pop_point || null, vcpu: 2, ram_mb: 2048, disk_gb: 40,
      mgmt_ip: '', mgmt_username: 'root', mgmt_password: '', mgmt_port: 22,
      os_type: '', os_version: '', bandwidth_mbps: 100, customer: null, product: null, remark: ''
    })
    billingPreview.value = null
  }
  dialogVisible.value = true
}

async function handleSave(pushToNode = false) {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    const payload = { ...form }
    if (!payload.mgmt_password) delete payload.mgmt_password
    if (editRow.value) {
      await updateVPS(editRow.value.id, payload, pushToNode)
      ElMessage.success(pushToNode ? '已更新并下发带宽到节点' : '已更新')
    } else {
      await createVPS(payload)
      ElMessage.success('VPS创建成功')
    }
    dialogVisible.value = false
    loadData()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally { saving.value = false }
}

async function handleDelete(row) {
  let deleteOnNode = false
  if (row.is_synced && row.source_ref) {
    // 同步虚机：询问是否联动删除节点上的真实虚机
    try {
      await ElMessageBox.confirm(
        `虚机 "${row.name}" 来自节点 PVE #${row.source_vmid}。是否同时删除节点上的真实虚机？\n\n` +
        `点「删除节点虚机」将不可恢复地销毁节点 VM；点「仅移除记录」只删除本地档案。`,
        '删除同步虚机',
        {
          type: 'warning',
          distinguishCancelAndClose: true,
          confirmButtonText: '删除节点虚机',
          cancelButtonText: '仅移除记录',
        },
      )
      deleteOnNode = true
    } catch (act) {
      if (act === 'close') return  // 关闭弹窗 = 取消
      deleteOnNode = false         // 点击「仅移除记录」
    }
  } else {
    await ElMessageBox.confirm(`确定删除VPS "${row.name}"？`, '警告', { type: 'warning' })
  }
  try {
    await deleteVPS(row.id, deleteOnNode)
    ElMessage.success(deleteOnNode ? '已删除节点虚机及记录' : '已删除')
    loadData()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '删除失败')
  }
}

onMounted(async () => {
  loadData()
  const [cRes, , prodRes] = await Promise.allSettled([
    getCustomers({ page_size: 200 }),
    loadLocationOptions(),
    getProductsBrief({ category: 'vps', status: 'all' })
  ])
  customers.value = cRes.status === 'fulfilled' ? cRes.value?.results || [] : []
  allProducts.value = prodRes.status === 'fulfilled' ? (prodRes.value || []) : []

  // 从详情页「编辑」返回时自动打开编辑弹窗
  const editId = route.query.edit
  if (editId != null) {
    try {
      const row = await getVPSDetail(editId)
      openDialog(row)
    } catch { /* ignore */ }
    router.replace({ path: '/resource/vps' })
  }
})
</script>

<style scoped>
.page-container { min-height: 100%; }
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.header-actions { display: flex; gap: 8px; }
.page-title { font-size: 18px; font-weight: 700; color: #1a1a2e; margin: 0; }
.search-card :deep(.el-card__body) { padding: 16px 16px 0; }
.pagination { margin-top: 16px; justify-content: flex-end; }
.spec-badge { display: inline-block; background: #f0f2f5; border-radius: 4px; padding: 1px 6px; font-size: 11px; margin-right: 4px; color: #606266; }
.prod-name { font-size: 13px; color: #1a1a2e; }
.prod-fee { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #67c23a; margin-top: 2px; }
.preview-box { display: flex; flex-direction: column; line-height: 1.4; }
.preview-total { font-size: 16px; font-weight: 700; color: #409eff; }
.preview-detail { font-size: 12px; color: #909399; }
.preview-empty { font-size: 12px; color: #c0c4cc; }
</style>
