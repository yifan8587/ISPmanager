<template>
  <div class="dashboard">
    <el-alert
      v-if="expiryAlerts.length"
      type="warning"
      show-icon
      :closable="false"
      style="margin-bottom: 16px"
      :title="`服务到期提醒：${expiryAlerts.length} 个服务需要处理`"
    >
      <div v-for="row in expiryAlerts" :key="row.id || row.instance_no">
        {{ row.instance_no }} · {{ row.customer_name || '客户' }} ·
        {{ row.is_trial ? '测试，到期前 1 天' : '正式，到期前 3 天' }} ·
        到期 {{ formatDate(row.expire_time) }}。
        {{ row.is_trial ? '未转正式则到期次日收回资源。' : '未续费则到期次日收回资源。' }}
      </div>
    </el-alert>
    <!-- 统计卡片 -->
    <el-row :gutter="16" class="stat-cards">
      <el-col :span="isChannelAccount ? 8 : 6" v-for="card in visibleStatCards" :key="card.key">
        <div class="stat-card" :style="{ '--card-color': card.color }">
          <div class="stat-icon">
            <el-icon :size="28"><component :is="card.icon" /></el-icon>
          </div>
          <div class="stat-info">
            <div class="stat-value">
              <count-up :end-val="card.value" :duration="1.5" :decimals="card.decimals || 0" />
            </div>
            <div class="stat-label">{{ card.label }}</div>
            <div class="stat-sub">{{ card.sub || '\u00A0' }}</div>
          </div>
        </div>
      </el-col>
    </el-row>

    <!-- POP 全球分布地图 -->
    <el-card v-if="!isChannelAccount" shadow="never" class="map-card" style="margin-top: 16px;">
      <template #header>
        <span class="card-title">
          <el-icon><Location /></el-icon> POP 节点全球分布
        </span>
      </template>
      <PopWorldMap ref="popMapRef" />
    </el-card>

    <!-- 图表区域 -->
    <el-row :gutter="16" style="margin-top: 16px;">
      <el-col :span="8">
        <el-card shadow="never" class="chart-card">
          <template #header>
            <span class="card-title">
              <el-icon><PieChart /></el-icon> 客户状态分布
            </span>
          </template>
          <div ref="customerChartRef" style="height: 240px;"></div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never" class="chart-card">
          <template #header>
            <span class="card-title">
              <el-icon><DataAnalysis /></el-icon> 服务实例状态
            </span>
          </template>
          <div ref="instanceChartRef" style="height: 240px;"></div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never" class="chart-card">
          <template #header>
            <span class="card-title">
              <el-icon><TrendCharts /></el-icon> 工单类型分布
            </span>
          </template>
          <div ref="orderChartRef" style="height: 240px;"></div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-top: 16px;">
      <el-col v-if="!isChannelAccount" :span="12">
        <el-card shadow="never" class="chart-card">
          <template #header>
            <span class="card-title">
              <el-icon><Connection /></el-icon> IP 资源使用情况
            </span>
          </template>
          <div ref="ipChartRef" style="height: 260px;"></div>
        </el-card>
      </el-col>
      <el-col :span="isChannelAccount ? 24 : 12">
        <el-card shadow="never" class="chart-card">
          <template #header>
            <span class="card-title">
              <el-icon><Money /></el-icon> 近期账单收入趋势
            </span>
          </template>
          <div ref="billingChartRef" style="height: 260px;"></div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-top: 16px;">
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>
            <div style="display:flex;align-items:center;justify-content:space-between;">
              <span class="card-title">
                <el-icon><List /></el-icon> 待处理工单
              </span>
              <el-button link type="primary" @click="$router.push('/service/orders')">查看全部</el-button>
            </div>
          </template>
          <el-table :data="pendingOrders" size="small" stripe>
            <el-table-column prop="order_no" label="工单号" width="130" />
            <el-table-column prop="customer_name" label="客户" />
            <el-table-column prop="order_type" label="类型" width="80">
              <template #default="{ row }">
                <el-tag size="small" :type="orderTypeMap[row.order_type]?.type">
                  {{ orderTypeMap[row.order_type]?.label || row.order_type }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="创建时间" width="100">
              <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>
            <div style="display:flex;align-items:center;justify-content:space-between;">
              <span class="card-title">
                <el-icon><AlarmClock /></el-icon> 即将到期服务（30天内）
              </span>
              <el-button link type="primary" @click="$router.push('/service/instances')">查看全部</el-button>
            </div>
          </template>
          <el-table :data="expiringInstances" size="small" stripe>
            <el-table-column prop="instance_no" label="实例编号" width="140" />
            <el-table-column prop="customer_name" label="客户" />
            <el-table-column prop="service_type" label="类型" width="80">
              <template #default="{ row }">
                <el-tag size="small" type="warning">{{ row.service_type }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="expire_time" label="到期时间" width="110">
              <template #default="{ row }">{{ formatDate(row.expire_time) }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import * as echarts from 'echarts'
import dayjs from 'dayjs'
import PopWorldMap from '@/components/PopWorldMap.vue'
import { getCustomerStatistics, getBillings } from '@/api/customer'
import { getOrderStatistics, getOrders, getInstanceStatistics, getInstances } from '@/api/service'
import { getIPAddresses, getPopStatistics } from '@/api/resource'

const authStore = useAuthStore()
const isChannelAccount = computed(() =>
  authStore.user?.role === 'channel' || authStore.user?.is_channel_account
)
const customerChartRef = ref(null)
const instanceChartRef = ref(null)
const orderChartRef = ref(null)
const ipChartRef = ref(null)
const billingChartRef = ref(null)
const popMapRef = ref(null)

const statCards = ref([
  { key: 'customers', label: '活跃客户', value: 0, icon: 'UserFilled', color: '#409EFF', sub: '' },
  { key: 'instances', label: '运行实例', value: 0, icon: 'Monitor', color: '#67C23A', sub: '' },
  { key: 'orders', label: '待处理工单', value: 0, icon: 'List', color: '#E6A23C', sub: '' },
  { key: 'pop', label: 'POP 节点', value: 0, icon: 'Location', color: '#F56C6C', sub: '' },
])

const pendingOrders = ref([])
const expiringInstances = ref([])
const expiryAlerts = ref([])
const visibleStatCards = computed(() =>
  isChannelAccount.value ? statCards.value.filter((card) => card.key !== 'pop') : statCards.value
)

const orderTypeMap = {
  new: { label: '新开', type: 'success' },
  renew: { label: '续费', type: 'primary' },
  upgrade: { label: '升级', type: 'warning' },
  downgrade: { label: '降级', type: 'info' },
  terminate: { label: '终止', type: 'danger' },
  change: { label: '变更', type: '' },
}

const ipStatusLabel = {
  available: '可用',
  assigned: '已分配',
  reserved: '预留',
  blocked: '封禁',
}

let charts = []

function formatDate(date) {
  return date ? dayjs(date).format('MM-DD HH:mm') : '-'
}

function toCountList(data, keyField = 'status') {
  if (Array.isArray(data)) return data
  return Object.entries(data || {}).map(([k, v]) => ({ [keyField]: k, count: v }))
}

function initCustomerChart(data) {
  if (!customerChartRef.value) return
  const chart = echarts.init(customerChartRef.value)
  charts.push(chart)
  const statusLabelMap = { active: '活跃', suspended: '暂停', cancelled: '注销', pending: '待审核' }
  const statusData = (data?.status_distribution || []).map((d) => ({
    value: d.count || 0,
    name: statusLabelMap[d.status] || d.status,
  }))
  if (!statusData.length) statusData.push({ value: 0, name: '暂无数据' })
  chart.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { bottom: 0, textStyle: { fontSize: 12 } },
    series: [{
      type: 'pie',
      radius: ['40%', '70%'],
      center: ['50%', '45%'],
      data: statusData,
      label: { show: false },
      itemStyle: { borderRadius: 4, borderWidth: 2, borderColor: '#fff' },
      color: ['#67C23A', '#E6A23C', '#909399', '#409EFF'],
    }],
  })
}

