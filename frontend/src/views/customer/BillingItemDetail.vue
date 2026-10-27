<template>
  <div class="billing-item-page">
    <div class="page-header">
      <el-button :icon="ArrowLeft" @click="goBack">返回</el-button>
      <span class="ph-title">计费明细</span>
    </div>

    <template v-if="detailRow">
      <el-descriptions :column="2" border size="small">
        <el-descriptions-item label="计费项">{{ detailRow.description }}</el-descriptions-item>
        <el-descriptions-item label="关联产品">{{ detailRow.product_name || '—' }}</el-descriptions-item>
        <el-descriptions-item label="计费依据">{{ detailRow.detail?.basis_label || '—' }}</el-descriptions-item>
        <el-descriptions-item label="计费周期">{{ detailRow.period_start }} ~ {{ detailRow.period_end }}</el-descriptions-item>
        <el-descriptions-item label="计费方式">
          <el-tag v-if="detailRow.is_trial" size="small" type="info">测试（未计费）</el-tag>
          <el-tag v-else-if="detailRow.is_prorated" size="small" type="warning">按天比例</el-tag>
          <el-tag v-else size="small" type="success">整月</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="使用天数">{{ detailRow.days_used }} / {{ detailRow.days_in_month }} 天</el-descriptions-item>
      </el-descriptions>

      <!-- 规格对比：实际 vs 模版基准 -->
      <template v-if="detailRow.detail?.spec_dims?.length">
        <el-divider content-position="left">规格对比（实际 vs 模版基准）</el-divider>
        <el-table :data="detailRow.detail.spec_dims" size="small" border>
          <el-table-column prop="label" label="资源项" width="120" />
          <el-table-column label="模版基准" align="right">
            <template #default="{ row }">{{ row.base }} {{ row.unit }}</template>
          </el-table-column>
          <el-table-column label="实际分配" align="right">
            <template #default="{ row }">{{ row.actual }} {{ row.unit }}</template>
          </el-table-column>
          <el-table-column label="超出" align="right">
            <template #default="{ row }">
              <span v-if="row.over > 0" class="overage-text">+{{ row.over }} {{ row.unit }}</span>
              <span v-else style="color:#67c23a">0</span>
            </template>
          </el-table-column>
        </el-table>
      </template>

      <!-- 超出阶梯计价逐项 -->
      <template v-if="detailRow.detail?.overage_items?.length">
        <el-divider content-position="left">超出部分阶梯计价</el-divider>
        <el-table :data="detailRow.detail.overage_items" size="small" border>
          <el-table-column prop="label" label="资源项" width="120" />
          <el-table-column prop="upgrade_qty" label="超出单位数" align="right" />
          <el-table-column label="月租增量" align="right">
            <template #default="{ row }">+{{ curSym(detailRow.source_currency) }}{{ Number(row.monthly_delta).toFixed(2) }}</template>
          </el-table-column>
        </el-table>
      </template>

      <!-- 计费公式汇总 -->
      <el-divider content-position="left">计费汇总</el-divider>
      <div class="calc-box">
        <div class="calc-row">
          <span>产品基础月租</span>
          <span>{{ curSym(detailRow.source_currency) }}{{ Number(detailRow.base_monthly_fee).toFixed(2) }}</span>
        </div>
        <div class="calc-row">
          <span>超出部分月租</span>
          <span :class="{ 'overage-text': Number(detailRow.overage_monthly_fee) > 0 }">
            +{{ curSym(detailRow.source_currency) }}{{ Number(detailRow.overage_monthly_fee).toFixed(2) }}
          </span>
        </div>
        <div class="calc-row calc-sub">
          <span>整月单价（基础 + 超出）</span>
          <span>{{ curSym(detailRow.source_currency) }}{{ Number(detailRow.unit_monthly_fee).toFixed(2) }}</span>
        </div>
        <div class="calc-row" v-if="detailRow.is_prorated">
          <span>× 按天比例（{{ detailRow.days_used }}/{{ detailRow.days_in_month }}）</span>
          <span>× {{ detailRow.detail?.ratio || (detailRow.days_used / detailRow.days_in_month).toFixed(4) }}</span>
        </div>
        <div class="calc-row calc-sub">
          <span>原始金额</span>
          <span>{{ curSym(detailRow.source_currency) }}{{ Number(detailRow.amount_original).toFixed(2) }}</span>
        </div>
        <div class="calc-row" v-if="detailRow.source_currency !== detailRow.currency">
          <span>× 折算汇率（{{ detailRow.source_currency }} → {{ detailRow.currency }}）</span>
          <span>× {{ detailRow.exchange_rate }}</span>
        </div>
        <div class="calc-row calc-total">
          <span>结算金额{{ detailRow.is_trial ? '（测试，不计入账单）' : '' }}</span>
          <span>{{ curSym(detailRow.currency) }}{{ Number(detailRow.amount).toFixed(2) }}</span>
        </div>
      </div>
    </template>
    <el-empty v-else description="请从账单明细列表进入查看" />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft } from '@element-plus/icons-vue'
import { getCurrencies } from '@/api/system'

const router = useRouter()

const detailRow = ref(null)
const currencySymbols = ref({ CNY: '¥', USD: '$', CREDIT: '积分' })

function curSym(c) { return currencySymbols.value[c] || c || '¥' }

function goBack() {
  router.push('/customer/billings')
}

onMounted(async () => {
  const item = window.history.state?.billingItem
  if (item) {
    try { detailRow.value = typeof item === 'string' ? JSON.parse(item) : item } catch { detailRow.value = item }
  }
  try {
    const res = await getCurrencies()
    const list = res?.results || res || []
    const map = { CNY: '¥', USD: '$', CREDIT: '积分' }
    for (const c of list) { if (c.code && c.symbol) map[c.code] = c.symbol }
    currencySymbols.value = map
  } catch { /* keep defaults */ }
})
</script>

<style scoped>
.billing-item-page { background:#fff; border-radius:8px; padding:16px 20px 24px; min-height:calc(100vh - 120px); }
.page-header { display:flex; align-items:center; gap:10px; margin-bottom:16px; padding-bottom:14px; border-bottom:1px solid #ebeef5; }
.ph-title { font-size:16px; font-weight:700; color:#1a1a2e; }
.overage-text { color:#e6a23c; font-weight:600; }
.calc-box { background:#f8f9fb; border:1px solid #ebeef5; border-radius:8px; padding:12px 16px; }
.calc-row { display:flex; justify-content:space-between; padding:5px 0; font-size:13px; color:#606266; }
.calc-sub { border-top:1px dashed #dcdfe6; margin-top:4px; padding-top:8px; font-weight:600; color:#303133; }
.calc-total { border-top:2px solid #409eff; margin-top:6px; padding-top:10px; font-size:15px; font-weight:700; color:#409eff; }
</style>
