<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <h2 class="page-title">隧道 IP 地址段</h2>
        <p class="page-desc">
          边缘加速隧道接口（GRE / VXLAN / WireGuard）的点对点 IP 池：在父网段内按 /30 依次切块
          （.1/.2 → .5/.6，步进 4），本端 / 对端各占一个可用地址。占用情况按所属 POP 下 IP 状态实时统计。
        </p>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;">
        <ExcelImportExport
          export-url="/api/resource/tunnel-ip-pools/export/"
          template-url="/api/resource/tunnel-ip-pools/import-template/"
          import-url="/api/resource/tunnel-ip-pools/import/"
          filename="隧道地址段.xlsx"
          :params="excelParams"
          @imported="load"
        />
        <el-button type="primary" :icon="Plus" @click="openDialog()">新建地址段</el-button>
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>

    <div style="margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap;">
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
      <el-select v-model="search.is_synced" clearable placeholder="来源" style="width:140px" @change="load">
        <el-option label="节点同步" :value="true" />
        <el-option label="中央新建" :value="false" />
      </el-select>
      <el-input v-model="search.search" placeholder="名称 / CIDR / 备注" clearable style="width:200px" @keyup.enter="load" />
      <el-button :icon="Search" @click="load">搜索</el-button>
    </div>

    <el-card shadow="never">
      <el-table :data="groupedRows" v-loading="loading" border stripe size="small" :span-method="locationSpanMethod">
        <el-table-column label="名称" width="160" show-overflow-tooltip>
          <template #default="{ row }">
            <ResourceTableGroupLabel v-if="isLocationGroup(row)" :row="row" />
            <span v-else>{{ row.name }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="cidr" label="地址段" width="150">
          <template #default="{ row }"><code class="mono">{{ row.cidr }}</code></template>
        </el-table-column>
        <el-table-column label="p2p 前缀" width="86" align="center">
          <template #default="{ row }">/{{ row.point_prefixlen }}</template>
        </el-table-column>
        <el-table-column label="POP 节点" width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ row.pop_point_name || getPopName(row.pop_point) }}</template>
        </el-table-column>
        <el-table-column label="区域 / 城市" min-width="150">
          <template #default="{ row }">
            {{ [row.region_name, row.country_name, row.city_name].filter(Boolean).join(' / ') || '-' }}
          </template>
        </el-table-column>
        <el-table-column label="归属客户" width="150">
          <template #default="{ row }">
            <span v-if="row.customer">{{ row.customer_name || row.customer }}</span>
            <el-tag v-else size="small" type="info">公共池</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="块用量（已用/总）" width="160" align="center">
          <template #default="{ row }">
            <span style="font-size:12px;">{{ row.used_blocks }} / {{ row.total_blocks }}</span>
            <el-progress
              :percentage="row.total_blocks ? Math.round((row.used_blocks / row.total_blocks) * 100) : 0"
              :stroke-width="6" :show-text="false" style="margin-top:2px" />
          </template>
        </el-table-column>
        <el-table-column label="下一段可用" min-width="180">
          <template #default="{ row }">
            <span v-if="row.next_free" class="mono" style="font-size:12px;">
              {{ row.next_free.local_cidr }} / {{ row.next_free.peer_cidr }}
            </span>
            <el-tag v-else size="small" type="danger">已耗尽</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="90" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="row.is_synced ? 'success' : ''">
              {{ row.is_synced ? '节点同步' : '中央' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="64" align="center">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'" size="small">{{ row.is_active ? '是' : '否' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openUsage(row)">用量</el-button>
            <el-button link type="warning" size="small" @click="handlePush(row)">下发</el-button>
            <el-button link type="primary" size="small" @click="openDialog(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-model:current-page="page" :total="total" :page-size="pageSize"
        layout="total,prev,pager,next" class="pagination" @change="load" />
    </el-card>

    <!-- 新建 / 编辑 -->
    <el-dialog v-model="dialog" :title="form.id ? '编辑地址段' : '新建地址段'" width="560px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="110px">
        <el-form-item label="POP 节点" prop="pop_point">
          <el-select v-model="form.pop_point" filterable placeholder="选择所属 POP 节点" style="width:100%">
            <el-option v-for="p in pops" :key="p.id" :label="popLabel(p)" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="如 edge-tunnel-p2p" />
        </el-form-item>
        <el-form-item label="地址段 CIDR" prop="cidr">
          <el-input v-model="form.cidr" placeholder="如 10.21.0.0/24" />
          <div class="hint">父网段；将在其内按 p2p 前缀切块分配</div>
        </el-form-item>
        <el-form-item label="p2p 前缀">
          <el-select v-model="form.point_prefixlen" style="width:220px">
            <el-option :value="30" label="/30（.1/.2，步进 4）" />
            <el-option :value="31" label="/31（步进 2，无浪费）" />
          </el-select>
        </el-form-item>
        <el-form-item label="归属客户">
          <el-select v-model="form.customer" clearable filterable placeholder="留空 = 公共池" style="width:100%">
            <el-option
              v-for="c in customers" :key="c.id"
              :label="`${c.customer_no || c.id} — ${c.name}`" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" maxlength="512" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.is_active" />
        </el-form-item>
        <el-form-item label="同步到节点">
          <el-switch v-model="form.push_to_node" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Search } from '@element-plus/icons-vue'
import {
  getTunnelIPPools, createTunnelIPPool, updateTunnelIPPool, deleteTunnelIPPool,
  pushTunnelIPPoolToNode,
} from '@/api/resource'
import { getCustomers } from '@/api/customer'
import { useRegionGroupedRows, useResourceLocation } from '@/composables/useResourceLocation'
import ResourceLocationFilter from './components/ResourceLocationFilter.vue'
import ResourceTableGroupLabel from './components/ResourceTableGroupLabel.vue'
import ExcelImportExport from '@/components/ExcelImportExport.vue'

const router = useRouter()

const loading = ref(false)
const rows = ref([])
const customers = ref([])
const page = ref(1)
const pageSize = 20
const total = ref(0)
const search = reactive({ is_synced: '', search: '' })
const {
  location, regions, filteredZones, filteredPopPoints,
  popPoints: pops, loadingLocation, popLabel,
  resetLocation, locationParams, loadLocationOptions,
} = useResourceLocation()
const excelParams = computed(() => {
  const params = { ...locationParams() }
  if (search.is_synced !== '' && search.is_synced !== null) params.is_synced = search.is_synced
  if (search.search) params.search = search.search
  return params
})
const {
  groupedRows,
  isLocationGroup,
  locationSpanMethod,
} = useRegionGroupedRows(rows, 11)

const stateMeta = {
  available: { label: '可用', type: 'success' },
  assigned: { label: '已分配', type: 'primary' },
  reserved: { label: '预留', type: 'warning' },
  blocked: { label: '封禁', type: 'danger' },
  system: { label: '系统保留', type: 'info' },
}

function getPopName(id) {
  const p = pops.value.find((x) => x.id === id)
  return p ? p.name : id
}

function applyLocation(next) {
  Object.assign(location, next)
}

function reloadForLocation() {
  page.value = 1
  load()
}

async function load() {
  loading.value = true
  try {
    const p = { page: page.value, page_size: pageSize, ...locationParams() }
    if (search.is_synced !== '' && search.is_synced !== null) p.is_synced = search.is_synced
    if (search.search) p.search = search.search
    const res = await getTunnelIPPools(p)
    rows.value = res?.results || []
    total.value = res?.count || 0
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

// ── 新建 / 编辑 ─────────────────────────────────────────────
const dialog = ref(false)
const saving = ref(false)
const formRef = ref()
const form = reactive({
  id: null,
  pop_point: '',
  name: '',
  cidr: '',
  point_prefixlen: 30,
  customer: null,
  is_active: true,
  remark: '',
  push_to_node: true,
})
const rules = {
  pop_point: [{ required: true, message: '请选择 POP 节点', trigger: 'change' }],
  name: [{ required: true, message: '必填', trigger: 'blur' }],
  cidr: [{ required: true, message: '必填，如 10.21.0.0/24', trigger: 'blur' }],
}

function openDialog(row = null) {
  if (row) {
    form.id = row.id
    form.pop_point = row.pop_point
    form.name = row.name
    form.cidr = row.cidr
    form.point_prefixlen = row.point_prefixlen
    form.customer = row.customer || null
    form.is_active = row.is_active
    form.remark = row.remark || ''
    form.push_to_node = true
  } else {
    form.id = null
    form.pop_point = location.pop_point || ''
    form.name = ''
    form.cidr = ''
    form.point_prefixlen = 30
    form.customer = null
    form.is_active = true
    form.remark = ''
    form.push_to_node = true
  }
  dialog.value = true
}

async function save() {
  const ok = await formRef.value?.validate().catch(() => false)
  if (ok === false) return
  saving.value = true
  try {
    const payload = {
      pop_point: form.pop_point,
      name: form.name,
      cidr: form.cidr,
      point_prefixlen: form.point_prefixlen,
      customer: form.customer || null,
      is_active: form.is_active,
      remark: form.remark,
    }
    if (form.id) {
      await updateTunnelIPPool(form.id, payload, form.push_to_node)
      ElMessage.success(form.push_to_node ? '已更新并尝试下发节点' : '已更新')
    } else {
      await createTunnelIPPool(payload, form.push_to_node)
      ElMessage.success(form.push_to_node ? '已创建并尝试下发节点' : '已创建')
    }
    dialog.value = false
    await load()
  } catch (e) {
    const d = e?.response?.data
    ElMessage.error(d ? (d.detail || JSON.stringify(d)) : (e.message || '保存失败'))
  } finally {
    saving.value = false
  }
}

async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(`删除隧道地址段 ${row.name}（${row.cidr}）？`, '确认', { type: 'warning' })
  } catch { return }
  try {
    await deleteTunnelIPPool(row.id, true)
    ElMessage.success('已删除')
    await load()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e.message || '删除失败')
  }
}

