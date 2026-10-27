<template>
  <div class="page">
    <div class="page-toolbar">
      <div class="pt-left">
        <span class="pt-title">流量截取 / 95 计费</span>
        <el-text type="info" size="small">按时间窗口截取接口流量并计算分位峰值计费，结果可保存为报告。上一自然月及更早、状态为已完成的报告会由日终任务自动归档并尝试出超额账单。</el-text>
      </div>
    </div>

    <!-- 查询区 -->
    <el-card shadow="never" class="query-card">
      <el-form :model="queryForm" label-width="92px" size="default">
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="监控主机" required>
              <el-select v-model="queryForm.monitoring_host" filterable remote :remote-method="searchHosts"
                :loading="hostLoading" placeholder="搜索资源 / 主机" style="width:100%" @change="onHostChange">
                <el-option v-for="h in hostOptions" :key="h.id" :label="h.label" :value="h.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="监控图形" required>
              <el-select v-model="queryForm.graph_id" :loading="graphsLoading"
                :disabled="!queryForm.monitoring_host" placeholder="请先选择监控主机" style="width:100%"
                @change="onGraphChange">
                <el-option v-for="g in hostGraphs" :key="g.graph_id" :label="g.label" :value="g.graph_id"
                  :disabled="!g.ready">
                  <div class="opt-main">{{ g.label }}</div>
                  <div class="opt-sub">
                    {{ g.interface_name || '接口待解析' }}
                    <el-text v-if="!g.ready" type="warning" size="small"> · 未同步监控项</el-text>
                  </div>
                </el-option>
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="计费规则">
              <el-select v-model="queryForm.profile" clearable placeholder="默认95计费" style="width:100%">
                <el-option v-for="p in profiles" :key="p.id" :label="p.name" :value="p.id" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="时间范围" required>
              <el-date-picker v-model="queryForm.range" type="datetimerange" unlink-panels
                range-separator="至" start-placeholder="开始" end-placeholder="结束"
                value-format="YYYY-MM-DDTHH:mm:ss" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :span="16">
            <el-form-item label="快捷范围">
              <el-button v-for="q in quickRanges" :key="q.k" size="small" @click="applyQuick(q)">{{ q.label }}</el-button>
              <el-divider direction="vertical" />
              <el-checkbox v-model="queryForm.persist">保存为报告</el-checkbox>
              <el-button type="primary" :icon="Search" :loading="querying" style="margin-left:12px;" @click="runQuery">查询</el-button>
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item v-if="selectedGraph?.interface_name" label="接口">
          <el-text size="small" type="info">{{ selectedGraph.interface_name }}</el-text>
        </el-form-item>
      </el-form>

      <MonitoringChart v-if="result" :chart-data="result" :show-period="false" :height="300" title="流量截取结果" />
    </el-card>

    <!-- 报告列表 -->
    <div class="list-head">
      <div class="list-title">已保存报告</div>
      <el-checkbox v-model="showArchived" @change="loadReports">显示已归档</el-checkbox>
    </div>
    <el-table v-loading="loading" :data="reports" border stripe size="small">
      <el-table-column prop="report_no" label="报告编号" width="170" />
      <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
      <el-table-column prop="interface_name" label="接口" width="120" show-overflow-tooltip />
      <el-table-column label="时间范围" min-width="240">
        <template #default="{ row }">
          <el-text size="small">{{ formatDate(row.time_from) }} ~ {{ formatDate(row.time_till) }}</el-text>
        </template>
      </el-table-column>
      <el-table-column label="峰值计费" width="130">
        <template #default="{ row }">
          <el-tag type="danger" size="small">{{ row.percentile_n }}峰值 {{ fmt(row.percentile_mbps) }} Mbps</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="采样" prop="sample_count" width="80" align="center" />
      <el-table-column label="状态" width="140">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">{{ row.status_label }}</el-tag>
          <el-text v-if="isAwaitingAutoArchive(row)" type="warning" size="small" style="margin-left:6px">待日终归档</el-text>
        </template>
      </el-table-column>
      <el-table-column label="出账" width="150">
        <template #default="{ row }">
          <el-tag v-if="row.traffic_billing?.bill_no" type="warning" size="small">
            {{ row.traffic_billing.bill_no }} {{ row.traffic_billing.currency }}{{ row.traffic_billing.amount }}
          </el-tag>
          <el-text v-else type="info" size="small">-</el-text>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="180" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="viewReport(row)">查看图表</el-button>
          <el-button v-if="row.status !== 'archived'" link type="warning" size="small" @click="doArchive(row)">归档</el-button>
          <el-button link type="danger" size="small" @click="doDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div class="pager">
      <el-pagination layout="prev, pager, next, total" :total="total" :page-size="pageSize"
        :current-page="page" @current-change="onPage" />
    </div>

    <el-dialog v-model="reportVisible" :title="currentReport?.title || '流量报告'" width="900px" destroy-on-close>
      <MonitoringChart v-if="reportData" :chart-data="reportData" :show-period="false" :height="320" />
      <el-empty v-else description="该报告无图表数据" :image-size="60" />
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import MonitoringChart from '@/components/MonitoringChart.vue'
import {
  getMonitoringHosts, getGraphProfiles, queryTraffic,
  getTrafficReports, getTrafficReport, getHostTrafficGraphs,
  archiveTrafficReport, deleteTrafficReport,
} from '@/api/operation'

