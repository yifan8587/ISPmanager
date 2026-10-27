<template>
  <div class="page quality-page">
    <div class="page-toolbar">
      <div>
        <span class="pt-title">POP 品质监控</span>
        <el-text type="info" size="small">
          定期探测 POP 管理 IP（ICMP）及管理 URL（HTTP），采样保存在本地，不影响 Zabbix 监控图形。
        </el-text>
      </div>
      <div class="toolbar-actions">
        <el-button :icon="Refresh" :loading="discovering" :disabled="!canManage" @click="discover">
          发现 POP
        </el-button>
        <el-button type="primary" :icon="Plus" :disabled="!canManage" @click="openConfig()">
          新增监控
        </el-button>
      </div>
    </div>

    <div class="filters">
      <el-select v-model="filters.status" clearable placeholder="全部状态" style="width:150px" @change="load">
        <el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" />
      </el-select>
      <el-select v-model="filters.alertActive" clearable placeholder="告警状态" style="width:130px" @change="load">
        <el-option label="告警中" :value="true" />
        <el-option label="未告警" :value="false" />
      </el-select>
      <el-input
        v-model="filters.search"
        :prefix-icon="Search"
        clearable
        placeholder="搜索 POP / 管理 IP / URL"
        style="width:260px"
        @keyup.enter="load"
        @clear="load"
      />
      <el-button :icon="Refresh" @click="load">刷新</el-button>
    </div>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="daemon-tip"
      title="品质探测由独立守护命令执行；首次使用请点击“发现 POP”，并确保 run_pop_quality_monitor 服务已经启动。"
    />

    <el-table v-loading="loading" :data="monitors" border stripe>
      <el-table-column label="POP 节点" min-width="170">
        <template #default="{ row }">
          <div class="pop-name">{{ row.pop_label }}</div>
          <el-tag size="small" :type="popStatusTag(row.pop_status)">{{ row.pop_status_label }}</el-tag>
          <el-tag v-if="row.is_alert_suppressed" size="small" type="info" style="margin-left:4px">维护期静默</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="探测目标" min-width="220" show-overflow-tooltip>
        <template #default="{ row }">
          <div v-if="row.enable_icmp">IP：{{ row.mgmt_ip || '未配置' }}</div>
          <div v-if="row.enable_http" class="muted">URL：{{ row.mgmt_url || '未配置' }}</div>
          <span v-if="!row.enable_icmp && !row.enable_http" class="muted">已关闭全部探测</span>
        </template>
      </el-table-column>
      <el-table-column label="当前品质" min-width="160">
        <template #default="{ row }">
          <el-tag :type="qualityStatusTag(row.status)" size="small">{{ row.status_label }}</el-tag>
          <div class="metric-row">
            丢包 <strong>{{ percent(row.last_icmp_packet_loss_pct) }}</strong>
            · ICMP {{ ms(row.last_icmp_avg_ms) }}
          </div>
          <div class="metric-row">
            HTTP {{ row.last_http_status_code || '-' }} · {{ ms(row.last_http_response_ms) }}
          </div>
        </template>
      </el-table-column>
      <el-table-column label="告警策略" min-width="150">
        <template #default="{ row }">
          <div>阈值 ≥ {{ number(row.packet_loss_threshold_pct) }}%</div>
          <div class="muted">连续 {{ row.consecutive_breach_threshold }} 次 / {{ row.alert_repeat_minutes }} 分钟</div>
          <el-tag v-if="row.alert_active" type="danger" size="small">告警中（{{ row.consecutive_breaches }} 次）</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="最近探测" width="170">
        <template #default="{ row }">
          <div>{{ formatTime(row.last_probe_at) || '未探测' }}</div>
          <el-tooltip v-if="row.last_error" :content="row.last_error" placement="top">
            <el-text type="danger" size="small" truncated style="max-width:140px">探测异常</el-text>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="启用" width="72" align="center">
        <template #default="{ row }">
          <el-switch v-model="row.is_enabled" :disabled="!canManage" @change="toggleEnabled(row)" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="250" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="openDetail(row)">曲线</el-button>
          <el-button link type="success" size="small" :loading="probingId === row.id" :disabled="!canManage" @click="probeNow(row)">
            立即探测
          </el-button>
          <el-button link type="warning" size="small" :disabled="!canManage" @click="openConfig(row)">配置</el-button>
          <el-button link type="danger" size="small" :disabled="!canManage" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-empty v-if="!loading && !monitors.length" description="暂无 POP 品质监控对象，请点击“发现 POP”自动创建。" />

    <el-dialog
      v-model="configVisible"
      :title="configForm.id ? '配置 POP 品质监控' : '新增 POP 品质监控'"
      width="840px"
      class="quality-config-dialog"
      destroy-on-close
    >
      <el-form
        ref="configFormRef"
        :model="configForm"
        :rules="configRules"
        label-position="top"
        class="quality-config-form"
      >
        <el-form-item label="关联 POP" prop="pop_point">
          <el-select
            v-model="configForm.pop_point"
            filterable
            remote
            :remote-method="loadPopOptions"
            :loading="popLoading"
            :disabled="!!configForm.id"
            placeholder="搜索并选择 POP 节点"
            style="width:100%"
          >
            <el-option v-for="pop in popOptions" :key="pop.id" :label="popLabel(pop)" :value="pop.id" />
          </el-select>
        </el-form-item>

        <div v-if="selectedPop" class="target-preview">
          <span>当前资源目标：</span>
          <el-tag :type="selectedPop.mgmt_ip ? 'success' : 'info'" size="small">
            管理 IP：{{ selectedPop.mgmt_ip || '未配置' }}
          </el-tag>
          <el-tag :type="selectedPop.mgmt_url ? 'success' : 'info'" size="small">
            管理 URL：{{ selectedPop.mgmt_url || '未配置' }}
          </el-tag>
        </div>

        <el-row :gutter="18" class="switch-grid">
          <el-col :xs="24" :sm="8">
            <el-form-item label="启用品质监控">
              <el-switch v-model="configForm.is_enabled" active-text="启用" inactive-text="停用" />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="8">
            <el-form-item label="ICMP 管理 IP">
              <el-switch v-model="configForm.enable_icmp" active-text="探测" inactive-text="关闭" />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="8">
            <el-form-item label="HTTP 管理 URL">
              <el-switch v-model="configForm.enable_http" active-text="探测" inactive-text="关闭" />
            </el-form-item>
          </el-col>
        </el-row>

        <div class="form-tip form-tip-block">
          探测目标始终从 POP 资源的管理 IP、管理 URL 动态读取；保存后，后台守护进程会在下一个轮询周期自动执行首轮探测。
        </div>

        <el-divider content-position="left">探测参数</el-divider>
        <el-row :gutter="18">
          <el-col :xs="24" :sm="8">
            <el-form-item label="探测间隔" prop="probe_interval_seconds">
              <div class="number-field">
                <el-input-number
                  v-model="configForm.probe_interval_seconds"
                  :min="15"
                  :max="86400"
                  :step="5"
                  controls-position="right"
                />
                <span>秒</span>
              </div>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="8">
            <el-form-item label="单次超时" prop="timeout_seconds">
              <div class="number-field">
                <el-input-number
                  v-model="configForm.timeout_seconds"
                  :min="1"
                  :max="60"
                  controls-position="right"
                />
                <span>秒</span>
              </div>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="8">
            <el-form-item label="每轮 Ping 次数" prop="icmp_count">
              <div class="number-field">
                <el-input-number
                  v-model="configForm.icmp_count"
                  :min="1"
                  :max="20"
                  controls-position="right"
                />
                <span>次</span>
              </div>
            </el-form-item>
          </el-col>
        </el-row>

        <el-divider content-position="left">丢包告警策略</el-divider>
        <el-row :gutter="18">
          <el-col :xs="24" :sm="8">
            <el-form-item label="丢包率阈值" prop="packet_loss_threshold_pct">
              <div class="number-field">
                <el-input-number
                  v-model="configForm.packet_loss_threshold_pct"
                  :min="0"
                  :max="100"
                  :step="0.5"
                  :precision="2"
                  controls-position="right"
                />
                <span>%</span>
              </div>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="8">
            <el-form-item label="连续超阈次数" prop="consecutive_breach_threshold">
              <div class="number-field">
                <el-input-number
                  v-model="configForm.consecutive_breach_threshold"
                  :min="1"
                  :max="100"
                  controls-position="right"
                />
                <span>次</span>
              </div>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="8">
            <el-form-item label="重复提醒间隔" prop="alert_repeat_minutes">
              <div class="number-field">
                <el-input-number
                  v-model="configForm.alert_repeat_minutes"
                  :min="1"
                  :max="1440"
                  :step="5"
                  controls-position="right"
                />
                <span>分钟</span>
              </div>
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="备注">
          <el-input v-model="configForm.remark" type="textarea" :rows="2" maxlength="500" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="configVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveConfig">保存</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" :title="detail?.pop_label || 'POP 品质详情'" size="82%" destroy-on-close>
      <template v-if="detail">
        <el-descriptions :column="4" border size="small" class="detail-summary">
          <el-descriptions-item label="管理 IP">{{ detail.mgmt_ip || '-' }}</el-descriptions-item>
          <el-descriptions-item label="管理 URL">{{ detail.mgmt_url || '-' }}</el-descriptions-item>
          <el-descriptions-item label="当前状态">
            <el-tag :type="qualityStatusTag(detail.status)" size="small">{{ detail.status_label }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="最近探测">{{ formatTime(detail.last_probe_at) || '-' }}</el-descriptions-item>
          <el-descriptions-item label="当前丢包">{{ percent(detail.last_icmp_packet_loss_pct) }}</el-descriptions-item>
          <el-descriptions-item label="ICMP 平均">{{ ms(detail.last_icmp_avg_ms) }}</el-descriptions-item>
          <el-descriptions-item label="HTTP 响应">{{ ms(detail.last_http_response_ms) }}</el-descriptions-item>
          <el-descriptions-item label="告警状态">
            <el-tag :type="detail.alert_active ? 'danger' : 'success'" size="small">{{ detail.alert_active ? '告警中' : '正常' }}</el-tag>
          </el-descriptions-item>
        </el-descriptions>

        <div class="history-toolbar">
          <MonitoringTimeRange v-model="timeQuery" label="图表范围" @change="loadHistory" />
          <el-button :icon="Refresh" :loading="historyLoading" @click="loadHistory">刷新图表</el-button>
        </div>
        <el-row :gutter="16" v-loading="historyLoading">
          <el-col :xs="24" :lg="12">
            <MonitoringChart
              title="品质趋势（丢包率 / ICMP 可用性）"
              :chart-data="history.quality_chart"
              :show-period="false"
              :height="320"
            />
          </el-col>
          <el-col :xs="24" :lg="12">
            <MonitoringChart
              title="响应时间趋势（ICMP / 管理 URL）"
              :chart-data="history.latency_chart"
              :show-period="false"
              :height="320"
            />
          </el-col>
        </el-row>

        <div class="probe-history-head">
          <span>最近采样</span>
          <span class="muted">显示最近 30 条，图表已按范围聚合</span>
        </div>
        <el-table :data="detail.recent_probes || []" border size="small" max-height="300">
          <el-table-column label="时间" width="165">
            <template #default="{ row }">{{ formatTime(row.started_at) }}</template>
          </el-table-column>
          <el-table-column prop="status_label" label="状态" width="100">
            <template #default="{ row }"><el-tag size="small" :type="probeStatusTag(row.status)">{{ row.status_label }}</el-tag></template>
          </el-table-column>
          <el-table-column label="ICMP" min-width="180">
            <template #default="{ row }">丢包 {{ percent(row.icmp_packet_loss_pct) }} · {{ ms(row.icmp_avg_ms) }}</template>
          </el-table-column>
          <el-table-column label="HTTP" min-width="140">
            <template #default="{ row }">{{ row.http_status_code || '-' }} · {{ ms(row.http_response_ms) }}</template>
          </el-table-column>
          <el-table-column prop="alert_kind_label" label="告警变化" width="100" />
          <el-table-column prop="error_message" label="异常" min-width="200" show-overflow-tooltip />
        </el-table>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Search } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import MonitoringChart from '@/components/MonitoringChart.vue'
