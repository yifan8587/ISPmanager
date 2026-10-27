<template>
  <div class="page-container">
    <div class="page-header">
      <h2 class="page-title">服务实例</h2>
    </div>

    <el-card shadow="never" class="search-card">
      <el-form :model="searchForm" inline>
        <el-form-item label="状态">
          <el-select v-model="searchForm.status" clearable placeholder="全部" style="width:120px">
            <el-option v-for="s in statusOptions" :key="s.value" :label="s.label" :value="s.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="服务类型">
          <el-select v-model="searchForm.service_type" clearable placeholder="全部" style="width:130px">
            <el-option v-for="t in typeOptions" :key="t.value" :label="t.label" :value="t.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="计费">
          <el-select v-model="searchForm.is_trial" clearable placeholder="全部" style="width:120px">
            <el-option label="正式计费" :value="false" />
            <el-option label="测试服务" :value="true" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="loadData">搜索</el-button>
          <el-button @click="resetSearch">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" style="margin-top:12px;">
      <el-table :data="tableData" v-loading="loading" stripe border>
        <el-table-column prop="instance_no" label="实例编号" width="160" />
        <el-table-column prop="customer_name" label="客户" width="130" />
        <el-table-column prop="service_type" label="服务类型" width="120">
          <template #default="{ row }">
            <el-tag :type="typeMap[row.service_type]?.type" size="small">{{ typeMap[row.service_type]?.label || row.service_type }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusMap[row.status]?.type" size="small">{{ statusMap[row.status]?.label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="计费" width="140">
          <template #default="{ row }">
            <el-tag v-if="row.is_trial" type="info" size="small">测试服务</el-tag>
            <span v-else>{{ curSym(row.fee_currency) }}{{ Number(row.monthly_fee || 0).toFixed(2) }}/月</span>
          </template>
        </el-table-column>
        <!-- 后端字段：provisioned_at（非start_date）-->
        <el-table-column label="开通时间" width="150">
          <template #default="{ row }">{{ formatDate(row.provisioned_at) }}</template>
        </el-table-column>
        <!-- 后端字段：expire_time（非end_date）-->
        <el-table-column label="到期时间" width="150">
          <template #default="{ row }">
            <span :class="isExpiringSoon(row.expire_time) ? 'text-warning' : ''">
              {{ formatDate(row.expire_time) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="计划停机" width="150">
          <template #default="{ row }">{{ formatDate(row.suspend_at) }}</template>
        </el-table-column>
        <el-table-column label="计划回收" width="150">
          <template #default="{ row }">{{ formatDate(row.recycle_at) }}</template>
        </el-table-column>
        <!-- 后端字段：order_no（服务工单号）-->
        <el-table-column prop="order_no" label="工单号" width="170" />
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button v-if="canManage && row.status === 'provisioning'" link type="success" size="small" @click="handleActivate(row)">激活</el-button>
            <el-button v-if="canManage && row.status === 'active'" link type="warning" size="small" @click="handleSuspend(row)">暂停</el-button>
            <el-button v-if="canManage && row.status === 'suspended'" link type="primary" size="small" @click="handleResume(row)">恢复</el-button>
            <el-button v-if="canManage && ['active','suspended'].includes(row.status)" link type="danger" size="small" @click="handleTerminate(row)">终止</el-button>
            <el-button v-if="canManage && row.is_trial" link type="success" size="small" @click="handleConvert(row)">转入计费</el-button>
            <el-button v-if="canManage" link type="primary" size="small" @click="openBillingDialog(row)">计费设置</el-button>
            <el-button link type="info" size="small" @click="viewLogs(row)">日志</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total"
        layout="total, prev, pager, next" class="pagination" @change="loadData" />
    </el-card>

    <!-- 计费设置对话框 -->
    <el-dialog v-model="billingDialogVisible" :title="`计费设置 - ${billingRow?.instance_no || ''}`" width="520px" destroy-on-close>
      <el-form :model="billingForm" label-width="100px">
        <el-form-item label="测试服务">
          <el-switch v-model="billingForm.is_trial" />
          <span class="hint">测试期间默认不计入账单</span>
        </el-form-item>
        <el-form-item label="整月月费">
          <el-input-number v-model="billingForm.monthly_fee" :min="0" :precision="2" controls-position="right" style="width:160px" />
          <el-select v-model="billingForm.fee_currency" style="width:110px; margin-left:8px">
            <el-option v-for="c in currencyChoices" :key="c.value" :label="c.value" :value="c.value" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="billingForm.is_trial" label="测试开始日">
          <el-date-picker v-model="billingForm.trial_start" type="date" value-format="YYYY-MM-DD" style="width:100%" />
        </el-form-item>
        <el-form-item v-if="billingForm.is_trial" label="测试结束日">
          <el-date-picker v-model="billingForm.trial_end" type="date" value-format="YYYY-MM-DD" style="width:100%" />
        </el-form-item>
        <el-form-item v-else label="计费起始日">
          <el-date-picker v-model="billingForm.billing_start_date" type="date" value-format="YYYY-MM-DD" placeholder="留空则取激活日" style="width:100%" />
        </el-form-item>
        <el-form-item label="到期时间">
          <el-date-picker v-model="billingForm.expire_time" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" style="width:100%" />
        </el-form-item>
        <el-form-item label="宽限天数">
          <el-input-number v-model="billingForm.grace_days" :min="0" :precision="0" controls-position="right" style="width:160px" />
          <span class="hint">留空则用系统参数，默认 3 天</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="billingDialogVisible = false">取消</el-button>
        <el-button v-if="canManage" type="primary" :loading="savingBilling" @click="saveBilling">保存</el-button>
      </template>
    </el-dialog>

  </div>
</template>

<script setup>
import { computed, ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import { getInstances, activateInstance, suspendInstance, resumeInstance, terminateInstance, convertInstanceToBilling, updateInstance } from '@/api/service'
import { usePermission } from '@/composables/usePermission'

const { canManagePageAction, canManageModule } = usePermission()
const canManage = computed(() => canManagePageAction('service', 'instance') || canManageModule('service'))

const router = useRouter()

const currencySymbols = { CNY: '¥', USD: '$', CREDIT: '积分' }
function curSym(c) { return currencySymbols[c] || c || '¥' }
const currencyChoices = [{ value: 'CNY' }, { value: 'USD' }, { value: 'CREDIT' }]

const billingDialogVisible = ref(false)
const savingBilling = ref(false)
const billingRow = ref(null)
const billingForm = reactive({
  is_trial: false, monthly_fee: 0, fee_currency: 'CNY',
  trial_start: '', trial_end: '', billing_start_date: '',
  expire_time: '', grace_days: null,
})

const loading = ref(false)
const tableData = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

const searchForm = reactive({ status: '', service_type: '', is_trial: '' })

// 与后端 ServiceInstance.ServiceType 对齐：vps/edge/ip/bandwidth
const typeOptions = [
  { value: 'vps', label: 'VPS' }, { value: 'edge', label: '边缘加速' },
  { value: 'ip', label: 'IP地址' }, { value: 'bandwidth', label: '带宽' }
]
const typeMap = {
  vps: { label: 'VPS', type: 'primary' }, edge: { label: '边缘加速', type: 'success' },
  ip: { label: 'IP地址', type: 'info' }, bandwidth: { label: '带宽', type: 'warning' }
}
const statusOptions = [
  { value: 'provisioning', label: '部署中' }, { value: 'active', label: '运行中' },
  { value: 'suspended', label: '已暂停' }, { value: 'terminated', label: '已终止' },
  { value: 'expired', label: '已过期' }, { value: 'error', label: '错误' }
]
const statusMap = {
  provisioning: { label: '部署中', type: 'primary' }, active: { label: '运行中', type: 'success' },
  suspended: { label: '已暂停', type: 'warning' }, terminated: { label: '已终止', type: 'danger' },
  expired: { label: '已过期', type: 'info' }, error: { label: '错误', type: 'danger' }
}

function isExpiringSoon(date) {
  if (!date) return false
  return dayjs(date).diff(dayjs(), 'day') <= 30
}

function formatDate(dt) {
  if (!dt) return '-'
  return dayjs(dt).format('YYYY-MM-DD HH:mm')
}

async function loadData() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (searchForm.status) params.status = searchForm.status
    if (searchForm.service_type) params.service_type = searchForm.service_type
    if (searchForm.is_trial !== '' && searchForm.is_trial !== null) params.is_trial = searchForm.is_trial
    const res = await getInstances(params)
    tableData.value = res?.results || []
    total.value = res?.count || 0
  } finally { loading.value = false }
}

function resetSearch() { Object.assign(searchForm, { status: '', service_type: '', is_trial: '' }); loadData() }

async function handleConvert(row) {
  try {
    await ElMessageBox.confirm(`确认将测试服务「${row.instance_no}」转入正式计费周期？转入后将从今日起计费。`, '转入计费', { type: 'warning' })
  } catch { return }
  try {
    await convertInstanceToBilling(row.id, {})
    ElMessage.success('已转入计费周期')
    loadData()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '操作失败')
  }
}

async function handleActivate(row) {
  await ElMessageBox.confirm('确定激活该实例？', '提示', { type: 'info' })
  await activateInstance(row.id)
  ElMessage.success('已激活')
  loadData()
}

async function handleSuspend(row) {
  const { value: reason } = await ElMessageBox.prompt('请输入暂停原因', '暂停实例', { inputPlaceholder: '原因（可选）' }).catch(() => ({ value: null }))
  if (reason === null) return
  await suspendInstance(row.id, { reason })
  ElMessage.success('已暂停')
  loadData()
}

async function handleResume(row) {
  await ElMessageBox.confirm('确定恢复该实例？', '提示', { type: 'info' })
  await resumeInstance(row.id)
  ElMessage.success('已恢复')
  loadData()
}

async function handleTerminate(row) {
  const { value: reason } = await ElMessageBox.prompt('请输入终止原因', '终止实例', { confirmButtonText: '确认终止', confirmButtonClass: 'el-button--danger', inputPlaceholder: '原因' })
  await terminateInstance(row.id, { reason })
  ElMessage.success('已终止')
  loadData()
}

function openBillingDialog(row) {
  billingRow.value = row
  Object.assign(billingForm, {
    is_trial: !!row.is_trial,
    monthly_fee: Number(row.monthly_fee || 0),
    fee_currency: row.fee_currency || 'CNY',
    trial_start: row.trial_start || '',
    trial_end: row.trial_end || '',
    billing_start_date: row.billing_start_date || '',
    expire_time: row.expire_time ? dayjs(row.expire_time).format('YYYY-MM-DDTHH:mm:ss') : '',
    grace_days: row.grace_days ?? null,
  })
  billingDialogVisible.value = true
}

async function saveBilling() {
  savingBilling.value = true
  try {
    const payload = {
      is_trial: billingForm.is_trial,
      monthly_fee: billingForm.monthly_fee,
      fee_currency: billingForm.fee_currency,
      trial_start: billingForm.trial_start || null,
      trial_end: billingForm.trial_end || null,
      billing_start_date: billingForm.billing_start_date || null,
      expire_time: billingForm.expire_time || null,
      grace_days: billingForm.grace_days === '' || billingForm.grace_days === null
        ? null
        : billingForm.grace_days,
    }
    await updateInstance(billingRow.value.id, payload)
    ElMessage.success('计费设置已保存')
    billingDialogVisible.value = false
    loadData()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally {
    savingBilling.value = false
  }
}

function viewLogs(row) {
  router.push(`/service/instances/${row.id}/logs`)
}

onMounted(loadData)
</script>

<style scoped>
.page-container { min-height: 100%; }
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.page-title { font-size: 18px; font-weight: 700; color: #1a1a2e; margin: 0; }
.search-card :deep(.el-card__body) { padding: 16px 16px 0; }
.pagination { margin-top: 16px; justify-content: flex-end; }
.text-warning { color: #E6A23C; font-weight: 600; }
.hint { font-size: 12px; color: #909399; margin-left: 8px; }
</style>