const querying = ref(false)
const result = ref(null)
const profiles = ref([])
const hostOptions = ref([])
const hostLoading = ref(false)
const hostGraphs = ref([])
const graphsLoading = ref(false)
const showArchived = ref(false)

const queryForm = reactive({
  monitoring_host: null,
  graph_id: null,
  range: null,
  profile: null,
  persist: true,
})

const selectedGraph = computed(() =>
  hostGraphs.value.find(g => g.graph_id === queryForm.graph_id) || null)

const quickRanges = [
  { k: '24h', label: '近24小时', hours: 24 },
  { k: '7d', label: '近7天', hours: 24 * 7 },
  { k: '30d', label: '近30天', hours: 24 * 30 },
  { k: 'month', label: '本月', month: true },
]

function pad(n) { return String(n).padStart(2, '0') }
function toLocalStr(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}
function applyQuick(q) {
  const end = new Date()
  const start = q.month ? new Date(end.getFullYear(), end.getMonth(), 1) : new Date(end.getTime() - q.hours * 3600000)
  queryForm.range = [toLocalStr(start), toLocalStr(end)]
}
function fmt(v) { return v == null ? '-' : Number(v).toFixed(2) }
function formatDate(dt) {
  if (!dt) return '-'
  return new Date(dt).toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-')
}
function statusType(s) {
  return { done: 'success', failed: 'danger', archived: 'info' }[s] || 'info'
}
function isAwaitingAutoArchive(row) {
  if (row.status !== 'done' || !row.time_till) return false
  const till = new Date(row.time_till)
  const now = new Date()
  return till < new Date(now.getFullYear(), now.getMonth(), 1)
}

async function searchHosts(q) {
  hostLoading.value = true
  try {
    const res = await getMonitoringHosts({ search: q || '', page_size: 50 })
    const items = res?.results || res || []
    hostOptions.value = items.map(h => ({
      id: h.id, label: `${h.resource_label} · ${h.visible_name || h.hostname}`,
    }))
  } catch { /* ignore */ } finally { hostLoading.value = false }
}

async function loadHostGraphs(hostId) {
  if (!hostId) {
    hostGraphs.value = []
    return
  }
  graphsLoading.value = true
  try {
    const res = await getHostTrafficGraphs(hostId)
    hostGraphs.value = res?.graphs || []
    if (!hostGraphs.value.length) {
      ElMessage.warning('该监控主机暂无流量类图形，请先同步监控主机')
    }
    const ready = hostGraphs.value.find(g => g.ready)
    queryForm.graph_id = ready?.graph_id || hostGraphs.value[0]?.graph_id || null
  } catch (e) {
    ElMessage.error('加载监控图形失败：' + (e?.response?.data?.detail || e.message))
    hostGraphs.value = []
  } finally {
    graphsLoading.value = false
  }
}

function onHostChange() {
  result.value = null
  queryForm.graph_id = null
  loadHostGraphs(queryForm.monitoring_host)
}

function onGraphChange() {
  result.value = null
}

