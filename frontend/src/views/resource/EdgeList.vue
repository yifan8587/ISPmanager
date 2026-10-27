<template>
  <div class="page-container">
    <div class="page-header">
      <h2 class="page-title">边缘加速管理</h2>
      <el-button type="primary" :icon="Plus" @click="openDialog()">新建加速节点</el-button>
    </div>

    <el-card shadow="never" class="search-card">
      <el-form :model="searchForm" inline>
        <el-form-item label="关键词">
          <el-input v-model="searchForm.search" placeholder="编号/名称" clearable style="width:180px" @keyup.enter="loadData" />
        </el-form-item>
        <!-- 后端过滤字段：protocol（非 acceleration_type） -->
        <el-form-item label="协议">
          <el-select v-model="searchForm.protocol" clearable placeholder="全部" style="width:130px">
            <el-option v-for="p in protocolOptions" :key="p.value" :label="p.label" :value="p.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="searchForm.status" clearable placeholder="全部" style="width:110px">
            <el-option v-for="s in statusOptions" :key="s.value" :label="s.label" :value="s.value" />
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
        <el-table-column label="加速编号" width="170">
          <template #default="{ row }">
            <ResourceTableGroupLabel v-if="isLocationGroup(row)" :row="row" />
            <span v-else>{{ row.edge_no }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="名称" min-width="150" />
        <!-- 后端字段：protocol（非 acceleration_type） -->
        <el-table-column label="隧道协议" width="130">
          <template #default="{ row }">
            <el-tag :type="protocolMap[row.protocol]?.type" size="small">{{ protocolMap[row.protocol]?.label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusMap[row.status]?.type" size="small">{{ statusMap[row.status]?.label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="POP节点" width="140">
          <template #default="{ row }">{{ row.pop_name || '-' }}</template>
        </el-table-column>
        <el-table-column label="区域 / 城市" min-width="150">
          <template #default="{ row }">
            {{ [row.region_name, row.country_name, row.city_name].filter(Boolean).join(' / ') || '-' }}
          </template>
        </el-table-column>
        <el-table-column prop="bandwidth_mbps" label="带宽" width="100" align="right">
          <template #default="{ row }">{{ row.bandwidth_mbps }} Mbps</template>
        </el-table-column>
        <el-table-column prop="customer_name" label="关联客户" width="150">
          <template #default="{ row }">
            <span v-if="row.customer_name">{{ row.customer_no ? `${row.customer_no} · ` : '' }}{{ row.customer_name }}</span>
            <span v-else style="color:#C0C4CC">-</span>
          </template>
        </el-table-column>
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
        <el-table-column label="下发" width="90" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.protocol === 'socks5'" size="small" type="info">N/A</el-tag>
            <el-tag v-else-if="row.is_synced" size="small" type="success">已下发</el-tag>
            <el-tag v-else size="small" type="warning">待下发</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="IP列表" min-width="200">
          <template #default="{ row }">
            <el-tag v-for="ip in row.ip_list" :key="ip.id" size="small" style="margin:1px;">
              {{ ip.address }}/{{ ip.prefix_len }}
            </el-tag>
            <span v-if="!row.ip_list?.length" style="color:#C0C4CC">无IP</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="185" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">
              <el-icon><Document/></el-icon> 详情
            </el-button>
            <el-button link type="primary" size="small" @click="openDialog(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total"
        layout="total, prev, pager, next" class="pagination" @change="loadData" />
    </el-card>

    <el-dialog v-model="dialogVisible" :title="editRow ? '编辑加速节点' : '新建加速节点'" width="600px" destroy-on-close>
      <el-form ref="formRef" :model="form" :rules="formRules" label-width="110px">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="节点名称" prop="name">
              <el-input v-model="form.name" placeholder="如 Tokyo-GRE-01" />
            </el-form-item>
          </el-col>
          <!-- 后端字段：protocol（非 acceleration_type） -->
          <el-col :span="12">
            <el-form-item label="隧道协议" prop="protocol">
              <el-select v-model="form.protocol" placeholder="选择协议" style="width:100%">
                <el-option v-for="p in protocolOptions" :key="p.value" :label="p.label" :value="p.value" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="POP节点" prop="pop_point">
              <el-select v-model="form.pop_point" filterable placeholder="选择POP节点" style="width:100%">
                <el-option v-for="p in popPoints" :key="p.id" :label="popLabel(p)" :value="p.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="状态">
              <el-select v-model="form.status" style="width:100%">
                <el-option v-for="s in statusOptions" :key="s.value" :label="s.label" :value="s.value" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="带宽(Mbps)">
              <el-input-number v-model="form.bandwidth_mbps" :min="1" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="关联客户">
              <el-select v-model="form.customer" filterable clearable placeholder="可选" style="width:100%">
                <el-option v-for="c in customers" :key="c.id"
                  :label="c.customer_no ? `${c.customer_no} · ${c.name}` : c.name" :value="c.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="关联产品">
              <el-select v-model="form.product" filterable clearable placeholder="按基础规格+超出计费"
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
                  <template v-if="Number(billingPreview.overage_monthly) > 0">+ 超出 {{ billingPreview.overage_monthly }}</template>
                </span>
              </div>
              <span v-else class="preview-empty">选择产品后预览</span>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="开通时间">
              <el-date-picker v-model="form.start_time" type="datetime" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="到期时间">
              <el-date-picker v-model="form.expire_time" type="datetime" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item label="备注">
              <el-input v-model="form.remark" type="textarea" :rows="2" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave(false)">
          {{ editRow ? '仅保存' : '创建并配置协议' }}
        </el-button>
        <el-button v-if="editRow" type="warning" :loading="saving" @click="handleSave(true)">
          保存并打开详情
        </el-button>
      </template>
    </el-dialog>

  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search, Document } from '@element-plus/icons-vue'
import { getEdge, createEdge, updateEdge, deleteEdge, getEdgeBillingPreview, getEdgeDetail } from '@/api/resource'
import { getProductsBrief } from '@/api/product'
import { getCustomers } from '@/api/customer'
import { useRegionGroupedRows, useResourceLocation } from '@/composables/useResourceLocation'
import ResourceLocationFilter from './components/ResourceLocationFilter.vue'
import ResourceTableGroupLabel from './components/ResourceTableGroupLabel.vue'

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)

function openDetail(row) {
  router.push(`/resource/edge/${row.id}`)
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

// 展示全部可用边缘加速产品；与当前 POP 匹配的优先排序（不做硬过滤）
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
      const res = await getEdgeBillingPreview(editRow.value.id, productId)
      if (res && res.monthly_price !== undefined) { billingPreview.value = res; return }
    } catch { /* 回退到基础价 */ }
  }
  if (prod) {
    billingPreview.value = {
      base_monthly: prod.monthly_price, overage_monthly: '0',
      monthly_price: prod.monthly_price, currency: prod.currency,
    }
  }
}

