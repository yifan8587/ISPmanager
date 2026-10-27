<template>
  <div v-loading="loading && !current" class="product-detail-page">
    <!-- ── 标题栏 ── -->
    <div class="page-header">
      <el-button :icon="ArrowLeft" @click="goBack">返回</el-button>
      <template v-if="current">
        <span class="ph-name">{{ current.name }}</span>
        <el-tag size="small" :type="statusColor[current.status]">{{ current.status_label }}</el-tag>
        <span class="ph-no">{{ current.product_no }}</span>
      </template>
      <div class="ph-actions" v-if="current">
        <el-button size="small" :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
        <el-button size="small" type="primary" @click="goEdit">编辑</el-button>
      </div>
    </div>

    <el-tabs v-if="current" v-model="activeTab">
      <el-tab-pane label="概览" name="overview">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="产品编号">{{ current.product_no }}</el-descriptions-item>
          <el-descriptions-item label="状态"><el-tag size="small" :type="statusColor[current.status]">{{ current.status_label }}</el-tag></el-descriptions-item>
          <el-descriptions-item label="POP点">
            {{ current.pop_name }} ({{ current.pop_code }})
            <el-tag v-if="current.ip_delivery_mode === 'nat'" size="small" type="warning" style="margin-left:6px">
              {{ current.ip_delivery_mode_label || 'NAT 交付' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="区域/国家/城市">
            {{ [current.region_name, current.country_name, current.city_name, current.zone_name].filter(Boolean).join(' / ') || '-' }}
          </el-descriptions-item>
          <el-descriptions-item label="网络类型">
            <template v-if="productNetworkTypes(current).length">
              <el-tag
                v-for="t in productNetworkTypes(current)"
                :key="t.value"
                size="small"
                :type="t.value === 'home_broadband' ? 'warning' : 'primary'"
                style="margin-right:4px"
              >
                {{ t.label }}
              </el-tag>
            </template>
            <span v-else>-</span>
          </el-descriptions-item>
          <el-descriptions-item label="ASN/企业性质">
            <template v-if="productAsnNatures(current).length">
              <el-tag v-for="t in productAsnNatures(current)" :key="t.value" size="small" type="info" style="margin-right:4px">
                {{ t.label }}
              </el-tag>
            </template>
            <span v-else>-</span>
          </el-descriptions-item>
          <el-descriptions-item label="IP属性">
            <template v-if="(current.ip_attributes || []).length">
              <el-tag v-for="a in current.ip_attributes" :key="a" size="small" effect="plain" style="margin-right:4px">
                {{ ipAttrLabel(a) }}
              </el-tag>
            </template>
            <span v-else>-</span>
          </el-descriptions-item>
          <el-descriptions-item label="供应商">{{ current.supplier_name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="热卖">
            <el-tag v-if="current.hot_sale || current.is_hot_sale" size="small" type="danger">🔥 热卖</el-tag>
            <span v-else class="muted">未设置</span>
            <span v-if="current.sold_service_count" class="muted" style="margin-left:8px">在服 {{ current.sold_service_count }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="模版">{{ current.template_name || '-' }}</el-descriptions-item>
          <el-descriptions-item v-if="current.category === 'vps'" label="系统版本">
            <template v-if="(current.os_templates || []).length">
              <el-tag
                v-for="item in current.os_templates"
                :key="item.id"
                size="small"
                :type="item.is_stale ? 'danger' : 'info'"
                style="margin-right:4px"
              >
                {{ item.label || `${item.name} ${item.version}` }}{{ item.is_stale ? '（待升级）' : '' }}
              </el-tag>
            </template>
            <span v-else class="muted">全部已启用系统模版</span>
          </el-descriptions-item>
          <el-descriptions-item label="当前规格">
            <span v-if="current.category === 'vps'">{{ current.cpu_cores }}核/{{ ramText(current.ram_mb) }}/{{ current.disk_gb }}G/{{ current.bandwidth_mbps }}M/{{ current.ip_count }}IP</span>
            <span v-else>{{ current.accel_type_label || accelLabel(current.accel_type) }}/{{ current.bandwidth_mbps }}M/{{ current.ip_count }}IP</span>
          </el-descriptions-item>
          <el-descriptions-item label="计费模式">{{ current.billing_mode_label || '包月' }}</el-descriptions-item>
          <el-descriptions-item v-if="current.billing_mode === 'transfer_quota'" label="流量包">
            {{ current.transfer_quota_gb || '-' }} GB/月
          </el-descriptions-item>
          <el-descriptions-item label="月租价">${{ current.monthly_price }} USD</el-descriptions-item>
          <el-descriptions-item label="示例 IP">
            <span v-if="current.sample_ip_visible && current.sample_ip">{{ current.sample_ip }}</span>
            <span v-else class="muted">{{ current.sample_ip ? `${current.sample_ip}（不展示）` : '未设置' }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="预估开通">
            {{ current.estimated_provision_hours ? `${current.estimated_provision_hours} 小时` : '-' }}
            <el-tag v-if="current.allow_backorder" size="small" type="warning" style="margin-left:6px">缺货可售</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="信誉值">{{ current.reputation_price }}</el-descriptions-item>
        </el-descriptions>

        <el-alert v-if="current.template_pricing" type="info" :closable="false" style="margin-top:12px">
          <template #title>
            模版定价（{{ current.template_name }} @ {{ current.pop_name }}）：月租 ${{ current.template_pricing.monthly_price }}，
            信誉值 {{ current.template_pricing.reputation_price }}，安装费 ${{ current.template_pricing.setup_price }}
          </template>
        </el-alert>

        <div class="cap-row">
          <el-card shadow="never" class="cap-card">
            <div class="cap-title">资源容量校验</div>
            <el-tag v-if="capacity" :type="capacity.ok ? 'success' : 'danger'">{{ capacity.ok ? '资源充足' : '资源不足' }}</el-tag>
            <ul v-if="capacity && capacity.shortages.length" class="shortage">
              <li v-for="(s, i) in capacity.shortages" :key="i">{{ s }}</li>
            </ul>
          </el-card>
          <el-card shadow="never" class="cap-card">
            <div class="cap-title">库存估算（该POP可开通数）</div>
            <div class="stock-num">{{ stock?.stock === null || stock?.stock === undefined ? '不受限/未知' : stock?.stock }}</div>
            <div v-if="stock?.limiting_label" class="muted">瓶颈资源：{{ stock.limiting_label }}</div>
          </el-card>
        </div>

        <el-card v-if="capacity?.detail" shadow="never" style="margin-top:12px">
          <div class="cap-title">资源明细（有效容量含 CPU 超分 / 系统预留）</div>
          <el-table :data="capacityRows(capacity.detail)" size="small" border>
            <el-table-column prop="label" label="资源" width="120" />
            <el-table-column label="本产品需求" width="110" align="right"><template #default="{ row }">{{ row.need || '-' }}</template></el-table-column>
            <el-table-column label="已用 / 总量" min-width="150" align="right">
              <template #default="{ row }">{{ row.total === null ? '未知' : `${row.used ?? '-'} / ${row.total}` }}</template>
            </el-table-column>
            <el-table-column label="剩余" width="100" align="right"><template #default="{ row }">{{ row.available === null ? '-' : row.available }}</template></el-table-column>
            <el-table-column label="使用率" width="140">
              <template #default="{ row }">
                <el-progress v-if="row.usage_pct !== null" :percentage="row.usage_pct" :stroke-width="10" :color="usageColor(row.usage_pct)" />
                <span v-else class="muted">-</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="阶梯定价（超出模版）" name="rules">
        <div class="rules-head">
          <span class="muted">阶梯以模版规格为基准，超出部分按区间逐档计价</span>
          <el-button size="small" type="primary" :icon="Plus" @click="openRule()">新增规则</el-button>
        </div>
        <el-table :data="current.upgrade_rules || []" size="small" border>
          <el-table-column label="资源" width="110"><template #default="{ row }">{{ row.resource_kind_label }}</template></el-table-column>
          <el-table-column label="阶梯区间" width="130">
            <template #default="{ row }">{{ row.min_qty }} ~ {{ row.max_qty ?? '∞' }}</template>
          </el-table-column>
          <el-table-column label="每单位月租($)" align="right"><template #default="{ row }">${{ row.unit_price }}</template></el-table-column>
          <el-table-column label="每单位信誉" align="right"><template #default="{ row }">{{ row.unit_reputation }}</template></el-table-column>
          <el-table-column label="操作" width="120">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="openRule(row)">编辑</el-button>
              <el-button link type="danger" size="small" @click="removeRule(row)">删除</el-button>
            </template>
          </el-table-column>
          <template #empty>暂无阶梯规则</template>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="报价测算" name="quote">
        <el-form :model="quoteForm" label-width="92px" inline class="quote-form">
          <el-form-item label="CPU核心"><el-input-number v-model="quoteForm.cpu_cores" :min="0" controls-position="right" style="width:130px" /></el-form-item>
          <el-form-item label="内存(GB)"><el-input-number v-model="quoteRamGb" :min="0" :step="1" controls-position="right" style="width:130px" /></el-form-item>
          <el-form-item label="硬盘(GB)"><el-input-number v-model="quoteForm.disk_gb" :min="0" controls-position="right" style="width:130px" /></el-form-item>
          <el-form-item label="带宽(Mbps)"><el-input-number v-model="quoteForm.bandwidth_mbps" :min="0" controls-position="right" style="width:130px" /></el-form-item>
          <el-form-item label="IP数量"><el-input-number v-model="quoteForm.ip_count" :min="0" controls-position="right" style="width:130px" /></el-form-item>
          <el-form-item><el-button type="primary" :loading="quoting" @click="doQuote">测算</el-button></el-form-item>
        </el-form>
        <el-result v-if="quoteResult" :icon="quoteResult.capacity.ok ? 'success' : 'warning'"
          :title="`月租 $${quoteResult.price.monthly_price} / 信誉值 ${quoteResult.price.reputation_price}`"
          :sub-title="quoteResult.capacity.ok ? '资源充足' : ('资源不足：' + quoteResult.capacity.shortages.join('；'))">
        </el-result>

        <el-row v-if="quoteResult" :gutter="12" style="margin-top:4px">
          <el-col :span="12">
            <el-card shadow="never" class="cap-card">
              <div class="cap-title">该配置库存估算（POP 可开通数）</div>
              <div class="stock-num">
                {{ quoteResult.stock?.stock === null || quoteResult.stock?.stock === undefined ? '不受限/未知' : quoteResult.stock.stock }}
              </div>
              <div v-if="quoteResult.stock?.limiting_label" class="muted">瓶颈资源：{{ quoteResult.stock.limiting_label }}</div>
            </el-card>
          </el-col>
          <el-col :span="12">
            <el-card shadow="never" class="cap-card">
              <div class="cap-title">资源充足性</div>
              <el-tag :type="quoteResult.capacity.ok ? 'success' : 'danger'">{{ quoteResult.capacity.ok ? '资源充足' : '资源不足' }}</el-tag>
              <ul v-if="quoteResult.capacity.shortages.length" class="shortage">
                <li v-for="(s, i) in quoteResult.capacity.shortages" :key="i">{{ s }}</li>
              </ul>
            </el-card>
          </el-col>
        </el-row>

        <el-card v-if="quoteResult?.stock?.by_resource" shadow="never" style="margin-top:12px">
          <div class="cap-title">各资源可开通数（取最小值为库存）</div>
          <el-table :data="stockRows(quoteResult.stock.by_resource)" size="small" border>
            <el-table-column prop="label" label="资源" width="120" />
            <el-table-column label="单台需求" width="110" align="right"><template #default="{ row }">{{ row.need }}</template></el-table-column>
            <el-table-column label="剩余可用" width="120" align="right"><template #default="{ row }">{{ row.available }}</template></el-table-column>
            <el-table-column label="可开通数" align="right">
              <template #default="{ row }">
                <el-tag size="small" :type="row.bottleneck ? 'danger' : 'info'">{{ row.count }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-table v-if="quoteResult && quoteResult.price.breakdown.length" :data="quoteResult.price.breakdown" size="small" border style="margin-top:12px">
          <el-table-column prop="resource_kind" label="升级资源" />
          <el-table-column prop="upgrade_qty" label="升级数量" />
          <el-table-column prop="monthly_delta" label="月租增量" />
          <el-table-column prop="reputation_delta" label="信誉增量" />
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="发布日志" name="logs">
        <el-timeline>
          <el-timeline-item v-for="log in publishLogs" :key="log.id" :timestamp="log.created_at" placement="top">
            [{{ log.action_label }}] {{ log.from_status }} → {{ log.to_status }} {{ log.message }} <span class="muted">{{ log.operator_name }}</span>
          </el-timeline-item>
        </el-timeline>
      </el-tab-pane>
    </el-tabs>

    <!-- 阶梯规则编辑 -->
    <el-dialog v-model="ruleVisible" :title="ruleEdit ? '编辑规则' : '新增规则'" width="500px" destroy-on-close>
      <el-form ref="ruleRef" :model="ruleForm" :rules="ruleRules" label-width="110px">
        <el-form-item label="资源类型" prop="resource_kind">
          <el-select v-model="ruleForm.resource_kind" style="width:100%">
            <el-option v-for="r in resourceKinds" :key="r.value" :label="r.label" :value="r.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="区间起(含)" prop="min_qty"><el-input-number v-model="ruleForm.min_qty" :min="0" controls-position="right" style="width:100%" /></el-form-item>
        <el-form-item label="区间止(含)"><el-input-number v-model="ruleForm.max_qty" :min="0" controls-position="right" style="width:100%" placeholder="留空=不封顶" /></el-form-item>
        <el-form-item label="每单位月租"><el-input-number v-model="ruleForm.unit_price" :min="0" :precision="2" controls-position="right" style="width:100%" /></el-form-item>
        <el-form-item label="每单位信誉"><el-input-number v-model="ruleForm.unit_reputation" :min="0" :precision="2" controls-position="right" style="width:100%" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="ruleVisible = false">取消</el-button>
        <el-button type="primary" :loading="ruleSaving" @click="saveRule">确认</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed } from 'vue'
import { gbToMb, mbToGb, ramAmount, ramText } from '@/utils/ram'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, ArrowLeft } from '@element-plus/icons-vue'
import {
  getProduct, quoteProduct, getProductPublishLogs,
  createUpgradeRule, updateUpgradeRule, deleteUpgradeRule,
} from '@/api/product'

const route = useRoute()
const router = useRouter()
const productId = computed(() => route.params.id)

const loading = ref(false)
const activeTab = ref('overview')
const current = ref(null)
const capacity = ref(null)
const stock = ref(null)
const publishLogs = ref([])

function goBack() {
  router.push('/product/products')
}
function goEdit() {
  if (current.value) router.push({ path: '/product/products', query: { edit: current.value.id } })
}

async function load() {
  if (!productId.value) return
  loading.value = true
  try {
    const detail = await getProduct(productId.value)
    current.value = detail
    capacity.value = detail.capacity
    stock.value = detail.stock
    publishLogs.value = await getProductPublishLogs(productId.value).catch(() => [])
  } catch (e) {
    ElMessage.error('加载失败：' + (e?.response?.data?.detail || e.message))
  } finally { loading.value = false }
}

// ── 常量与辅助 ─────────────────────────────────────────────
const statusColor = { draft: 'info', online: 'success', offline: 'warning', archived: '' }
const resourceKinds = [
  { value: 'cpu', label: 'CPU 核' }, { value: 'ram', label: '内存(GB)' }, { value: 'disk', label: '硬盘(GB)' },
  { value: 'bandwidth', label: '带宽(Mbps)' }, { value: 'ip', label: 'IP 数量' }, { value: 'nic', label: '网卡数量' },
]
function ipAttrLabel(v) { return ({ native: '原生', broadcast: '广播' })[v] || v }
function productNetworkTypes(row) {
  if (Array.isArray(row?.network_types) && row.network_types.length) return row.network_types
  if (row?.network_type) {
    return [{ value: row.network_type, label: row.network_type_label || row.network_type }]
  }
  return []
}
function productAsnNatures(row) {
  if (Array.isArray(row?.asn_natures) && row.asn_natures.length) return row.asn_natures
  if (row?.asn_nature) {
    return [{ value: row.asn_nature, label: row.asn_nature_label || row.asn_nature }]
  }
  return []
}
function accelLabel(v) {
  const map = { gre: 'GRE', wireguard: 'WireGuard' }
  const items = Array.isArray(v) ? v : String(v || '').split(/[,;/]/)
  const types = []
  for (const item of items) {
    let value = String(item || '').trim().toLowerCase()
    if (value === 'wg') value = 'wireguard'
    if ((value === 'gre' || value === 'wireguard') && !types.includes(value)) types.push(value)
  }
  return types.length ? types.map((t) => map[t]).join(' / ') : '-'
}
function usageColor(pct) { return pct >= 90 ? '#f56c6c' : pct >= 70 ? '#e6a23c' : '#67c23a' }

const RES_ORDER = ['cpu_cores', 'ram_mb', 'disk_gb', 'bandwidth_mbps', 'ip_count']
function capacityRows(detail) {
  return RES_ORDER.filter((k) => detail[k]).map((k) => ({
    key: k,
    ...detail[k],
    need: ramAmount(k, detail[k].need),
    total: ramAmount(k, detail[k].total),
    used: ramAmount(k, detail[k].used),
    available: ramAmount(k, detail[k].available),
  }))
}
function stockRows(byRes) {
  const min = Object.values(byRes).filter((v) => v).reduce((m, v) => Math.min(m, v.count), Infinity)
  return RES_ORDER.filter((k) => byRes[k]).map((k) => ({
    label: ({ cpu_cores: 'CPU 核', ram_mb: '内存(GB)', disk_gb: '硬盘(GB)', bandwidth_mbps: '带宽(Mbps)', ip_count: 'IP 数量' })[k],
    ...byRes[k],
    need: ramAmount(k, byRes[k].need),
    available: ramAmount(k, byRes[k].available),
    bottleneck: byRes[k].count === min,
  }))
}

// ── 报价 ──────────────────────────────────────────────────
const quoting = ref(false)
const quoteResult = ref(null)
const quoteForm = reactive({ cpu_cores: 1, ram_mb: 1024, disk_gb: 40, bandwidth_mbps: 5, ip_count: 1 })
const quoteRamGb = computed({
  get: () => mbToGb(quoteForm.ram_mb),
  set: (gb) => { quoteForm.ram_mb = gbToMb(gb) },
})

async function doQuote() {
  quoting.value = true
  try {
    quoteResult.value = await quoteProduct(current.value.id, quoteForm)
  } finally { quoting.value = false }
}

// ── 阶梯规则 ──────────────────────────────────────────────
const ruleVisible = ref(false)
const ruleSaving = ref(false)
const ruleRef = ref(null)
const ruleEdit = ref(null)
const ruleForm = reactive({ resource_kind: 'cpu', min_qty: 1, max_qty: null, unit_price: 0, unit_reputation: 0 })
const ruleRules = { resource_kind: [{ required: true, message: '请选择资源类型' }] }

function openRule(row = null) {
  ruleEdit.value = row
  if (row) {
    Object.assign(ruleForm, {
      resource_kind: row.resource_kind, min_qty: row.min_qty, max_qty: row.max_qty,
      unit_price: parseFloat(row.unit_price) || 0, unit_reputation: parseFloat(row.unit_reputation) || 0,
    })
  } else {
    Object.assign(ruleForm, { resource_kind: 'cpu', min_qty: 1, max_qty: null, unit_price: 0, unit_reputation: 0 })
  }
  ruleVisible.value = true
}

async function saveRule() {
  const valid = await ruleRef.value.validate().catch(() => false)
  if (!valid) return
  ruleSaving.value = true
  try {
    const payload = { ...ruleForm, product: current.value.id }
    if (ruleEdit.value) { await updateUpgradeRule(ruleEdit.value.id, payload); ElMessage.success('已更新') }
    else { await createUpgradeRule(payload); ElMessage.success('已新增') }
    ruleVisible.value = false
    current.value = await getProduct(current.value.id)
  } finally { ruleSaving.value = false }
}

async function removeRule(row) {
  await ElMessageBox.confirm('确定删除该阶梯规则？', '警告', { type: 'warning' })
  await deleteUpgradeRule(row.id)
  ElMessage.success('已删除')
  current.value = await getProduct(current.value.id)
}

onMounted(load)
</script>

<style scoped>
.product-detail-page { background: #fff; border-radius: 8px; padding: 16px 20px 24px; min-height: calc(100vh - 120px); }
.page-header { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; padding-bottom: 14px; border-bottom: 1px solid #ebeef5; }
.ph-name { font-size: 18px; font-weight: 700; color: #1a1a2e; }
.ph-no { font-size: 12px; color: #909399; }
.ph-actions { margin-left: auto; display: flex; gap: 8px; }
.muted { color: #909399; font-size: 12px; }
.cap-row { display: flex; gap: 12px; margin-top: 12px; }
.cap-card { flex: 1; }
.cap-title { font-weight: 600; color: #606266; margin-bottom: 8px; }
.shortage { color: #f56c6c; margin: 8px 0 0; padding-left: 18px; }
.stock-num { font-size: 28px; font-weight: 700; color: #409EFF; }
.rules-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.quote-form { display: flex; flex-wrap: wrap; }
.quote-form :deep(.el-form-item) { margin-right: 16px; }
</style>
