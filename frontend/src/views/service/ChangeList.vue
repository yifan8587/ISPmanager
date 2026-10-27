<template>
  <div class="page-container">
    <div class="page-header">
      <h2 class="page-title">变更记录</h2>
    </div>

    <el-card shadow="never" class="search-card">
      <el-form :model="searchForm" inline>
        <el-form-item label="变更类型">
          <el-select v-model="searchForm.change_type" clearable placeholder="全部" style="width:130px">
            <el-option v-for="t in typeOptions" :key="t.value" :label="t.label" :value="t.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="searchForm.status" clearable placeholder="全部" style="width:120px">
            <el-option v-for="s in statusOptions" :key="s.value" :label="s.label" :value="s.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="客户">
          <el-select v-model="searchForm.service_instance__service_order__customer" filterable clearable placeholder="全部" style="width:180px">
            <el-option v-for="c in customers" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="时间">
          <el-date-picker v-model="dateRange" type="daterange" value-format="YYYY-MM-DD"
            start-placeholder="开始" end-placeholder="结束" style="width:240px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="loadData">搜索</el-button>
          <el-button @click="resetSearch">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" style="margin-top:12px;">
      <el-table :data="tableData" v-loading="loading" stripe border>
        <el-table-column prop="change_no" label="变更编号" width="160" />
        <el-table-column prop="instance_no" label="实例编号" width="160" />
        <el-table-column prop="customer_name" label="客户" width="130" />
        <el-table-column prop="change_type" label="变更类型" width="110">
          <template #default="{ row }">
            <el-tag :type="typeMap[row.change_type]?.type" size="small">{{ typeMap[row.change_type]?.label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusMap[row.status]?.type" size="small">{{ statusMap[row.status]?.label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="price_delta" label="价格差额" width="100" align="right">
          <template #default="{ row }">{{ row.price_delta != null ? row.price_delta : '-' }}</template>
        </el-table-column>
        <el-table-column label="支付" width="150">
          <template #default="{ row }">
            <template v-if="row.payment_billing">
              <el-tag size="small" :type="row.payment_billing.status === 'paid' ? 'success' : 'warning'">
                {{ row.payment_billing.status_label }}
              </el-tag>
              <div class="muted">{{ row.payment_billing.bill_no }}</div>
            </template>
            <el-tag v-else-if="row.payment_required" size="small" type="warning">待生成账单</el-tag>
            <span v-else class="muted">无需支付</span>
          </template>
        </el-table-column>
        <el-table-column prop="requested_by_name" label="申请人" width="100" />
        <el-table-column prop="handler_name" label="处理人" width="100">
          <template #default="{ row }">{{ row.handler_name || '-' }}</template>
        </el-table-column>
        <el-table-column prop="executed_by_name" label="执行人" width="100">
          <template #default="{ row }">{{ row.executed_by_name || '-' }}</template>
        </el-table-column>
        <el-table-column prop="created_at" label="申请时间" width="160">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="change_reason" label="原因" min-width="140" show-overflow-tooltip />
        <el-table-column label="操作" width="260" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button>
            <el-button v-if="canManage && row.status === 'pending'" link type="warning" size="small" @click="handleApprove(row, 'approved')">批准</el-button>
            <el-button v-if="canManage && row.status === 'pending'" link type="danger" size="small" @click="handleApprove(row, 'rejected')">驳回</el-button>
            <el-button v-if="canManage && canExecute(row)" link type="success" size="small" @click="handleExecute(row)">执行</el-button>
            <el-button v-if="canManage && row.status === 'completed'" link type="warning" size="small" @click="handleRollback(row)">回滚</el-button>
            <el-button v-if="canManage && ['pending','approved','in_progress'].includes(row.status)" link type="info" size="small" @click="handleCancel(row)">取消</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination v-model:current-page="page" :total="total"
        layout="total, prev, pager, next" class="pagination" @change="loadData" />
    </el-card>

    <el-drawer v-model="detailVisible" title="变更详情" size="560px" destroy-on-close>
      <template v-if="detail">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="变更编号">{{ detail.change_no }}</el-descriptions-item>
          <el-descriptions-item label="实例">{{ detail.instance_no }}</el-descriptions-item>
          <el-descriptions-item label="客户">{{ detail.customer_name }}</el-descriptions-item>
          <el-descriptions-item label="类型">{{ detail.change_type_label || detail.change_type }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ detail.status_label || detail.status }}</el-descriptions-item>
          <el-descriptions-item label="关联工单">{{ detail.related_order_no || '-' }} {{ detail.related_order_status ? `（${detail.related_order_status}）` : '' }}</el-descriptions-item>
          <el-descriptions-item label="支付状态">
            <template v-if="detail.payment_billing">
              {{ detail.payment_billing.status_label }} · {{ detail.payment_billing.bill_no }}
              （{{ detail.payment_billing.currency }}{{ detail.payment_billing.amount }}）
            </template>
            <span v-else-if="detail.payment_required">待支付补差价，付款后才能执行</span>
            <span v-else>无需支付</span>
          </el-descriptions-item>
          <el-descriptions-item label="申请人">{{ detail.requested_by_name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="审批人">{{ detail.approved_by_name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="处理人">{{ detail.handler_name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="执行人">{{ detail.executed_by_name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="价格差额">{{ detail.price_delta != null ? detail.price_delta : '-' }}</el-descriptions-item>
          <el-descriptions-item label="原因">{{ detail.change_reason }}</el-descriptions-item>
          <el-descriptions-item label="详情">{{ detail.change_detail || '-' }}</el-descriptions-item>
        </el-descriptions>
        <el-divider>变更前资源</el-divider>
        <pre class="snap-pre">{{ JSON.stringify(detail.before_spec || {}, null, 2) }}</pre>
        <el-divider>变更后资源</el-divider>
        <pre class="snap-pre">{{ JSON.stringify(detail.after_spec || {}, null, 2) }}</pre>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import { getChanges, getChange, approveChange, executeChange, rollbackChange, cancelChange } from '@/api/service'
import { getCustomers } from '@/api/customer'
import { usePermission } from '@/composables/usePermission'

const { canManagePageAction, canManageModule } = usePermission()
const canManage = computed(() => canManagePageAction('service', 'change') || canManageModule('service'))

const loading = ref(false)
const tableData = ref([])
const total = ref(0)
const page = ref(1)
const customers = ref([])
const dateRange = ref([])
const detailVisible = ref(false)
const detail = ref(null)

const searchForm = reactive({
  change_type: '',
  status: '',
  service_instance__service_order__customer: null,
})

const typeOptions = [
  { value: 'upgrade', label: '升级' }, { value: 'downgrade', label: '降级' },
  { value: 'extend', label: '续期' }, { value: 'ip_change', label: 'IP变更' },
  { value: 'bw_change', label: '带宽变更' }, { value: 'protocol_change', label: '协议变更' },
  { value: 'migrate', label: '迁移' }, { value: 'other', label: '其他' }
]
const typeMap = {
  upgrade: { label: '升级', type: 'warning' }, downgrade: { label: '降级', type: 'info' },
  extend: { label: '续期', type: 'success' }, ip_change: { label: 'IP变更', type: 'primary' },
  bw_change: { label: '带宽变更', type: 'primary' }, protocol_change: { label: '协议变更', type: '' },
  migrate: { label: '迁移', type: 'warning' }, other: { label: '其他', type: 'info' }
}
const statusOptions = [
  { value: 'pending', label: '待审批' }, { value: 'approved', label: '已批准' },
  { value: 'in_progress', label: '变更中' }, { value: 'completed', label: '已完成' },
  { value: 'rolled_back', label: '已回滚' }, { value: 'rejected', label: '已驳回' }, { value: 'cancelled', label: '已取消' }
]
const statusMap = {
  pending: { label: '待审批', type: 'warning' }, approved: { label: '已批准', type: 'success' },
  in_progress: { label: '变更中', type: 'primary' }, completed: { label: '已完成', type: 'success' },
  rolled_back: { label: '已回滚', type: 'warning' },
  rejected: { label: '已驳回', type: 'danger' }, cancelled: { label: '已取消', type: 'info' }
}

function formatDate(date) { return date ? dayjs(date).format('YYYY-MM-DD HH:mm') : '-' }

function canExecute(row) {
  if (!['approved', 'in_progress'].includes(row.status)) return false
  return !row.payment_required
}

function formatApiError(error, fallback = '操作失败') {
  const data = error?.response?.data
  if (typeof data?.detail === 'string') return data.detail
  return error?.message || fallback
}

async function loadData() {
  loading.value = true
  try {
    const params = { ...searchForm, page: page.value, page_size: 20 }
    if (dateRange.value?.length === 2) {
      params.start = dateRange.value[0]
      params.end = dateRange.value[1]
    }
    Object.keys(params).forEach(k => {
      if (params[k] === '' || params[k] == null) delete params[k]
    })
    const res = await getChanges(params)
    tableData.value = res?.results || []
    total.value = res?.count || 0
  } finally { loading.value = false }
}

function resetSearch() {
  Object.assign(searchForm, { change_type: '', status: '', service_instance__service_order__customer: null })
  dateRange.value = []
  loadData()
}

async function openDetail(row) {
  detailVisible.value = true
  try {
    detail.value = await getChange(row.change_no)
  } catch {
    detail.value = row
  }
}

async function handleApprove(row, status) {
  let reject_reason = ''
  if (status === 'rejected') {
    const res = await ElMessageBox.prompt('请填写驳回原因', '驳回变更', { inputPlaceholder: '原因' }).catch(() => null)
    if (!res) return
    reject_reason = res.value
  } else {
    const needPay = Number(row.price_delta || 0) > 0 || row.payment_required
    await ElMessageBox.confirm(
      needPay
        ? '确定批准该变更？若关联工单有补差价，将同步生成预付账单，客户付款后才能执行。'
        : '确定批准该变更？无补差价时可直接执行。',
      '批准变更',
      { type: 'info' },
    )
  }
  try {
    const result = await approveChange(row.change_no, { status, reject_reason })
    if (status !== 'approved') {
      ElMessage.success('已驳回')
    } else if (result?.payment_billing && result.payment_billing.status !== 'paid') {
      ElMessage.success(`已批准，等待支付账单 ${result.payment_billing.bill_no}`)
    } else {
      ElMessage.success('已批准')
    }
    loadData()
  } catch (e) {
    ElMessage.error(formatApiError(e, '审批失败'))
  }
}

async function handleExecute(row) {
  await ElMessageBox.confirm(
    '确定执行该变更？将按变更后规格更新资源，并记录变更前后快照。',
    '执行变更',
    { type: 'warning' },
  )
  try {
    await executeChange(row.change_no)
    ElMessage.success('变更已执行完成')
    loadData()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e.message || '执行失败')
  }
}

async function handleRollback(row) {
  await ElMessageBox.confirm(
    '确定回滚？系统将依据变更前资源快照恢复本地资源规格；终止类变更不能自动回滚。',
    '回滚变更',
    { type: 'warning' },
  )
  try {
    await rollbackChange(row.change_no)
    ElMessage.success('变更已回滚')
    loadData()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e.message || '回滚失败')
  }
}

async function handleCancel(row) {
  await ElMessageBox.confirm(
    '确定取消该变更申请？未支付的关联工单将同步驳回。',
    '取消变更',
    { type: 'warning' },
  )
  try {
    await cancelChange(row.change_no)
    ElMessage.success('已取消')
    loadData()
  } catch (e) {
    ElMessage.error(formatApiError(e, '取消失败'))
  }
}

onMounted(async () => {
  try {
    const res = await getCustomers({ page_size: 200 })
    customers.value = res?.results || []
  } catch { customers.value = [] }
  loadData()
})
</script>

<style scoped>
.page-container { min-height: 100%; }
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.page-title { font-size: 18px; font-weight: 700; color: #1a1a2e; margin: 0; }
.search-card :deep(.el-card__body) { padding: 16px 16px 0; }
.pagination { margin-top: 16px; justify-content: flex-end; }
.snap-pre { font-size: 12px; white-space: pre-wrap; word-break: break-all; max-height: 260px; overflow: auto; background: #f5f7fa; padding: 10px; border-radius: 4px; }
.muted { color: #909399; font-size: 12px; }
</style>