import MonitoringTimeRange from '@/components/MonitoringTimeRange.vue'
import { getPopPoints } from '@/api/resource'
import {
  createPopQualityMonitor,
  deletePopQualityMonitor,
  discoverPopQualityMonitors,
  getPopQualityHistory,
  getPopQualityMonitor,
  getPopQualityMonitors,
  probePopQualityMonitor,
  updatePopQualityMonitor,
} from '@/api/operation'

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('operation.manage') || authStore.user?.is_superuser)

const monitors = ref([])
const loading = ref(false)
const discovering = ref(false)
const filters = reactive({ status: '', alertActive: '', search: '' })
const statusOptions = [
  { value: 'unknown', label: '未探测' },
  { value: 'healthy', label: '正常' },
  { value: 'degraded', label: '品质下降' },
  { value: 'unreachable', label: '不可达' },
  { value: 'error', label: '探测异常' },
  { value: 'skipped', label: '已跳过' },
  { value: 'disabled', label: '已停用' },
]

function extractError(error, fallback = '操作失败') {
  const data = error?.response?.data
  if (!data) return error?.message || fallback
  const detail = data.detail ?? data
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.join('；')
  if (typeof detail === 'object') {
    const parts = Object.entries(detail).map(([key, value]) => {
      const text = Array.isArray(value) ? value.join('，') : String(value)
      return key === 'non_field_errors' ? text : `${key}：${text}`
    })
    return parts.join('；') || fallback
  }
  return fallback
}

