<template>
  <div class="page-container">
    <div class="page-header">
      <h2 class="page-title">带宽管理</h2>
      <el-button type="primary" :icon="Plus" @click="openDialog()">新建分配</el-button>
    </div>

    <!-- KPI -->
    <el-row :gutter="12" class="kpi-row">
      <el-col :span="6">
        <el-card shadow="never"><div class="kpi"><div class="kpi-num">{{ stats.total || 0 }}</div><div class="kpi-label">分配记录</div></div></el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never"><div class="kpi"><div class="kpi-num">{{ stats.synced || 0 }}</div><div class="kpi-label">节点同步</div></div></el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never"><div class="kpi"><div class="kpi-num">{{ stats.total_allocated_mbps || 0 }}</div><div class="kpi-label">已分配 Mbps</div></div></el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never"><div class="kpi"><div class="kpi-num">{{ vpsMbps }}/{{ edgeMbps }}</div><div class="kpi-label">VPS / 边缘 Mbps</div></div></el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="search-card">
      <el-form :model="searchForm" inline>
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
        <el-form-item label="目标类型">
          <el-select v-model="searchForm.target_type" clearable placeholder="全部" style="width:120px" @change="loadData">
            <el-option label="VPS" value="vps" />
            <el-option label="边缘加速" value="edge" />
          </el-select>
        </el-form-item>
        <el-form-item label="来源">
          <el-select v-model="searchForm.is_synced" clearable placeholder="全部" style="width:120px" @change="loadData">
            <el-option label="节点同步" :value="true" />
            <el-option label="手工建档" :value="false" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button @click="resetSearch">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" style="margin-top:12px;">
      <el-table :data="groupedTableData" v-loading="loading" stripe border :span-method="locationSpanMethod">
        <el-table-column label="POP节点" min-width="140">
          <template #default="{ row }">
            <ResourceTableGroupLabel v-if="isLocationGroup(row)" :row="row" />
            <span v-else>{{ popName(row.pop_point) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="区域 / 城市" min-width="150">
          <template #default="{ row }">
            {{ [row.region_name, row.country_name, row.city_name].filter(Boolean).join(' / ') || '-' }}
          </template>
        </el-table-column>
        <el-table-column prop="target_type_label" label="类型" width="100" />
        <el-table-column prop="target_name" label="目标" min-width="180" />
        <el-table-column prop="allocated_mbps" label="带宽(Mbps)" width="120" align="right" />
        <el-table-column prop="burst_mbps" label="突发(Mbps)" width="120" align="right" />
        <el-table-column label="来源" width="170">
          <template #default="{ row }">
            <el-tag v-if="row.is_synced" type="success" size="small" effect="plain">
              同步{{ row.source_interface_code ? ' ' + row.source_interface_code : '' }}
            </el-tag>
            <el-tag v-else type="info" size="small" effect="plain">手工</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="生效" width="80">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'" size="small">{{ row.is_active ? '是' : '否' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <template v-if="!row.is_synced">
              <el-button link type="primary" size="small" @click="openDialog(row)">编辑</el-button>
              <el-button link type="danger" size="small" @click="handleDelete(row)">删除</el-button>
            </template>
            <span v-else class="muted">节点同步</span>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total"
        layout="total, prev, pager, next" class="pagination" @change="loadData" />
    </el-card>

    <el-dialog v-model="dialogVisible" :title="editRow ? '编辑带宽分配' : '新建带宽分配'" width="520px" destroy-on-close>
      <el-form ref="formRef" :model="form" :rules="formRules" label-width="100px">
        <el-form-item label="POP节点" prop="pop_point">
          <el-select v-model="form.pop_point" filterable placeholder="选择POP节点" style="width:100%" @change="onPopChange">
            <el-option v-for="p in popPoints" :key="p.id" :label="popLabel(p)" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="目标类型" prop="target_type">
          <el-radio-group v-model="form.target_type" @change="form.vps = null; form.edge = null">
            <el-radio label="vps">VPS</el-radio>
            <el-radio label="edge">边缘加速</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.target_type === 'vps'" label="目标 VPS" prop="vps">
          <el-select v-model="form.vps" filterable placeholder="选择VPS" style="width:100%">
            <el-option v-for="v in vpsOptions" :key="v.id" :label="`${v.name}（${v.vps_no}）`" :value="v.id" />
          </el-select>
        </el-form-item>
        <el-form-item v-else label="目标边缘" prop="edge">
          <el-select v-model="form.edge" filterable placeholder="选择边缘加速" style="width:100%">
            <el-option v-for="e in edgeOptions" :key="e.id" :label="`${e.name}（${e.edge_no}）`" :value="e.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="带宽(Mbps)" prop="allocated_mbps">
          <el-input-number v-model="form.allocated_mbps" :min="1" style="width:100%" />
        </el-form-item>
        <el-form-item label="突发(Mbps)">
          <el-input-number v-model="form.burst_mbps" :min="0" style="width:100%" />
        </el-form-item>
        <el-form-item label="生效">
          <el-switch v-model="form.is_active" />
        </el-form-item>
        <el-form-item label="同步到节点">
          <el-switch v-model="form.push_to_node" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">确认</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import {
  getBandwidthAllocations, createBandwidthAllocation, updateBandwidthAllocation,
  deleteBandwidthAllocation, getBandwidthStatistics,
  getVPS, getEdgeAccelerations,
} from '@/api/resource'
import { useRegionGroupedRows, useResourceLocation } from '@/composables/useResourceLocation'
import ResourceLocationFilter from './components/ResourceLocationFilter.vue'
import ResourceTableGroupLabel from './components/ResourceTableGroupLabel.vue'

const loading = ref(false)
const saving = ref(false)
const tableData = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const stats = ref({})
const vpsOptions = ref([])
const edgeOptions = ref([])
const dialogVisible = ref(false)
const editRow = ref(null)
const formRef = ref(null)

const searchForm = reactive({ target_type: '', is_synced: '' })
const {
  location, regions, filteredZones, filteredPopPoints,
  popPoints, loadingLocation, popLabel,
  resetLocation, locationParams, loadLocationOptions,
} = useResourceLocation()
const {
  groupedRows: groupedTableData,
  isLocationGroup,
  locationSpanMethod,
} = useRegionGroupedRows(tableData, 9)
const form = reactive({
  pop_point: null, target_type: 'vps', vps: null, edge: null,
  allocated_mbps: 100, burst_mbps: 0, is_active: true, push_to_node: true,
})
const formRules = {
  pop_point: [{ required: true, message: '请选择POP节点' }],
  target_type: [{ required: true, message: '请选择目标类型' }],
  allocated_mbps: [{ required: true, type: 'number', message: '请设置带宽' }],
}

const vpsMbps = computed(() => {
  const r = (stats.value.by_target_type || []).find(x => x.target_type === 'vps')
  return r?.mbps || 0
})
const edgeMbps = computed(() => {
  const r = (stats.value.by_target_type || []).find(x => x.target_type === 'edge')
  return r?.mbps || 0
})

function popName(id) {
  const p = popPoints.value.find(x => x.id === id)
  return p ? p.name : id
}

function applyLocation(next) {
  Object.assign(location, next)
}

function reloadForLocation() {
  page.value = 1
  loadData()
}

async function loadStats() {
  try {
    const params = locationParams()
    stats.value = await getBandwidthStatistics(params) || {}
  } catch { stats.value = {} }
}

async function loadData() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value, ...locationParams() }
    if (searchForm.target_type) params.target_type = searchForm.target_type
    if (searchForm.is_synced !== '' && searchForm.is_synced !== null) params.is_synced = searchForm.is_synced
    const res = await getBandwidthAllocations(params)
    tableData.value = res?.results || []
    total.value = res?.count || 0
  } finally { loading.value = false }
  loadStats()
}