function initInstanceChart(data) {
  if (!instanceChartRef.value) return
  const chart = echarts.init(instanceChartRef.value)
  charts.push(chart)
  const statusData = toCountList(data?.by_status)
  const statusLabelMap = {
    active: '运行中', suspended: '已暂停', provisioning: '部署中',
    terminated: '已终止', expired: '已过期', error: '错误',
  }
  chart.setOption({
    tooltip: { trigger: 'item' },
    legend: { bottom: 0, textStyle: { fontSize: 12 } },
    series: [{
      type: 'pie',
      radius: ['40%', '70%'],
      center: ['50%', '45%'],
      data: statusData.map((s) => ({
        value: s.count || 0,
        name: statusLabelMap[s.status] || s.status,
      })),
      label: { show: false },
      itemStyle: { borderRadius: 4, borderWidth: 2, borderColor: '#fff' },
      color: ['#67C23A', '#E6A23C', '#409EFF', '#909399', '#C0C4CC', '#F56C6C'],
    }],
  })
}

function initOrderChart(data) {
  if (!orderChartRef.value) return
  const chart = echarts.init(orderChartRef.value)
  charts.push(chart)
  const byType = toCountList(data?.by_order_type, 'order_type')
  const typeLabelMap = {
    new: '新开', renew: '续费', upgrade: '升级',
    downgrade: '降级', terminate: '终止', change: '变更',
  }
  chart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { top: 10, left: 40, right: 10, bottom: 30 },
    xAxis: {
      type: 'category',
      data: byType.map((d) => typeLabelMap[d.order_type] || d.order_type),
      axisLabel: { fontSize: 11 },
    },
    yAxis: { type: 'value', minInterval: 1 },
    series: [{
      type: 'bar',
      data: byType.map((d) => d.count || 0),
      barMaxWidth: 40,
      itemStyle: { color: '#409EFF', borderRadius: [4, 4, 0, 0] },
    }],
  })
}