async function load() {
  loading.value = true
  try {
    const params = { page_size: 2000 }
    if (filters.status) params.status = filters.status
    if (filters.alertActive !== '') params.alert_active = filters.alertActive
    if (filters.search) params.search = filters.search
    const response = await getPopQualityMonitors(params)
    monitors.value = response?.results || response || []
  } catch (error) {
    ElMessage.error(`加载品质监控失败：${extractError(error)}`)
  } finally {
    loading.value = false
  }
}

function qualityStatusTag(status) {
  return {
    healthy: 'success', degraded: 'warning', unreachable: 'danger',
    error: 'danger', skipped: 'info', disabled: 'info', unknown: 'info',
  }[status] || 'info'
}
function probeStatusTag(status) {
  return { success: 'success', degraded: 'warning', failed: 'danger', error: 'danger', skipped: 'info' }[status] || 'info'
}
function popStatusTag(status) {
  return { online: 'success', maintenance: 'warning', offline: 'danger', decommissioned: 'info' }[status] || 'info'
}
function number(value) {
  return value == null ? '-' : Number(value).toFixed(2).replace(/\.00$/, '')
}
function percent(value) {
  return value == null ? '-' : `${number(value)}%`
}
function ms(value) {
  return value == null ? '-' : `${number(value)} ms`
}
function formatTime(value) {
  if (!value) return ''
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

async function discover() {
  discovering.value = true
  try {
    const result = await discoverPopQualityMonitors()
    ElMessage.success(result.message || `已新增 ${result.created || 0} 个 POP 品质监控`)
    await load()
  } catch (error) {
    ElMessage.error(`发现 POP 失败：${extractError(error)}`)
  } finally {
    discovering.value = false
  }
}

const popOptions = ref([])
const popLoading = ref(false)

async function loadPopOptions(query = '') {
  popLoading.value = true
  try {
    const response = await getPopPoints({ search: query, page_size: 200 })
    popOptions.value = response?.results || response || []
  } catch (error) {
    ElMessage.error(`加载 POP 列表失败：${extractError(error)}`)
  } finally {
    popLoading.value = false
  }
}
function popLabel(pop) {
  return `[${pop.code || '-'}] ${pop.name}${pop.mgmt_ip ? ` · ${pop.mgmt_ip}` : ''}`
}

const defaultConfig = () => ({
  id: null,
  pop_point: null,
  is_enabled: true,
  enable_icmp: true,
  enable_http: true,
  probe_interval_seconds: 60,
  timeout_seconds: 5,
  icmp_count: 4,
  packet_loss_threshold_pct: 20,
  consecutive_breach_threshold: 2,
  alert_repeat_minutes: 15,
  remark: '',
})
const configVisible = ref(false)
const configForm = reactive(defaultConfig())
const configFormRef = ref()
const saving = ref(false)
const selectedPop = computed(() => (
  popOptions.value.find((pop) => Number(pop.id) === Number(configForm.pop_point)) || null
))

function rangeValidator(label, min, max) {
  return (_rule, value, callback) => {
    const numberValue = Number(value)
    if (!Number.isFinite(numberValue) || numberValue < min || numberValue > max) {
      callback(new Error(`${label}必须在 ${min} 到 ${max} 之间`))
      return
    }
    callback()
  }
}

const configRules = {
  pop_point: [{ required: true, message: '请选择关联 POP', trigger: 'change' }],
  probe_interval_seconds: [{ validator: rangeValidator('探测间隔（秒）', 15, 86400), trigger: 'change' }],
  timeout_seconds: [{ validator: rangeValidator('单次超时（秒）', 1, 60), trigger: 'change' }],
  icmp_count: [{ validator: rangeValidator('每轮 Ping 次数', 1, 20), trigger: 'change' }],
  packet_loss_threshold_pct: [{ validator: rangeValidator('丢包率阈值（%）', 0, 100), trigger: 'change' }],
  consecutive_breach_threshold: [{ validator: rangeValidator('连续超阈次数', 1, 100), trigger: 'change' }],
  alert_repeat_minutes: [{ validator: rangeValidator('重复提醒间隔（分钟）', 1, 1440), trigger: 'change' }],
}

function normalizedNumber(value, fallback) {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : fallback
}

async function openConfig(row = null) {
  Object.assign(configForm, defaultConfig())
  if (row) {
    Object.assign(configForm, {
      id: row.id,
      pop_point: row.pop_point,
      is_enabled: row.is_enabled,
      enable_icmp: row.enable_icmp,
      enable_http: row.enable_http,
      probe_interval_seconds: normalizedNumber(row.probe_interval_seconds, 60),
      timeout_seconds: normalizedNumber(row.timeout_seconds, 5),
      icmp_count: normalizedNumber(row.icmp_count, 4),
      packet_loss_threshold_pct: normalizedNumber(row.packet_loss_threshold_pct, 20),
      consecutive_breach_threshold: normalizedNumber(row.consecutive_breach_threshold, 2),
      alert_repeat_minutes: normalizedNumber(row.alert_repeat_minutes, 15),
      remark: row.remark || '',
    })
    popOptions.value = [{
      id: row.pop_point,
      code: row.pop_code,
      name: row.pop_name,
      mgmt_ip: row.mgmt_ip,
    }]
  } else {
    await loadPopOptions()
  }
  configVisible.value = true
}

async function saveConfig() {
  if (!configForm.enable_icmp && !configForm.enable_http) {
    ElMessage.warning('请至少启用一种探测方式')
    return
  }
  try {
    await configFormRef.value?.validate()
  } catch {
    return
  }
  saving.value = true
  try {
    const payload = { ...configForm }
    delete payload.id
    if (configForm.id) await updatePopQualityMonitor(configForm.id, payload)
    else await createPopQualityMonitor(payload)
    ElMessage.success('品质监控配置已保存，后台守护进程将自动执行探测')
    configVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(`保存失败：${extractError(error)}`)
  } finally {
    saving.value = false
  }
}

async function toggleEnabled(row) {
  const enabled = row.is_enabled
  try {
    await updatePopQualityMonitor(row.id, { is_enabled: enabled })
    ElMessage.success(enabled ? '已启用品质监控' : '已停用品质监控')
  } catch (error) {
    row.is_enabled = !enabled
    ElMessage.error(`更新失败：${extractError(error)}`)
  }
}

const probingId = ref(null)
async function probeNow(row) {
  probingId.value = row.id
  try {
    const result = await probePopQualityMonitor(row.id)
    ElMessage.success(`探测完成：${result.probe?.status_label || '已记录'}`)
    await load()
    if (detailVisible.value && detail.value?.id === row.id) {
      detail.value = result.monitor
      await loadHistory()
    }
  } catch (error) {
    ElMessage.error(`探测失败：${extractError(error)}`)
  } finally {
    probingId.value = null
  }
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(`确认删除「${row.pop_label}」的品质监控配置及全部历史采样吗？`, '删除确认', {
      type: 'warning',
    })
    await deletePopQualityMonitor(row.id)
    ElMessage.success('已删除品质监控配置')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(`删除失败：${extractError(error)}`)
  }
}