function resetSearch() {
  Object.assign(searchForm, { target_type: '', is_synced: '' })
  resetLocation()
  loadData()
}

async function onPopChange() {
  form.vps = null
  form.edge = null
  await loadTargets(form.pop_point)
}

async function loadTargets(popId) {
  if (!popId) { vpsOptions.value = []; edgeOptions.value = []; return }
  const [v, e] = await Promise.allSettled([
    getVPS({ pop_point: popId, page_size: 300 }),
    getEdgeAccelerations({ pop_point: popId, page_size: 300 }),
  ])
  vpsOptions.value = v.status === 'fulfilled' ? v.value?.results || [] : []
  edgeOptions.value = e.status === 'fulfilled' ? e.value?.results || [] : []
}

function openDialog(row = null) {
  editRow.value = row
  if (row) {
    Object.assign(form, {
      pop_point: row.pop_point, target_type: row.target_type,
      vps: row.vps, edge: row.edge,
      allocated_mbps: row.allocated_mbps, burst_mbps: row.burst_mbps,
      is_active: row.is_active, push_to_node: true,
    })
    loadTargets(row.pop_point)
  } else {
    Object.assign(form, {
      pop_point: location.pop_point || null, target_type: 'vps', vps: null, edge: null,
      allocated_mbps: 100, burst_mbps: 0, is_active: true, push_to_node: true,
    })
  }
  dialogVisible.value = true
}

async function handleSave() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  if (form.target_type === 'vps' && !form.vps) return ElMessage.warning('请选择目标VPS')
  if (form.target_type === 'edge' && !form.edge) return ElMessage.warning('请选择目标边缘加速')
  saving.value = true
  try {
    const payload = {
      pop_point: form.pop_point, target_type: form.target_type,
      vps: form.target_type === 'vps' ? form.vps : null,
      edge: form.target_type === 'edge' ? form.edge : null,
      allocated_mbps: form.allocated_mbps, burst_mbps: form.burst_mbps,
      is_active: form.is_active,
    }
    if (editRow.value) {
      await updateBandwidthAllocation(editRow.value.id, payload, form.push_to_node)
      ElMessage.success(form.push_to_node ? '已更新并尝试下发节点' : '已更新')
    } else {
      await createBandwidthAllocation(payload, form.push_to_node)
      ElMessage.success(form.push_to_node ? '已创建并尝试下发节点' : '已创建')
    }
    dialogVisible.value = false
    loadData()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || JSON.stringify(e?.response?.data || '') || '保存失败')
  } finally { saving.value = false }
}

async function handleDelete(row) {
  await ElMessageBox.confirm('确定删除该带宽分配？', '警告', { type: 'warning' })
  await deleteBandwidthAllocation(row.id, true)
  ElMessage.success('已删除')
  loadData()
}

onMounted(async () => {
  await loadLocationOptions()
  loadData()
})
</script>

<style scoped>
.page-container { min-height: 100%; }
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.page-title { font-size: 18px; font-weight: 700; color: #1a1a2e; margin: 0; }
.kpi-row { margin-bottom: 12px; }
.kpi { text-align: center; padding: 6px 0; }
.kpi-num { font-size: 24px; font-weight: 700; color: #1a1a2e; }
.kpi-label { font-size: 12px; color: #909399; margin-top: 4px; }
.search-card :deep(.el-card__body) { padding: 16px 16px 0; }
.pagination { margin-top: 16px; justify-content: flex-end; }
.muted { color: #c0c4cc; font-size: 12px; }
</style>