function initIPChart(counts) {
  if (!ipChartRef.value) return
  const chart = echarts.init(ipChartRef.value)
  charts.push(chart)
  const ipData = Object.entries(counts).map(([status, count]) => ({
    name: ipStatusLabel[status] || status,
    value: count,
    itemStyle: {
      color: { available: '#67C23A', assigned: '#409EFF', reserved: '#E6A23C', blocked: '#F56C6C' }[status] || '#909399',
    },
  }))
  if (!ipData.length) ipData.push({ name: '暂无数据', value: 0, itemStyle: { color: '#C0C4CC' } })
  chart.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c}' },
    legend: { right: 10, top: 'middle', orient: 'vertical', textStyle: { fontSize: 12 } },
    series: [{
      type: 'pie',
      radius: ['50%', '80%'],
      center: ['40%', '50%'],
      data: ipData,
      label: { formatter: '{b}\n{c}', fontSize: 11 },
    }],
  })
}

function initBillingChart(monthlyData) {
  if (!billingChartRef.value) return
  const chart = echarts.init(billingChartRef.value)
  charts.push(chart)
  const months = []
  const values = []
  for (let i = 5; i >= 0; i--) {
    const key = dayjs().subtract(i, 'month').format('YYYY-MM')
    months.push(key)
    values.push(monthlyData[key] || 0)
  }
  chart.setOption({
    tooltip: { trigger: 'axis', formatter: (params) => `${params[0].axisValue}<br/>账单金额: ¥${Number(params[0].value).toLocaleString()}` },
    grid: { top: 15, left: 55, right: 20, bottom: 30 },
    xAxis: { type: 'category', data: months, axisLabel: { fontSize: 11 } },
    yAxis: { type: 'value', axisLabel: { formatter: '¥{value}', fontSize: 11 } },
    series: [{
      type: 'line',
      data: values,
      smooth: true,
      symbol: 'circle',
      symbolSize: 6,
      lineStyle: { color: '#409EFF', width: 2 },
      itemStyle: { color: '#409EFF' },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: 'rgba(64,158,255,0.3)' },
            { offset: 1, color: 'rgba(64,158,255,0.02)' },
          ],
        },
      },
    }],
  })
}

async function loadIPStatusCounts() {
  const statuses = ['available', 'assigned', 'reserved', 'blocked']
  const entries = await Promise.all(
    statuses.map(async (status) => {
      try {
        const res = await getIPAddresses({ status, page_size: 1 })
        return [status, res.count || 0]
      } catch {
        return [status, 0]
      }
    }),
  )
  return Object.fromEntries(entries)
}

async function loadBillingMonthly() {
  const start = dayjs().subtract(5, 'month').startOf('month').format('YYYY-MM')
  try {
    const res = await getBillings({ page_size: 500, ordering: '-bill_month' })
    const rows = res.results || res || []
    const monthly = {}
    rows.forEach((row) => {
      const key = row.bill_month
      if (!key || key < start) return
      monthly[key] = (monthly[key] || 0) + Number(row.amount || 0)
    })
    return monthly
  } catch {
    return {}
  }
}

async function loadExpiringInstances() {
  try {
    const res = await getInstances({ status: 'active', page_size: 100, ordering: 'expire_time' })
    const rows = res.results || res || []
    const deadline = dayjs().add(30, 'day')
    const upcoming = rows.filter((r) => r.expire_time && dayjs(r.expire_time).isBefore(deadline))
    return {
      table: upcoming.slice(0, 5),
      alerts: upcoming.filter((row) => {
        const days = dayjs(row.expire_time).startOf('day').diff(dayjs().startOf('day'), 'day')
        if (days < -1) return false
        return row.is_trial ? days <= 1 : days <= 3
      }),
    }
  } catch {
    return { table: [], alerts: [] }
  }
}

