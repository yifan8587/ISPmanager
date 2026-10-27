<template>
  <div class="mon-chart">
    <div v-if="showHeader" class="mc-header">
      <span class="mc-title">{{ displayTitle }}</span>
      <div class="mc-tools">
        <el-radio-group v-if="showPeriod && !timeQuery" v-model="period" size="small" @change="reload">
          <el-radio-button value="1h">1h</el-radio-button>
          <el-radio-button value="6h">6h</el-radio-button>
          <el-radio-button value="24h">24h</el-radio-button>
          <el-radio-button value="7d">7d</el-radio-button>
          <el-radio-button value="30d">30d</el-radio-button>
        </el-radio-group>
        <el-button size="small" :icon="Refresh" circle :loading="loading" @click="reload" />
      </div>
    </div>

    <!-- 流量统计：入站 / 出站分列展示 -->
    <div v-if="isTrafficChart && billing" class="mc-stats-dual">
      <div class="ms-col ms-col-in">
        <div class="ms-col-head"><span class="dot in" />入站 (Inbound)</div>
        <div class="ms-grid">
          <div class="ms-cell">
            <span class="ms-num">{{ fmt(inStats.p95) }}</span>
            <span class="ms-lbl">{{ p95Label }}</span>
          </div>
          <div class="ms-cell">
            <span class="ms-num">{{ fmt(inStats.peak) }}</span>
            <span class="ms-lbl">峰值 (Mbps)</span>
          </div>
          <div class="ms-cell">
            <span class="ms-num">{{ fmt(inStats.avg) }}</span>
            <span class="ms-lbl">均值 (Mbps)</span>
          </div>
          <div class="ms-cell">
            <span class="ms-num">{{ gib(billing.total_in_bytes) }}</span>
            <span class="ms-lbl">总量 (GiB)</span>
          </div>
        </div>
      </div>
      <div class="ms-col ms-col-out">
        <div class="ms-col-head"><span class="dot out" />出站 (Outbound)</div>
        <div class="ms-grid">
          <div class="ms-cell">
            <span class="ms-num">{{ fmt(outStats.p95) }}</span>
            <span class="ms-lbl">{{ p95Label }}</span>
          </div>
          <div class="ms-cell">
            <span class="ms-num">{{ fmt(outStats.peak) }}</span>
            <span class="ms-lbl">峰值 (Mbps)</span>
          </div>
          <div class="ms-cell">
            <span class="ms-num">{{ fmt(outStats.avg) }}</span>
            <span class="ms-lbl">均值 (Mbps)</span>
          </div>
          <div class="ms-cell">
            <span class="ms-num">{{ gib(billing.total_out_bytes) }}</span>
            <span class="ms-lbl">总量 (GiB)</span>
          </div>
        </div>
      </div>
      <div class="ms-sample">
        <span class="ms-num sm">{{ billing.sample_count || 0 }}</span>
        <span class="ms-lbl">采样点</span>
      </div>
    </div>

    <div v-loading="loading" :style="{ height: height + 'px' }" class="mc-canvas">
      <v-chart v-if="hasData && chartOption" :option="chartOption" autoresize />
      <el-empty v-else-if="!loading" :description="errMsg || '暂无数据'" :image-size="60" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import {
  GridComponent, TooltipComponent, LegendComponent,
  DataZoomComponent, MarkLineComponent, TitleComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { getGraphChartData, getItemChartData } from '@/api/operation'

use([
  LineChart, GridComponent, TooltipComponent, LegendComponent,
  DataZoomComponent, MarkLineComponent, TitleComponent, CanvasRenderer,
])

const props = defineProps({
  graphId: { type: [Number, String], default: null },
  itemId: { type: [Number, String], default: null },
  chartData: { type: Object, default: null },
  title: { type: String, default: '' },
  height: { type: Number, default: 260 },
  defaultPeriod: { type: String, default: '24h' },
  showPeriod: { type: Boolean, default: true },
  showHeader: { type: Boolean, default: true },
  timeQuery: { type: Object, default: null },
  chartKey: { type: String, default: '' },
})

const loading = ref(false)
const errMsg = ref('')
const data = ref(props.chartData)
const period = ref(props.defaultPeriod)

const chartType = computed(() => data.value?.chart_type || 'traffic')
const isTrafficChart = computed(() => chartType.value === 'traffic')
const billing = computed(() => data.value?.billing || null)
const timeseries = computed(() => data.value?.timeseries || [])
const genericSeries = computed(() => {
  const s = data.value?.series
  return Array.isArray(s) ? s : (s ? Object.values(s) : [])
})

const p95Label = computed(() => `${billing.value?.percentile_n || 95} 峰值 (Mbps)`)

const inStats = computed(() => ({
  p95: billing.value?.percentile_in_mbps ?? toMbps(billing.value?.percentile_in_bps),
  peak: billing.value?.peak_in_mbps ?? toMbps(billing.value?.peak_in_bps),
  avg: billing.value?.avg_in_mbps ?? toMbps(billing.value?.avg_in_bps),
}))

const outStats = computed(() => ({
  p95: billing.value?.percentile_out_mbps ?? toMbps(billing.value?.percentile_out_bps),
  peak: billing.value?.peak_out_mbps ?? toMbps(billing.value?.peak_out_bps),
  avg: billing.value?.avg_out_mbps ?? toMbps(billing.value?.avg_out_bps),
}))

const displayTitle = computed(() =>
  props.title || data.value?.meta?.title || data.value?.meta?.interface_name || '监控图')

const hasData = computed(() => {
  if (chartType.value === 'generic') {
    return genericSeries.value.some(s => s.points?.length)
  }
  return timeseries.value.length > 0
})

function extractError(e) {
  const d = e?.response?.data
  if (!d) return e?.message || '加载失败'
  const detail = d.detail ?? d
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.join('；')
  if (typeof detail === 'object') {
    return Object.entries(detail).map(([k, v]) => {
      const val = Array.isArray(v) ? v.join('，') : v
      return k === 'non_field_errors' ? val : `${k}: ${val}`
    }).join('；') || d.message || '加载失败'
  }
  return d.message || '加载失败'
}

function buildQueryParams() {
  if (props.timeQuery?.time_from && props.timeQuery?.time_till) {
    return { time_from: props.timeQuery.time_from, time_till: props.timeQuery.time_till }
  }
  return { period: props.timeQuery?.period || period.value }
}

function toMbps(bps) {
  if (bps == null) return null
  return Math.round((bps / 1_000_000) * 100) / 100
}
function fmt(v) {
  if (v == null) return '-'
  return Number(v).toFixed(2)
}
function fmtAxis(v) {
  if (v == null || v === '') return ''
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(2) : v
}
function gib(bytes) {
  if (!bytes) return '0.00'
  return (bytes / 1024 / 1024 / 1024).toFixed(2)
}

function formatAxisTime(val) {
  const d = new Date(val)
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function baseGrid(bottom = 40) {
  return { left: 52, right: 16, top: 28, bottom }
}

function buildTrafficOption() {
  const rows = timeseries.value
  const x = rows.map(r => r.time)
  const inSeries = rows.map(r => toMbps(r.in_bps))
  const outSeries = rows.map(r => toMbps(r.out_bps))
  const p95Bill = toMbps(billing.value?.percentile_bps)

  const markLine = (billing.value && p95Bill != null) ? {
    silent: true, symbol: 'none',
    lineStyle: { color: '#F56C6C', type: 'dashed', width: 1.5 },
    data: [{
      yAxis: p95Bill,
      label: {
        formatter: `计费${billing.value.percentile_n || 95} ${p95Bill.toFixed(2)} Mbps`,
        position: 'insideEndTop', color: '#F56C6C', fontSize: 11,
      },
    }],
  } : undefined

  return {
    tooltip: {
      trigger: 'axis',
      valueFormatter: (v) => (v == null ? '-' : Number(v).toFixed(2) + ' Mbps'),
    },
    legend: { data: ['入站 (Inbound)', '出站 (Outbound)'], top: 0, right: 8, textStyle: { fontSize: 11 } },
    grid: baseGrid(),
    xAxis: {
      type: 'category', data: x, boundaryGap: false,
      axisLabel: { fontSize: 10, formatter: formatAxisTime },
    },
    yAxis: {
      type: 'value', name: 'Mbps', nameTextStyle: { fontSize: 10 },
      axisLabel: { fontSize: 10, formatter: fmtAxis },
    },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 16, bottom: 8 }],
    series: [
      {
        name: '入站 (Inbound)', type: 'line', smooth: true, showSymbol: false,
        areaStyle: { opacity: 0.12 }, lineStyle: { width: 1.5 },
        itemStyle: { color: '#409EFF' }, data: inSeries, markLine,
      },
      {
        name: '出站 (Outbound)', type: 'line', smooth: true, showSymbol: false,
        areaStyle: { opacity: 0.12 }, lineStyle: { width: 1.5 },
        itemStyle: { color: '#67C23A' }, data: outSeries,
      },
    ],
  }
}