const searchForm = reactive({
  search: '',
  protocol: '',  // 后端过滤字段：protocol（非 acceleration_type）
  status: ''
})
const {
  location, regions, filteredZones, filteredPopPoints,
  popPoints, loadingLocation, popLabel,
  resetLocation, locationParams, loadLocationOptions,
} = useResourceLocation()
const {
  groupedRows: groupedTableData,
  isLocationGroup,
  locationSpanMethod,
} = useRegionGroupedRows(tableData, 12)

// 与后端 EdgeAccelerationWriteSerializer.fields 完全对齐
// 后端字段：protocol（非 acceleration_type）
const form = reactive({
  name: '',
  protocol: '',   // 后端：protocol（非 acceleration_type）
  status: 'pending',
  pop_point: null,
  bandwidth_mbps: 100,
  route_table: null,
  customer: null,
  contract: null,
  product: null,
  start_time: null,
  expire_time: null,
  remark: ''
})

const protocolOptions = [
  { value: 'gre', label: 'GRE' },
  { value: 'vxlan', label: 'VXLAN' },
  { value: 'wireguard', label: 'WireGuard' },
  { value: 'socks5', label: 'Socks5' }
]
const protocolMap = {
  gre: { label: 'GRE', type: '' },
  vxlan: { label: 'VXLAN', type: 'primary' },
  wireguard: { label: 'WireGuard', type: 'success' },
  socks5: { label: 'Socks5', type: 'warning' }
}
const statusOptions = [
  { value: 'active', label: '运行中' }, { value: 'stopped', label: '已停止' },
  { value: 'pending', label: '待配置' }, { value: 'error', label: '故障' }
]
const statusMap = {
  active: { label: '运行中', type: 'success' }, stopped: { label: '已停止', type: 'info' },
  pending: { label: '待配置', type: 'warning' }, error: { label: '故障', type: 'danger' }
}

