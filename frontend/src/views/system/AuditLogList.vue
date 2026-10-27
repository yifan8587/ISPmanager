<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <h2 class="page-title">操作日志</h2>
        <p class="page-desc">按功能模块查看全站操作留存：时间、人员、对象与详细内容。</p>
      </div>
      <el-button :icon="Refresh" :loading="loading" @click="reload">刷新</el-button>
    </div>

    <el-row :gutter="12" class="kpi-row">
      <el-col :span="6">
        <el-card shadow="never"><div class="kpi"><div class="kpi-num">{{ total }}</div><div class="kpi-label">当前筛选</div></div></el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never"><div class="kpi"><div class="kpi-num">{{ todayCount }}</div><div class="kpi-label">本页今日</div></div></el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never"><div class="kpi"><div class="kpi-num">{{ activeModuleCount }}</div><div class="kpi-label">有日志模块</div></div></el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never"><div class="kpi"><div class="kpi-num">{{ currentModuleLabel }}</div><div class="kpi-label">当前模块</div></div></el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="search-card">
      <el-tabs v-model="searchForm.module" class="module-tabs" @tab-change="onModuleChange">
        <el-tab-pane name="">
          <template #label>全部 <span class="tab-count">{{ allModuleCount }}</span></template>
        </el-tab-pane>
        <el-tab-pane v-for="mod in modules" :key="mod.value" :name="mod.value">
          <template #label>{{ mod.label }} <span class="tab-count">{{ mod.count || 0 }}</span></template>
        </el-tab-pane>
      </el-tabs>

      <el-form :model="searchForm" inline>
        <el-form-item label="操作类型">
          <el-select v-model="searchForm.action_type" clearable filterable placeholder="全部" style="width:160px" @change="reload">
            <el-option v-for="item in actionTypes" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="操作人">
          <el-input v-model="searchForm.operator_name" clearable placeholder="姓名 / 用户名" style="width:160px" @keyup.enter="reload" @clear="reload" />
        </el-form-item>
        <el-form-item label="关键词">
          <el-input v-model="searchForm.search" clearable placeholder="内容、对象、路径、IP" style="width:220px" @keyup.enter="reload" @clear="reload" />
        </el-form-item>
        <el-form-item label="时间">
          <el-date-picker
            v-model="dateRange"
            type="datetimerange"
            range-separator="至"
            start-placeholder="开始"
            end-placeholder="结束"
            value-format="YYYY-MM-DDTHH:mm:ss"
            style="width:360px"
            @change="reload"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="reload">查询</el-button>
          <el-button @click="resetSearch">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" style="margin-top:12px;">
      <el-table :data="rows" v-loading="loading" stripe border @row-click="openDetail">
        <el-table-column prop="created_at" label="操作时间" width="180">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="功能模块" width="120">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.module_label || row.module }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作类型" width="120">
          <template #default="{ row }">
            <el-tag size="small" :type="actionTag(row.action_type)">{{ row.action_type_label || row.action_type }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="operator_name" label="操作人" width="130" />
        <el-table-column label="操作对象" min-width="160">
          <template #default="{ row }">
            <div>{{ row.target_repr || row.target_model || '-' }}</div>
            <div v-if="row.target_id" class="muted">{{ row.target_model }} #{{ row.target_id }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="操作内容" min-width="260" show-overflow-tooltip />
        <el-table-column prop="ip_address" label="来源 IP" width="140" />
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click.stop="openDetail(row)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        class="pagination"
        @change="loadData"
      />
    </el-card>

    <el-drawer v-model="detailVisible" title="日志详情" size="520px" destroy-on-close>
      <template v-if="current">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="操作时间">{{ formatTime(current.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="功能模块">{{ current.module_label || current.module }}</el-descriptions-item>
          <el-descriptions-item label="操作类型">{{ current.action_type_label || current.action_type }}</el-descriptions-item>
          <el-descriptions-item label="操作人">{{ current.operator_name || '系统' }}</el-descriptions-item>
          <el-descriptions-item label="操作对象">{{ current.target_repr || current.target_model || '-' }}</el-descriptions-item>
          <el-descriptions-item label="对象标识">{{ current.target_id ? `${current.target_model} #${current.target_id}` : '-' }}</el-descriptions-item>
          <el-descriptions-item label="来源 IP">{{ current.ip_address || '-' }}</el-descriptions-item>
          <el-descriptions-item label="请求">{{ current.request_method || '-' }} {{ current.request_path || '-' }}</el-descriptions-item>
          <el-descriptions-item label="日志来源">{{ current.source_label || current.source }}</el-descriptions-item>
          <el-descriptions-item label="操作内容">{{ current.description }}</el-descriptions-item>
        </el-descriptions>
        <div class="detail-title">详细信息</div>
        <pre class="detail-json">{{ formatDetail(current.detail) }}</pre>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { getAuditLogMeta, getAuditLogs } from '@/api/log'

const loading = ref(false)
const rows = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const modules = ref([])
const actionTypes = ref([])
const dateRange = ref([])
const detailVisible = ref(false)
const current = ref(null)

const searchForm = reactive({
  module: '',
  action_type: '',
  operator_name: '',
  search: '',
})

const allModuleCount = computed(() => modules.value.reduce((sum, item) => sum + (item.count || 0), 0))
const activeModuleCount = computed(() => modules.value.filter((item) => item.count > 0).length)
const currentModuleLabel = computed(() => {
  if (!searchForm.module) return '全部模块'
  return modules.value.find((item) => item.value === searchForm.module)?.label || searchForm.module
})
const todayCount = computed(() => {
  const today = new Date().toDateString()
  return rows.value.filter((row) => row.created_at && new Date(row.created_at).toDateString() === today).length
})

function actionTag(type) {
  if (['delete', 'login_failed', 'terminate', 'reject_order'].includes(type)) return 'danger'
  if (['create', 'login', 'publish', 'activate', 'payment'].includes(type)) return 'success'
  if (['update', 'change', 'status_change', 'sync'].includes(type)) return 'warning'
  return 'info'
}

function formatTime(value) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-'
}

function formatDetail(detail) {
  if (!detail || (typeof detail === 'object' && !Object.keys(detail).length)) return '无'
  try {
    return JSON.stringify(detail, null, 2)
  } catch {
    return String(detail)
  }
}

function buildParams() {
  const params = {
    page: page.value,
    page_size: pageSize.value,
  }
  if (searchForm.module) params.module = searchForm.module
  if (searchForm.action_type) params.action_type = searchForm.action_type
  if (searchForm.operator_name) params.operator_name = searchForm.operator_name
  if (searchForm.search) params.search = searchForm.search
  if (dateRange.value?.length === 2) {
    params.created_at_after = dateRange.value[0]
    params.created_at_before = dateRange.value[1]
  }
  return params
}

async function loadMeta() {
  const res = await getAuditLogMeta().catch(() => null)
  modules.value = res?.modules || []
  actionTypes.value = res?.action_types || []
}

async function loadData() {
  loading.value = true
  try {
    const res = await getAuditLogs(buildParams())
    rows.value = res.results || res || []
    total.value = res.count || rows.value.length
  } catch {
    rows.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function reload() {
  page.value = 1
  loadData()
  loadMeta()
}

function onModuleChange() {
  reload()
}

function resetSearch() {
  searchForm.module = ''
  searchForm.action_type = ''
  searchForm.operator_name = ''
  searchForm.search = ''
  dateRange.value = []
  reload()
}

function openDetail(row) {
  current.value = row
  detailVisible.value = true
}

onMounted(async () => {
  await loadMeta()
  await loadData()
})
</script>

<style scoped>
.page-container { min-height: 100%; }
.page-header { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 16px; }
.page-title { font-size: 18px; font-weight: 700; color: #1a1a2e; margin: 0; }
.page-desc { margin: 6px 0 0; font-size: 13px; color: #909399; }
.kpi-row { margin-bottom: 12px; }
.kpi { text-align: center; padding: 8px 0; }
.kpi-num { font-size: 22px; font-weight: 700; color: #303133; }
.kpi-label { font-size: 12px; color: #909399; margin-top: 4px; }
.search-card { margin-bottom: 0; }
.module-tabs { margin-bottom: 4px; }
.tab-count { color: #909399; font-size: 12px; }
.muted { font-size: 12px; color: #909399; margin-top: 2px; }
.pagination { margin-top: 14px; justify-content: flex-end; }
.detail-title { margin: 18px 0 8px; font-weight: 600; color: #303133; }
.detail-json {
  margin: 0;
  padding: 12px;
  background: #f5f7fa;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.6;
  color: #303133;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