function buildGenericOption() {
  const series = genericSeries.value
  const clockMap = new Map()
  series.forEach(s => {
    (s.points || []).forEach(p => clockMap.set(p.clock, p.time))
  })
  const clocks = [...clockMap.keys()].sort((a, b) => a - b)
  const x = clocks.map(c => clockMap.get(c))
  const unit = series.find(s => s.unit)?.unit || ''

  const eSeries = series.map(s => {
    const byClock = Object.fromEntries((s.points || []).map(p => [p.clock, p.value]))
    return {
      name: s.name,
      type: 'line', smooth: true, showSymbol: false,
      lineStyle: { width: 1.5 },
      itemStyle: { color: s.color || undefined },
      data: clocks.map(c => byClock[c] ?? null),
    }
  })

  return {
    tooltip: {
      trigger: 'axis',
      valueFormatter: (v) => (v == null ? '-' : Number(v).toFixed(2) + (unit ? ` ${unit}` : '')),
    },
    legend: {
      data: series.map(s => s.name),
      top: 0, right: 10, type: 'scroll', textStyle: { fontSize: 11 },
    },
    grid: baseGrid(),
    xAxis: {
      type: 'category', data: x, boundaryGap: false,
      axisLabel: { fontSize: 10, formatter: formatAxisTime },
    },
    yAxis: {
      type: 'value', name: unit, nameTextStyle: { fontSize: 10 },
      axisLabel: { fontSize: 10, formatter: fmtAxis },
    },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 16, bottom: 8 }],
    series: eSeries,
  }
}

