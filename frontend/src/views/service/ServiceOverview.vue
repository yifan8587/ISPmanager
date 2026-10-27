<template>
  <div class="page-container">
    <div class="page-header">
      <h2 class="page-title">服务全景统计</h2>
      <el-button type="primary" :icon="Refresh" @click="loadData">刷新</el-button>
    </div>

    <el-card shadow="never" class="search-card">
      <el-form :model="filters" inline>
        <el-form-item label="客户">
          <el-select v-model="filters.customer" filterable clearable placeholder="全部客户" style="width:220px">
            <el-option v-for="c in customers" :key="c.id" :label="`${c.customer_no || ''} ${c.name}`" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="时间段">
          <el-date-picker
            v-model="dateRange"
            type="daterange"
            value-format="YYYY-MM-DD"
            start-placeholder="开始"
            end-placeholder="结束"
            style="width:260px"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="loadData">查询</el-button>
          <el-button @click="resetFilters">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-row :gutter="12" style="margin-top:12px" v-loading="loading">
      <el-col :span="6" v-for="card in summaryCards" :key="card.label">
        <el-card shadow="never" class="stat-card">
          <div class="stat-label">{{ card.label }}</div>
          <div class="stat-value">{{ card.value }}</div>
          <div class="stat-sub" v-if="card.sub">{{ card.sub }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="12" style="margin-top:12px">
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>工单状态分布</template>
          <el-table :data="statusRows" size="small" stripe>
            <el-table-column prop="label" label="状态" />
            <el-table-column prop="count" label="数量" width="100" align="right" />
          </el-table>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>生命周期阶段</template>
          <el-table :data="stageRows" size="small" stripe>
            <el-table-column prop="label" label="阶段" />
            <el-table-column prop="count" label="工单数" width="100" align="right" />
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" style="margin-top:12px">
      <template #header>
        <div class="card-head">
          <span>客户服务汇总（Top 50）</span>
          <span class="hint">可点击客户筛选上方条件后再次查询</span>
        </div>
      </template>
      <el-table :data="overview.customers || []" stripe border size="small" @row-click="pickCustomer">
        <el-table-column prop="customer_no" label="客户编号" width="140" />
        <el-table-column prop="customer_name" label="客户名称" min-width="160" />
        <el-table-column prop="order_count" label="工单数" width="90" align="right" />
        <el-table-column prop="active_orders" label="服务中" width="90" align="right" />
        <el-table-column prop="quoted_sum" label="报价合计" width="120" align="right">
          <template #default="{ row }">¥{{ Number(row.quoted_sum || 0).toFixed(2) }}</template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-row :gutter="12" style="margin-top:12px">
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>资费统计（供后续计费）</template>
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="活跃实例月费合计">
              ¥{{ Number(overview.fees?.active_monthly_fee_total || 0).toFixed(2) }}
            </el-descriptions-item>
            <el-descriptions-item label="工单报价合计">
              ¥{{ Number(overview.fees?.quoted_total || 0).toFixed(2) }}
            </el-descriptions-item>
            <el-descriptions-item label="已完成变更差额">
              ¥{{ Number(overview.fees?.change_price_delta_total || 0).toFixed(2) }}
            </el-descriptions-item>
            <el-descriptions-item label="按币种月费">
              <span v-for="(v, k) in (overview.fees?.by_currency || {})" :key="k" style="margin-right:12px">
                {{ k }}: {{ Number(v).toFixed(2) }}
              </span>
              <span v-if="!Object.keys(overview.fees?.by_currency || {}).length">-</span>
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>月度工单趋势</template>
          <el-table :data="overview.trends?.monthly_orders || []" size="small" stripe max-height="280">
            <el-table-column prop="month" label="月份" width="100" />
            <el-table-column prop="count" label="工单数" width="90" align="right" />
            <el-table-column prop="quoted" label="报价合计" align="right">
              <template #default="{ row }">¥{{ Number(row.quoted || 0).toFixed(2) }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { Refresh, Search } from '@element-plus/icons-vue'
import { getServiceOverview } from '@/api/service'
import { getCustomers } from '@/api/customer'

const loading = ref(false)
const customers = ref([])
const overview = ref({})
const dateRange = ref([])
const filters = reactive({ customer: null })

const statusLabel = {
  draft: '草稿', pending: '待审批', approved: '已审批', provisioning: '开通中',
  active: '服务中', suspended: '已暂停', terminated: '已终止', rejected: '已驳回',
  suspend_pending: '待暂停', terminate_pending: '待终止',
}
const stageLabel = {
  order: '下单', requirement: '需求确认', test: '测试',
  usage: '正式使用', change: '变更', termination: '终止',
}

const summaryCards = computed(() => {
  const o = overview.value || {}
  return [
    { label: '工单总数', value: o.orders?.total ?? 0, sub: `服务中 ${o.orders?.by_status?.active || 0}` },
    { label: '服务实例', value: o.instances?.total ?? 0, sub: `活跃 ${o.instances?.active_count || 0} / 测试 ${o.instances?.trial_count || 0}` },
    { label: '变更单', value: o.changes?.total ?? 0, sub: `待审 ${o.changes?.pending || 0} / 完成 ${o.changes?.completed || 0}` },
    { label: '活跃月费', value: `¥${Number(o.fees?.active_monthly_fee_total || 0).toFixed(2)}`, sub: '正式计费实例合计' },
  ]
})

const statusRows = computed(() => {
  const map = overview.value?.orders?.by_status || {}
  return Object.entries(map).map(([k, v]) => ({ label: statusLabel[k] || k, count: v }))
})
const stageRows = computed(() => {
  const map = overview.value?.orders?.by_stage || {}
  return Object.entries(map).map(([k, v]) => ({ label: stageLabel[k] || k, count: v }))
})

function pickCustomer(row) {
  filters.customer = row.customer_id
  loadData()
}

function resetFilters() {
  filters.customer = null
  dateRange.value = []
  loadData()
}

async function loadData() {
  loading.value = true
  try {
    const params = {}
    if (filters.customer) params.customer = filters.customer
    if (dateRange.value?.length === 2) {
      params.start = dateRange.value[0]
      params.end = dateRange.value[1]
    }
    overview.value = await getServiceOverview(params) || {}
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  try {
    const res = await getCustomers({ page_size: 200 })
    customers.value = res?.results || res || []
  } catch { customers.value = [] }
  loadData()
})
</script>

<style scoped>
.page-container { min-height: 100%; }
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.page-title { font-size: 18px; font-weight: 700; color: #1a1a2e; margin: 0; }
.search-card :deep(.el-card__body) { padding: 16px 16px 0; }
.stat-card { min-height: 110px; }
.stat-label { color: #909399; font-size: 13px; }
.stat-value { font-size: 26px; font-weight: 700; color: #1a1a2e; margin-top: 8px; }
.stat-sub { color: #a0a3ad; font-size: 12px; margin-top: 6px; }
.card-head { display: flex; justify-content: space-between; align-items: center; }
.hint { color: #909399; font-size: 12px; font-weight: 400; }
</style>
