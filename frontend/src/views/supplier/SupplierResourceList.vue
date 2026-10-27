<template>
  <div class="page-container">
    <div class="page-header">
      <h2 class="page-title">供应商资源</h2>
    </div>

    <el-card shadow="never" class="search-card">
      <el-form :model="searchForm" inline>
        <el-form-item label="供应商">
          <el-select v-model="searchForm.supplier" clearable filterable placeholder="全部" style="width:180px" @change="loadData">
            <el-option v-for="s in supplierOptions" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="searchForm.resource_type" clearable placeholder="全部" style="width:120px" @change="loadData">
            <el-option v-for="t in typeOptions" :key="t.value" :label="t.label" :value="t.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="同步状态">
          <el-select v-model="searchForm.sync_status" clearable placeholder="全部" style="width:120px" @change="loadData">
            <el-option v-for="s in syncOptions" :key="s.value" :label="s.label" :value="s.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="关键词">
          <el-input v-model="searchForm.search" placeholder="资源ID/名称/地区" clearable style="width:170px" @keyup.enter="loadData" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="loadData">搜索</el-button>
          <el-button @click="resetSearch">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" style="margin-top:12px;">
      <el-table :data="tableData" v-loading="loading" stripe border>
        <el-table-column prop="supplier_name" label="供应商" width="140" />
        <el-table-column prop="external_id" label="资源ID" width="130" />
        <el-table-column prop="name" label="名称" min-width="140" />
        <el-table-column label="类型" width="90"><template #default="{ row }"><el-tag size="small">{{ row.resource_type_label }}</el-tag></template></el-table-column>
        <el-table-column label="规格" min-width="180">
          <template #default="{ row }">{{ row.cpu_cores || '-' }}核/{{ ramText(row.ram_mb) }}/{{ row.disk_gb || '-' }}G/{{ row.bandwidth_mbps || '-' }}M/{{ row.ip_count || '-' }}IP</template>
        </el-table-column>
        <el-table-column label="地区" width="120"><template #default="{ row }">{{ row.region_text || '-' }}</template></el-table-column>
        <el-table-column label="映射POP" width="130"><template #default="{ row }">{{ row.pop_name || '未映射' }}</template></el-table-column>
        <el-table-column label="成本/月" width="100" align="right"><template #default="{ row }">{{ row.cost_price ? `¥${row.cost_price}` : '-' }}</template></el-table-column>
        <el-table-column label="状态" width="90"><template #default="{ row }"><el-tag size="small" :type="syncColor[row.sync_status]">{{ row.sync_status_label }}</el-tag></template></el-table-column>
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openMap(row)">映射/同步</el-button>
            <el-button link type="danger" size="small" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total"
        layout="total, prev, pager, next" class="pagination" @change="loadData" />
    </el-card>

    <!-- 映射/同步 -->
    <el-dialog v-model="mapVisible" title="映射资源到 POP / 同步产品" width="520px" destroy-on-close>
      <el-form ref="mapRef" :model="mapForm" :rules="mapRules" label-width="120px">
        <el-form-item label="资源">
          <span>{{ mapRow?.name || mapRow?.external_id }}（{{ mapRow?.resource_type_label }}）</span>
        </el-form-item>
        <el-form-item label="映射到POP点" prop="pop_point_id">
          <el-select v-model="mapForm.pop_point_id" filterable placeholder="选择本地POP点" style="width:100%">
            <el-option v-for="p in popOptions" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="同步为本地产品">
          <el-switch v-model="mapForm.to_product" />
          <span class="muted" style="margin-left:8px">开启后将按资源规格在该POP创建/更新产品</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="mapVisible = false">取消</el-button>
        <el-button type="primary" :loading="mapSaving" @click="saveMap">确认</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ramText } from '@/utils/ram'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { getResources, mapResource, deleteResource, getSuppliersBrief } from '@/api/supplier'
import { getPopPoints } from '@/api/resource'

const loading = ref(false)
const tableData = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const supplierOptions = ref([])
const popOptions = ref([])

const typeOptions = [
  { value: 'vps', label: 'VPS' }, { value: 'edge', label: '边缘加速' }, { value: 'ip', label: 'IP资源' },
  { value: 'bandwidth', label: '带宽' }, { value: 'other', label: '其他' },
]
const syncOptions = [
  { value: 'pending', label: '待处理' }, { value: 'synced', label: '已接收' },
  { value: 'mapped', label: '已映射' }, { value: 'error', label: '异常' },
]
const syncColor = { pending: 'info', synced: 'primary', mapped: 'success', error: 'danger' }

const searchForm = reactive({ supplier: '', resource_type: '', sync_status: '', search: '' })

async function loadOptions() {
  const [sups, pops] = await Promise.all([
    getSuppliersBrief().catch(() => null),
    getPopPoints({ page_size: 200 }).catch(() => null),
  ])
  supplierOptions.value = sups || []
  popOptions.value = pops?.results || pops || []
}

async function loadData() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (searchForm.supplier) params.supplier = searchForm.supplier
    if (searchForm.resource_type) params.resource_type = searchForm.resource_type
    if (searchForm.sync_status) params.sync_status = searchForm.sync_status
    if (searchForm.search) params.search = searchForm.search
    const res = await getResources(params)
    tableData.value = res?.results || []
    total.value = res?.count || 0
  } finally { loading.value = false }
}

function resetSearch() { Object.assign(searchForm, { supplier: '', resource_type: '', sync_status: '', search: '' }); loadData() }

// ── 映射 ──────────────────────────────────────────────────
const mapVisible = ref(false)
const mapSaving = ref(false)
const mapRef = ref(null)
const mapRow = ref(null)
const mapForm = reactive({ pop_point_id: null, to_product: false })
const mapRules = { pop_point_id: [{ required: true, message: '请选择POP点' }] }

function openMap(row) {
  mapRow.value = row
  Object.assign(mapForm, { pop_point_id: row.pop_point || null, to_product: false })
  mapVisible.value = true
}

async function saveMap() {
  const valid = await mapRef.value.validate().catch(() => false)
  if (!valid) return
  mapSaving.value = true
  try {
    const res = await mapResource(mapRow.value.id, { ...mapForm })
    ElMessage.success(res?.product_no ? `已同步产品 ${res.product_no}` : '已映射')
    mapVisible.value = false
    loadData()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '操作失败')
  } finally { mapSaving.value = false }
}

async function handleDelete(row) {
  await ElMessageBox.confirm(`确定删除资源 "${row.name || row.external_id}"？`, '警告', { type: 'warning' })
  await deleteResource(row.id)
  ElMessage.success('已删除')
  loadData()
}

onMounted(() => { loadOptions(); loadData() })
</script>

<style scoped>
.page-container { min-height: 100%; }
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.page-title { font-size: 18px; font-weight: 700; color: #1a1a2e; margin: 0; }
.search-card :deep(.el-card__body) { padding: 16px 16px 0; }
.pagination { margin-top: 16px; justify-content: flex-end; }
.muted { color: #909399; font-size: 12px; }
</style>