async function loadData() {
  try {
    const skipResource = isChannelAccount.value
    const [
      customerStat, orderStat, instanceStat, popStat,
      pendingRes, ipCounts, billingMonthly, expiring,
    ] = await Promise.all([
      getCustomerStatistics().catch(() => ({})),
      getOrderStatistics().catch(() => ({})),
      getInstanceStatistics().catch(() => ({})),
      skipResource ? Promise.resolve({}) : getPopStatistics().catch(() => ({})),
      getOrders({ status: 'pending', page_size: 5 }).catch(() => ({ results: [] })),
      skipResource ? Promise.resolve({}) : loadIPStatusCounts(),
      loadBillingMonthly(),
      loadExpiringInstances(),
    ])

    statCards.value[0].value = customerStat?.active_customers || 0
    statCards.value[1].value = instanceStat?.active_count || 0
    statCards.value[2].value = orderStat?.by_status?.pending || 0
    statCards.value[3].value = popStat?.total || 0
    const onlinePop = (popStat?.by_status || []).find((s) => s.status === 'online')
    statCards.value[3].sub = `在线 ${onlinePop?.count || 0} · 总带宽 ${formatGbps(popStat?.total_bandwidth_mbps)}`

    pendingOrders.value = pendingRes?.results || []
    expiringInstances.value = expiring.table || []
    expiryAlerts.value = expiring.alerts || []

    initCustomerChart(customerStat)
    initInstanceChart(instanceStat)
    initOrderChart(orderStat)
    if (!skipResource) initIPChart(ipCounts)
    initBillingChart(billingMonthly)
  } catch {
    initCustomerChart({})
    initInstanceChart({})
    initOrderChart({})
    initIPChart({})
    initBillingChart({})
  }
}

function formatGbps(mbps) {
  const n = Number(mbps) || 0
  if (n >= 1000) return `${(n / 1000).toFixed(1)} Gbps`
  return `${n} Mbps`
}

const resizeHandler = () => charts.forEach((c) => c?.resize())

onMounted(() => {
  loadData()
  window.addEventListener('resize', resizeHandler)
})

onUnmounted(() => {
  window.removeEventListener('resize', resizeHandler)
  charts.forEach((c) => c?.dispose())
})
</script>

<script>
import { h } from 'vue'

const CountUp = {
  props: {
    endVal: Number,
    duration: { type: Number, default: 1.5 },
    decimals: { type: Number, default: 0 },
  },
  data() { return { current: 0 } },
  watch: {
    endVal: {
      immediate: true,
      handler(val) {
        const target = Number(val) || 0
        const start = this.current
        const steps = Math.max(1, Math.floor(this.duration * 60))
        const step = (target - start) / steps
        let tick = 0
        const timer = setInterval(() => {
          tick += 1
          this.current += step
          if (tick >= steps) {
            this.current = target
            clearInterval(timer)
          }
        }, 1000 / 60)
      },
    },
  },
  render() {
    const val = this.decimals > 0
      ? Number(this.current).toFixed(this.decimals)
      : Math.floor(this.current).toLocaleString()
    return h('span', val)
  },
}

export default { components: { CountUp } }
</script>

<style scoped>
.dashboard { min-height: 100%; }

.stat-cards :deep(.el-col) {
  display: flex;
}

.stat-card {
  background: #fff;
  border-radius: 12px;
  padding: 20px;
  display: flex;
  align-items: center;
  gap: 16px;
  box-shadow: 0 1px 6px rgba(0, 0, 0, 0.06);
  border-left: 4px solid var(--card-color);
  position: relative;
  overflow: hidden;
  width: 100%;
  min-height: 108px;
  height: 100%;
  box-sizing: border-box;
}

.stat-card::after {
  content: '';
  position: absolute;
  right: -20px;
  top: -20px;
  width: 80px;
  height: 80px;
  border-radius: 50%;
  background: var(--card-color);
  opacity: 0.06;
}

.stat-icon {
  width: 52px;
  height: 52px;
  border-radius: 12px;
  background: var(--card-color);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  flex-shrink: 0;
}

.stat-info { flex: 1; min-width: 0; }

.stat-value {
  font-size: 26px;
  font-weight: 700;
  color: #1a1a2e;
  line-height: 1;
  margin-bottom: 4px;
}

.stat-label { font-size: 12px; color: #909399; }
.stat-sub {
  min-height: 18px;
  margin-top: 4px;
  font-size: 11px;
  color: #a8abb2;
  line-height: 1.4;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chart-card, .map-card { height: 100%; }

.card-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  color: #333;
  font-size: 14px;
}

:deep(.el-card__header) {
  padding: 12px 16px;
  border-bottom: 1px solid #f0f0f0;
}

:deep(.el-card__body) {
  padding: 12px 16px;
}
</style>