const detailVisible = ref(false)
const detail = ref(null)
const historyLoading = ref(false)
const timeQuery = ref({ period: '24h' })
const history = reactive({ quality_chart: null, latency_chart: null })

async function openDetail(row) {
  detailVisible.value = true
  detail.value = null
  history.quality_chart = null
  history.latency_chart = null
  try {
    detail.value = await getPopQualityMonitor(row.id)
    await loadHistory()
  } catch (error) {
    ElMessage.error(`加载品质详情失败：${extractError(error)}`)
    detailVisible.value = false
  }
}
async function loadHistory() {
  if (!detail.value?.id) return
  historyLoading.value = true
  try {
    const result = await getPopQualityHistory(detail.value.id, timeQuery.value)
    history.quality_chart = result.quality_chart
    history.latency_chart = result.latency_chart
  } catch (error) {
    ElMessage.error(`加载品质图表失败：${extractError(error)}`)
  } finally {
    historyLoading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.quality-page { min-height: 100%; }
.page-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 14px; }
.pt-title { margin-right: 12px; font-size: 18px; font-weight: 700; color: #303133; }
.toolbar-actions, .filters { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.filters { margin-bottom: 12px; }
.daemon-tip { margin-bottom: 12px; }
.pop-name { margin-bottom: 4px; font-weight: 600; color: #303133; }
.muted, .form-tip { font-size: 12px; color: #909399; }
.metric-row { margin-top: 4px; font-size: 12px; color: #606266; }
.metric-row strong { color: #303133; }
.quality-config-form { padding: 0 8px; }
.quality-config-form :deep(.el-form-item) { margin-bottom: 18px; }
.quality-config-form :deep(.el-form-item__label) { padding-bottom: 7px; font-weight: 600; color: #303133; line-height: 1.2; }
.switch-grid :deep(.el-form-item) { margin-bottom: 8px; }
.target-preview {
  display: flex; align-items: center; flex-wrap: wrap; gap: 8px;
  margin: -2px 0 14px; padding: 10px 12px;
  color: #606266; font-size: 13px; background: #f5f7fa; border-radius: 6px;
}
.form-tip-block { margin: 2px 0 8px; line-height: 1.6; }
.number-field { display: flex; align-items: center; gap: 8px; width: 100%; }
.number-field :deep(.el-input-number) { flex: 1; width: 100%; }
.number-field > span { min-width: 32px; color: #909399; font-size: 13px; white-space: nowrap; }
.history-toolbar { display: flex; align-items: center; gap: 10px; margin: 20px 0 14px; }
.history-toolbar .time-range-bar { flex: 1; }
.detail-summary { margin-bottom: 8px; }
.probe-history-head { display: flex; justify-content: space-between; align-items: center; margin: 22px 0 10px; font-size: 14px; font-weight: 600; }
@media (max-width: 768px) {
  .page-toolbar, .history-toolbar { align-items: flex-start; flex-direction: column; }
  .quality-config-form { padding: 0; }
}
</style>