const chartOption = computed(() => {
  if (!hasData.value) return null
  return isTrafficChart.value ? buildTrafficOption() : buildGenericOption()
})

async function reload() {
  if (!props.graphId && !props.itemId) return
  loading.value = true
  errMsg.value = ''
  try {
    const params = buildQueryParams()
    data.value = props.itemId
      ? await getItemChartData(props.itemId, params)
      : await getGraphChartData(props.graphId, params)
  } catch (e) {
    errMsg.value = extractError(e)
    data.value = null
  } finally {
    loading.value = false
  }
}

watch(() => props.chartData, (v) => { if (v) data.value = v })
watch(() => [props.graphId, props.itemId, props.chartKey], () => {
  if ((props.graphId || props.itemId) && !props.chartData) reload()
})
watch(() => props.timeQuery, () => {
  if ((props.graphId || props.itemId) && !props.chartData) reload()
}, { deep: true })

onMounted(() => {
  if ((props.graphId || props.itemId) && !props.chartData) reload()
})

defineExpose({ reload })
</script>

<style scoped>
.mon-chart { background: #fff; border: 1px solid #ebeef5; border-radius: 8px; padding: 10px 12px; }
.mc-header { display: flex; align-items: center; margin-bottom: 8px; }
.mc-title { font-size: 13px; font-weight: 600; color: #303133; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mc-tools { margin-left: auto; display: flex; align-items: center; gap: 8px; flex-shrink: 0; }

.mc-stats-dual {
  display: flex;
  align-items: stretch;
  gap: 10px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}
.ms-col {
  flex: 1;
  min-width: 200px;
  border-radius: 6px;
  padding: 8px 10px;
  border: 1px solid #ebeef5;
}
.ms-col-in { background: #f0f7ff; border-color: #d9ecff; }
.ms-col-out { background: #f0f9eb; border-color: #e1f3d8; }
.ms-col-head {
  font-size: 12px; font-weight: 600; margin-bottom: 8px;
  display: flex; align-items: center; gap: 6px;
}
.ms-col-in .ms-col-head { color: #409EFF; }
.ms-col-out .ms-col-head { color: #67C23A; }
.ms-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px; }
.ms-cell { text-align: center; padding: 2px 4px; }
.ms-num { display: block; font-size: 15px; font-weight: 700; color: #303133; line-height: 1.3; }
.ms-num.sm { font-size: 14px; }
.ms-lbl { display: block; font-size: 10px; color: #909399; margin-top: 2px; }
.ms-sample {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  padding: 8px 12px; border-left: 1px solid #f0f0f0; min-width: 56px;
}
.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
.dot.in { background: #409EFF; }
.dot.out { background: #67C23A; }
.mc-canvas { width: 100%; }

@media (max-width: 640px) {
  .ms-grid { grid-template-columns: repeat(2, 1fr); }
}
</style>