async function handlePush(row) {
  try {
    await ElMessageBox.confirm(
      `将把地址段「${row.name}（${row.cidr}）」下发到所属 POP 节点（节点侧按名称幂等创建/更新）。继续？`,
      '下发到节点', { type: 'warning', confirmButtonText: '下发', cancelButtonText: '取消' },
    )
  } catch { return }
  try {
    const res = await pushTunnelIPPoolToNode(row.id, false)
    if (res?.ok) {
      ElMessage.success(res.detail || '已下发到节点')
      await load()
    } else {
      ElMessage.error(res?.detail || '下发失败')
    }
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e.message || '下发失败')
  }
}

// ── 用量详情 ─────────────────────────────────────────────
function openUsage(row) {
  router.push(`/resource/tunnel-ip/${row.id}/usage`)
}

onMounted(async () => {
  const [, cRes] = await Promise.allSettled([
    loadLocationOptions(),
    getCustomers({ page_size: 500 }),
  ])
  customers.value = cRes.status === 'fulfilled' ? (cRes.value?.results || []) : []
  await load()
})
</script>

<style scoped>
.page-container { min-height: 100%; }
.page-header { display:flex; align-items:flex-start; justify-content:space-between; margin-bottom:16px; gap:12px; }
.page-title { margin:0; font-size:20px; }
.page-desc { margin:4px 0 0; font-size:12px; color:#909399; line-height:1.5; max-width:760px; }
.pagination { margin-top:12px; justify-content:flex-end; }
.hint { color:#999; font-size:12px; line-height:1.4; }
.mono { font-family: monospace; }
.drawer-title { display:flex; align-items:center; gap:10px; font-size:15px; }
.drawer-title .dt-meta { color:#888; font-size:12px; margin-left:auto; }
.sub-title { font-weight:600; color:#4a5b6c; margin-bottom:6px; font-size:13px; }
.entry-line { display:flex; align-items:center; gap:8px; padding:2px 0; }
.muted { color:#999; font-size:12px; }
</style>
