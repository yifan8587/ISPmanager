<template>
  <div v-loading="pageLoading" class="page rtb-page">
    <div class="page-toolbar">
      <div class="pt-left">
        <el-button link type="primary" :icon="ArrowLeft" @click="goBack">返回</el-button>
        <span class="pt-title">流量截取 / 95 计费</span>
        <el-tag v-if="context.resource_name" size="small" effect="plain">{{ context.resource_name }}</el-tag>
        <el-text v-if="context.pop_point_label" type="info" size="small">{{ context.pop_point_label }}</el-text>
      </div>
    </div>

    <el-card shadow="never" class="query-card">
      <el-form :model="form" label-width="108px" size="default">
        <el-row :gutter="16">
          <el-col :xs="24" :md="14">
            <el-form-item label="监控图形/项" required>
              <el-select v-model="form.binding_id" placeholder="选择已关联的监控图形或监控项" style="width:100%">
                <el-option v-for="b in context.bindings" :key="b.binding_id" :label="b.label" :value="b.binding_id">
                  <div class="opt-main">{{ b.label }}</div>
                  <div class="opt-sub">{{ b.detail }} · {{ b.panel_slot_label || b.panel_slot }}</div>
                </el-option>
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :md="10">
            <el-form-item label="计费规则">
              <el-select v-model="form.profile" clearable placeholder="默认 95 计费" style="width:100%">
                <el-option v-for="p in profiles" :key="p.id" :label="p.name" :value="p.id" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item v-if="selectedBinding" label="来源">
          <el-text size="small" type="info">
            {{ selectedBinding.monitoring_host_name }}
            <template v-if="selectedBinding.interface_name"> · 接口 {{ selectedBinding.interface_name }}</template>
          </el-text>
        </el-form-item>

        <el-form-item label="时间范围" required>
          <MonitoringTimeRange v-model="timeQuery" label="" @change="onTimeChange" />
        </el-form-item>

        <el-form-item>
          <el-button type="primary" :icon="Search" :loading="querying" @click="runQuery">查询</el-button>
          <el-checkbox v-model="form.persist" style="margin-left:12px;">保存为报告</el-checkbox>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card v-if="result" shadow="never" class="chart-card">
      <MonitoringChart
        :chart-data="result"
        :show-period="false"
        :show-header="true"
        :height="520"
        :title="chartTitle"
      />
      <div v-if="result?.report_no" class="report-no">
        已保存报告：<el-tag size="small" type="success">{{ result.report_no }}</el-tag>
      </div>
    </el-card>

    <el-empty
      v-else-if="!pageLoading"
      :description="emptyHint"
      :image-size="80"
    />
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Search, ArrowLeft } from '@element-plus/icons-vue'
import MonitoringChart from '@/components/MonitoringChart.vue'
import MonitoringTimeRange from '@/components/MonitoringTimeRange.vue'
import { getGraphProfiles, queryTraffic, getTrafficResourceContext } from '@/api/operation'

const route = useRoute()
const router = useRouter()

const targetType = computed(() => route.meta.targetType || route.params.targetType)
const resourceId = computed(() => Number(route.params.id))

const pageLoading = ref(false)
const querying = ref(false)
const context = ref({
  resource_name: '',
  pop_point_label: '',
  bindings: [],
})
const profiles = ref([])
const result = ref(null)
const timeQuery = ref({ period: '24h' })

const form = reactive({
  binding_id: null,
  profile: null,
  persist: false,
})

const selectedBinding = computed(() =>
  context.value.bindings?.find(b => b.binding_id === form.binding_id) || null)

const chartTitle = computed(() => {
  const label = selectedBinding.value?.label || '流量截取'
  return `${context.value.resource_name || ''} · ${label}`
})

const emptyHint = computed(() =>
  context.value.bindings?.length
    ? '选择监控图形/监控项与时间范围后点击查询'
    : '请先在监控 Tab 关联监控图形/监控项，再进行流量截取')

function extractError(e) {
  const d = e?.response?.data
  if (!d) return e?.message || '请求失败'
  return d.detail || d.message || '请求失败'
}

function goBack() {
  router.back()
}

function onTimeChange() {
  result.value = null
}

async function loadContext() {
  pageLoading.value = true
  try {
    context.value = await getTrafficResourceContext({
      target_type: targetType.value,
      resource_id: resourceId.value,
    })
    const qBinding = route.query.binding_id ? Number(route.query.binding_id) : null
    const preferred = qBinding && context.value.bindings?.find(b => b.binding_id === qBinding)
    form.binding_id = preferred?.binding_id
      || context.value.default_binding_id
      || context.value.bindings?.[0]?.binding_id
      || null
    if (!context.value.bindings?.length) {
      ElMessage.warning('尚未关联监控图形/监控项，请先在监控 Tab 中关联')
    }
  } catch (e) {
    ElMessage.error('加载失败：' + extractError(e))
  } finally {
    pageLoading.value = false
  }
}

async function runQuery() {
  if (!form.binding_id) {
    ElMessage.warning('请选择已关联的监控图形或监控项')
    return
  }
  const tq = timeQuery.value
  const time_from = tq?.time_from
  const time_till = tq?.time_till
  if (!time_from || !time_till) {
    ElMessage.warning('请选择时间范围')
    return
  }
  querying.value = true
  try {
    const payload = {
      binding: form.binding_id,
      time_from,
      time_till,
      persist: form.persist,
      [targetType.value]: resourceId.value,
    }
    if (form.profile) payload.profile = form.profile
    result.value = await queryTraffic(payload)
    if (form.persist && result.value?.report_no) {
      ElMessage.success('查询完成，报告 ' + result.value.report_no)
    }
  } catch (e) {
    ElMessage.error('查询失败：' + extractError(e))
  } finally {
    querying.value = false
  }
}

onMounted(async () => {
  try {
    const res = await getGraphProfiles({ is_active: true })
    profiles.value = res?.results || res || []
  } catch { /* ignore */ }
  await loadContext()
})
</script>

<style scoped>
.rtb-page { min-height: calc(100vh - 120px); }
.page-toolbar { display: flex; align-items: center; margin-bottom: 14px; }
.pt-left { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.pt-title { font-size: 18px; font-weight: 700; color: #1a1a2e; }
.query-card { margin-bottom: 16px; background: #fafbff; }
.chart-card { padding: 4px 8px 12px; }
.report-no { margin-top: 12px; font-size: 13px; color: #606266; }
.opt-main { font-size: 13px; line-height: 1.4; }
.opt-sub { font-size: 11px; color: #909399; line-height: 1.3; }
</style>