const formRules = {
  name: [{ required: true, message: '请输入节点名称' }],
  protocol: [{ required: true, message: '请选择隧道协议' }],
  pop_point: [{ required: true, message: '请选择POP节点' }]
}

async function loadData() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value, ...locationParams() }
    if (searchForm.search) params.search = searchForm.search
    // 使用后端正确的过滤字段：protocol
    if (searchForm.protocol) params.protocol = searchForm.protocol
    if (searchForm.status) params.status = searchForm.status
    const res = await getEdge(params)
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
  Object.assign(searchForm, { search: '', protocol: '', status: '' })
  resetLocation()
  loadData()
}

function openDialog(row = null) {
  editRow.value = row
  if (row) {
    Object.assign(form, {
      name: row.name,
      protocol: row.protocol,   // 后端：protocol
      status: row.status,
      pop_point: row.pop_point,
      bandwidth_mbps: row.bandwidth_mbps || 100,
      route_table: row.route_table || null,
      customer: row.customer || null,
      contract: row.contract || null,
      product: row.product || null,
      start_time: row.start_time || null,
      expire_time: row.expire_time || null,
      remark: row.remark || ''
    })
    billingPreview.value = row.product_billing || null
  } else {
    Object.assign(form, {
      name: '', protocol: '', status: 'pending', pop_point: location.pop_point || null,
      bandwidth_mbps: 100, route_table: null, customer: null, contract: null,
      product: null, start_time: null, expire_time: null, remark: ''
    })
    billingPreview.value = null
  }
  dialogVisible.value = true
}

async function handleSave(openDetailAfter = false) {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    if (editRow.value) {
      await updateEdge(editRow.value.id, form)
      ElMessage.success('已更新')
      dialogVisible.value = false
      if (openDetailAfter) {
        router.push(`/resource/edge/${editRow.value.id}`)
        return
      }
    } else {
      const created = await createEdge(form)
      ElMessage.success('加速节点已创建，请配置协议参数后下发到节点')
      dialogVisible.value = false
      const newId = created?.id
      if (newId) {
        tabHint()
        router.push(`/resource/edge/${newId}`)
        return
      }
    }
    loadData()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally { saving.value = false }
}

function tabHint() {
  ElMessage.info('请在详情页「协议配置」中填写隧道参数，再点击「保存并下发」')
}

async function handleDelete(row) {
  await ElMessageBox.confirm(`确定删除加速节点 "${row.name}"？`, '警告', { type: 'warning' })
  await deleteEdge(row.id)
  ElMessage.success('已删除')
  loadData()
}

onMounted(async () => {
  loadData()
  const [, cRes, prodRes] = await Promise.allSettled([
    loadLocationOptions(),
    getCustomers({ page_size: 200 }),
    getProductsBrief({ category: 'edge', status: 'all' })
  ])
  customers.value = cRes.status === 'fulfilled' ? cRes.value?.results || [] : []
  allProducts.value = prodRes.status === 'fulfilled' ? (prodRes.value || []) : []

  // 从详情页「编辑」返回时自动打开编辑弹窗
  const editId = route.query.edit
  if (editId != null) {
    try {
      const row = await getEdgeDetail(editId)
      openDialog(row)
    } catch { /* ignore */ }
    router.replace({ path: '/resource/edge' })
  }
})
</script>

<style scoped>
.page-container { min-height: 100%; }
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.page-title { font-size: 18px; font-weight: 700; color: #1a1a2e; margin: 0; }
.search-card :deep(.el-card__body) { padding: 16px 16px 0; }
.pagination { margin-top: 16px; justify-content: flex-end; }
.prod-name { font-size: 13px; color: #1a1a2e; }
.prod-fee { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #67c23a; margin-top: 2px; }
.preview-box { display: flex; flex-direction: column; line-height: 1.4; }
.preview-total { font-size: 16px; font-weight: 700; color: #409eff; }
.preview-detail { font-size: 12px; color: #909399; }
.preview-empty { font-size: 12px; color: #c0c4cc; }
</style>
