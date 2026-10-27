<template>
  <div class="page-container">
    <div class="page-header">
      <h2 class="page-title">供应商账单结算</h2>
      <div class="header-actions">
        <el-button v-if="canRequest" type="primary" :disabled="!summary.can_settle" @click="requestSettle">
          申请结算
        </el-button>
        <el-button v-if="canFinanceManage && !isSupplierAccount" type="success" :disabled="!summary.can_settle" @click="requestSettle">
          发起结算
        </el-button>
      </div>
    </div>

    <el-card shadow="never" class="search-card" v-if="!isSupplierAccount">
      <el-form inline>
        <el-form-item label="供应商">
          <el-select v-model="supplierId" filterable clearable placeholder="全部" style="width:220px" @change="onSupplierChange">
            <el-option v-for="s in supplierOptions" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="billStatus" clearable placeholder="全部" style="width:140px" @change="loadBills">
            <el-option label="待结算" value="pending" />
            <el-option label="结算中" value="in_settlement" />
            <el-option label="已结算" value="settled" />
          </el-select>
        </el-form-item>
      </el-form>
    </el-card>

    <el-row :gutter="12" class="summary-row">
      <el-col :span="6">
        <el-card shadow="never"><div class="stat-label">结算比例</div><div class="stat-value">{{ summary.settlement_rate ?? '-' }}%</div></el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never"><div class="stat-label">满额阈值</div><div class="stat-value">{{ summary.settlement_min_amount ?? '-' }}</div></el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never"><div class="stat-label">待结算金额</div><div class="stat-value">{{ summary.pending_settle_amount ?? 0 }}</div></el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never"><div class="stat-label">已结算</div><div class="stat-value">{{ summary.settled_amount ?? 0 }}</div></el-card>
      </el-col>
    </el-row>
    <div v-if="summary.threshold_gap > 0" class="muted gap-hint">
      距满额结算还差 {{ summary.threshold_gap }} {{ summary.settlement_currency }}
    </div>

    <el-card shadow="never" style="margin-top:12px;">
      <template #header>采购账单</template>
      <el-table :data="bills" v-loading="billLoading" stripe border>
        <el-table-column prop="bill_no" label="账单号" width="170" />
        <el-table-column prop="customer_name" label="客户" min-width="120" />
        <el-table-column prop="product_name" label="产品/服务" min-width="140" />
        <el-table-column prop="customer_bill_no" label="客户账单" width="150" />
        <el-table-column prop="source_amount" label="用户实付" width="110" />
        <el-table-column prop="settlement_rate" label="比例%" width="80" />
        <el-table-column prop="settle_amount" label="应结金额" width="110" />
        <el-table-column prop="currency" label="币种" width="70" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="billColor[row.status]">{{ row.status_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="入账时间" width="170" />
      </el-table>
      <el-pagination v-model:current-page="billPage" v-model:page-size="billPageSize" :total="billTotal"
        layout="total, prev, pager, next" class="pagination" @change="loadBills" />
    </el-card>

    <el-card shadow="never" style="margin-top:12px;">
      <template #header>结算单</template>
      <el-table :data="settlements" v-loading="settlementLoading" stripe border>
        <el-table-column prop="settlement_no" label="结算单号" width="170" />
        <el-table-column prop="supplier_name" label="供应商" min-width="120" />
        <el-table-column prop="amount" label="金额" width="110" />
        <el-table-column prop="currency" label="币种" width="70" />
        <el-table-column prop="bill_count" label="笔数" width="70" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="settleColor[row.status]">{{ row.status_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="90">
          <template #default="{ row }">{{ row.auto_created ? '满额自动' : '手工' }}</template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="170" />
        <el-table-column v-if="canFinanceManage" label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row.status === 'pending'" link type="success" size="small" @click="paySettle(row)">确认打款</el-button>
            <el-button v-if="row.status === 'pending'" link type="danger" size="small" @click="cancelSettle(row)">取消</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination v-model:current-page="settlePage" v-model:page-size="settlePageSize" :total="settleTotal"
        layout="total, prev, pager, next" class="pagination" @change="loadSettlements" />
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import {
  getSuppliersBrief, getSupplierBills, getSupplierBillSummary,
  getSupplierSettlements, requestSupplierSettlement,
  paySupplierSettlement, cancelSupplierSettlement,
} from '@/api/supplier'
import { pagePermission, userHasPermission } from '@/constants/permissions'

const auth = useAuthStore()
const isSupplierAccount = computed(() => auth.user?.role === 'supplier' || auth.user?.is_supplier_account)
const canFinanceManage = computed(() =>
  auth.user?.is_superuser ||
  userHasPermission(auth.user, pagePermission('supplier', 'list', 'manage')) ||
  userHasPermission(auth.user, pagePermission('supplier', 'billing', 'manage'))
)
const canRequest = computed(() => isSupplierAccount.value)

const supplierOptions = ref([])
const supplierId = ref(null)
const billStatus = ref('')
const summary = reactive({
  settlement_rate: null,
  settlement_min_amount: null,
  pending_settle_amount: 0,
  settled_amount: 0,
  can_settle: false,
  threshold_gap: 0,
  settlement_currency: 'CNY',
})

const bills = ref([])
const billLoading = ref(false)
const billTotal = ref(0)
const billPage = ref(1)
const billPageSize = ref(20)
const settlements = ref([])
const settlementLoading = ref(false)
const settleTotal = ref(0)
const settlePage = ref(1)
const settlePageSize = ref(20)

const billColor = { pending: 'warning', in_settlement: '', settled: 'success', cancelled: 'info' }
const settleColor = { pending: 'warning', paid: 'success', cancelled: 'info' }

async function loadOptions() {
  if (isSupplierAccount.value) return
  const list = await getSuppliersBrief().catch(() => [])
  supplierOptions.value = list || []
  if (!supplierId.value && supplierOptions.value.length) {
    supplierId.value = supplierOptions.value[0].id
  }
}

async function loadSummary() {
  const params = {}
  if (!isSupplierAccount.value && supplierId.value) params.supplier = supplierId.value
  const data = await getSupplierBillSummary(params).catch(() => null)
  if (data) Object.assign(summary, data)
}

async function loadBills() {
  billLoading.value = true
  try {
    const params = { page: billPage.value, page_size: billPageSize.value }
    if (billStatus.value) params.status = billStatus.value
    if (!isSupplierAccount.value && supplierId.value) params.supplier = supplierId.value
    const res = await getSupplierBills(params)
    bills.value = res?.results || []
    billTotal.value = res?.count || 0
  } finally { billLoading.value = false }
}

async function loadSettlements() {
  settlementLoading.value = true
  try {
    const params = { page: settlePage.value, page_size: settlePageSize.value }
    if (!isSupplierAccount.value && supplierId.value) params.supplier = supplierId.value
    const res = await getSupplierSettlements(params)
    settlements.value = res?.results || []
    settleTotal.value = res?.count || 0
  } finally { settlementLoading.value = false }
}

async function reloadAll() {
  await Promise.all([loadSummary(), loadBills(), loadSettlements()])
}

function onSupplierChange() {
  billPage.value = 1
  settlePage.value = 1
  reloadAll()
}

async function requestSettle() {
  await ElMessageBox.confirm('将把当前待结算账单生成一张待打款结算单，确认继续？', '申请结算', { type: 'info' })
  const payload = {}
  if (!isSupplierAccount.value && supplierId.value) payload.supplier = supplierId.value
  await requestSupplierSettlement(payload)
  ElMessage.success('已生成结算单')
  reloadAll()
}

async function paySettle(row) {
  await ElMessageBox.confirm(`确认已向供应商打款 ${row.amount} ${row.currency}？`, '确认打款', { type: 'warning' })
  await paySupplierSettlement(row.id, {})
  ElMessage.success('已标记结算完成')
  reloadAll()
}

async function cancelSettle(row) {
  await ElMessageBox.confirm('取消后账单将退回待结算，确认？', '取消结算单', { type: 'warning' })
  await cancelSupplierSettlement(row.id)
  ElMessage.success('已取消')
  reloadAll()
}

onMounted(async () => {
  await loadOptions()
  await reloadAll()
})
</script>

<style scoped>
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.page-title { font-size: 18px; font-weight: 700; color: #1a1a2e; margin: 0; }
.search-card :deep(.el-card__body) { padding: 16px 16px 0; }
.summary-row { margin-top: 12px; }
.stat-label { color: #909399; font-size: 13px; }
.stat-value { font-size: 22px; font-weight: 700; margin-top: 6px; }
.pagination { margin-top: 16px; justify-content: flex-end; }
.muted { color: #909399; font-size: 12px; }
.gap-hint { margin-top: 8px; }
</style>