async function runQuery() {
  if (!queryForm.monitoring_host) { ElMessage.warning('请选择监控主机'); return }
  if (!queryForm.graph_id) { ElMessage.warning('请选择监控图形'); return }
  if (!selectedGraph.value?.ready) {
    ElMessage.warning('所选图形尚未关联可计费的监控项，请先同步监控主机')
    return
  }
  if (!queryForm.range || queryForm.range.length !== 2) { ElMessage.warning('请选择时间范围'); return }
  querying.value = true
  try {
    const payload = {
      monitoring_host: queryForm.monitoring_host,
      graph: queryForm.graph_id,
      time_from: queryForm.range[0],
      time_till: queryForm.range[1],
      persist: queryForm.persist,
    }
    if (queryForm.profile) payload.profile = queryForm.profile
    result.value = await queryTraffic(payload)
    if (queryForm.persist) {
      ElMessage.success('查询完成' + (result.value?.report_no ? '，报告 ' + result.value.report_no : ''))
      loadReports()
    } else {
      ElMessage.success('查询完成')
    }
  } catch (e) {
    ElMessage.error('查询失败：' + (e?.response?.data?.detail || e.message))
  } finally { querying.value = false }
}

const loading = ref(false)
const reports = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)

async function loadReports() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (showArchived.value) params.show_archived = '1'
    const res = await getTrafficReports(params)
    reports.value = res?.results || res || []
    total.value = res?.count ?? reports.value.length
  } catch (e) {
    ElMessage.error('加载报告失败：' + (e?.response?.data?.detail || e.message))
  } finally { loading.value = false }
}
function onPage(p) { page.value = p; loadReports() }

const reportVisible = ref(false)
const currentReport = ref(null)
const reportData = ref(null)

async function viewReport(row) {
  currentReport.value = row
  reportData.value = null
  reportVisible.value = true
  try {
    const full = await getTrafficReport(row.id)
    reportData.value = full?.result || null
  } catch (e) {
    ElMessage.error('加载报告失败：' + (e?.response?.data?.detail || e.message))
  }
}

async function doArchive(row) {
  try {
    await ElMessageBox.confirm(`确定归档报告「${row.report_no}」？归档后默认列表不再显示。`, '归档确认', { type: 'warning' })
  } catch { return }
  try {
    const res = await archiveTrafficReport(row.id)
    const tb = res?.traffic_billing
    if (tb?.status === 'billed') {
      ElMessage.success(`已归档，并生成流量超额账单 ${tb.bill_no} ${tb.currency}${tb.amount}`)
    } else if (tb?.status === 'skipped') {
      ElMessage.success(`已归档（未出超额：${tb.reason_label || tb.reason}）`)
    } else if (tb?.status === 'error') {
      ElMessage.warning(`已归档，但出账失败：${tb.reason_label || '可稍后用命令补扫'}`)
    } else {
      ElMessage.success('已归档')
    }
    loadReports()
  } catch (e) {
    ElMessage.error('归档失败：' + (e?.response?.data?.detail || e.message))
  }
}

async function doDelete(row) {
  try {
    await ElMessageBox.confirm(`确定删除报告「${row.report_no}」？此操作不可恢复。`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await deleteTrafficReport(row.id)
    ElMessage.success('已删除')
    loadReports()
  } catch (e) {
    ElMessage.error('删除失败：' + (e?.response?.data?.detail || e.message))
  }
}

onMounted(async () => {
  applyQuick(quickRanges[0])
  searchHosts('')
  try {
    const res = await getGraphProfiles({ is_active: true })
    profiles.value = res?.results || res || []
  } catch { /* ignore */ }
  loadReports()
})
</script>

<style scoped>
.page { background: #fff; border-radius: 8px; padding: 16px 20px; }
.page-toolbar { display: flex; align-items: center; margin-bottom: 14px; }
.pt-left { display: flex; flex-direction: column; gap: 2px; }
.pt-title { font-size: 16px; font-weight: 700; color: #1a1a2e; }
.query-card { margin-bottom: 18px; background: #fafbff; }
.list-head { display: flex; align-items: center; justify-content: space-between; margin: 6px 0 12px; }
.list-title { font-size: 14px; font-weight: 600; color: #303133; padding-left: 8px; border-left: 3px solid #409EFF; }
.pager { display: flex; justify-content: flex-end; margin-top: 12px; }
.opt-main { font-size: 13px; line-height: 1.4; }
.opt-sub { font-size: 11px; color: #909399; line-height: 1.3; }
</style>
