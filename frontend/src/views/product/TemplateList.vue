<template>
  <div class="page-container">
    <div class="page-header">
      <h2 class="page-title">产品模版</h2>
      <div class="page-header-actions">
        <ExcelImportExport
          export-url="/api/product/templates/export/"
          template-url="/api/product/templates/import-template/"
          import-url="/api/product/templates/import/"
          filename="产品模版.xlsx"
          hint="表格中的模版编号会原样保存；系统里已有该编号则覆盖更新，不会另建一条。"
          :params="excelParams"
          @imported="loadData"
        />
        <el-button type="primary" :icon="Plus" @click="openDialog()">新建模版</el-button>
      </div>
    </div>

    <el-card shadow="never" class="search-card">
      <el-form :model="searchForm" inline>
        <el-form-item label="类别">
          <el-radio-group v-model="searchForm.category" @change="loadData">
            <el-radio-button label="">全部</el-radio-button>
            <el-radio-button label="vps">VPS</el-radio-button>
            <el-radio-button label="edge">边缘加速</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="关键词">
          <el-input v-model="searchForm.search" placeholder="名称/编号" clearable style="width:180px" @keyup.enter="loadData" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="loadData">搜索</el-button>
          <el-button @click="resetSearch">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" style="margin-top:12px;">
      <el-table :data="tableData" v-loading="loading" stripe border row-key="id">
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="pricing-panel">
              <div class="pricing-head">
                <span>模版定价（按 POP 点：月租 / 信誉值）</span>
                <el-button size="small" type="primary" :icon="Plus" @click="openPricing(row)">新增定价</el-button>
              </div>
              <el-table :data="row.pricings || []" size="small" border>
                <el-table-column label="POP" min-width="200">
                  <template #default="{ row: pr }">
                    {{ pr.pop_name }} <span class="muted">({{ pr.pop_code }})</span>
                    <el-tag v-if="pr.ip_delivery_mode === 'nat'" size="small" type="warning" style="margin-left:6px">
                      {{ pr.ip_delivery_mode_label || 'NAT 交付' }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="月租价" width="120" align="right">
                  <template #default="{ row: pr }">${{ pr.monthly_price }}</template>
                </el-table-column>
                <el-table-column label="信誉值" width="120" align="right">
                  <template #default="{ row: pr }">{{ pr.reputation_price }}</template>
                </el-table-column>
                <el-table-column label="安装费" width="110" align="right">
                  <template #default="{ row: pr }">${{ pr.setup_price }}</template>
                </el-table-column>
                <el-table-column prop="currency" label="币种" width="80" />
                <el-table-column label="操作" width="130">
                  <template #default="{ row: pr }">
                    <el-button link type="primary" size="small" @click="openPricing(row, pr)">编辑</el-button>
                    <el-button link type="danger" size="small" @click="removePricing(row, pr)">删除</el-button>
                  </template>
                </el-table-column>
                <template #empty>暂无定价，请新增</template>
              </el-table>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="template_no" label="模版编号" width="160" />
        <el-table-column prop="name" label="模版名称" min-width="140" />
        <el-table-column label="类别" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.category === 'vps' ? 'primary' : 'success'">{{ row.category_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="规格" min-width="220">
          <template #default="{ row }">
            <span v-if="row.category === 'vps'">
              {{ row.cpu_cores }}核 / {{ ramText(row.ram_mb) }} / {{ row.disk_gb }}G / {{ row.bandwidth_mbps }}M / {{ row.ip_count }}IP
            </span>
            <span v-else>{{ row.accel_type_label || accelLabel(row.accel_type) }} / {{ row.bandwidth_mbps }}M / {{ row.ip_count }}IP</span>
          </template>
        </el-table-column>
        <el-table-column label="定价POP数" width="100" align="center">
          <template #default="{ row }">{{ (row.pricings || []).length }}</template>
        </el-table-column>
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'" size="small">{{ row.is_active ? '启用' : '停用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDialog(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total"
        layout="total, prev, pager, next" class="pagination" @change="loadData" />
    </el-card>

    <!-- 模版编辑 -->
    <el-dialog v-model="dialogVisible" :title="editRow ? '编辑模版' : '新建模版'" width="760px" destroy-on-close>
      <el-form ref="formRef" :model="form" :rules="formRules" label-width="96px">
        <el-row :gutter="16">
          <el-col :span="12"><el-form-item label="模版名称" prop="name"><el-input v-model="form.name" placeholder="如 VPS 1C1G" /></el-form-item></el-col>
          <el-col :span="12">
            <el-form-item label="类别" prop="category">
              <el-radio-group v-model="form.category" @change="onCategoryChange">
                <el-radio-button label="vps">VPS</el-radio-button>
                <el-radio-button label="edge">边缘加速</el-radio-button>
              </el-radio-group>
            </el-form-item>
          </el-col>
        </el-row>
        <template v-if="form.category === 'vps'">
          <el-divider content-position="left">VPS 规格</el-divider>
          <el-row :gutter="16">
            <el-col :span="8"><el-form-item label="CPU核心"><el-input-number v-model="form.cpu_cores" :min="1" controls-position="right" style="width:100%" /></el-form-item></el-col>
            <el-col :span="8"><el-form-item label="内存(GB)"><el-input-number v-model="ramGb" :min="1" :step="1" controls-position="right" style="width:100%" /></el-form-item></el-col>
            <el-col :span="8"><el-form-item label="硬盘(GB)"><el-input-number v-model="form.disk_gb" :min="1" controls-position="right" style="width:100%" /></el-form-item></el-col>
            <el-col :span="8"><el-form-item label="网卡数"><el-input-number v-model="form.nic_count" :min="1" controls-position="right" style="width:100%" /></el-form-item></el-col>
            <el-col :span="8"><el-form-item label="带宽(Mbps)"><el-input-number v-model="form.bandwidth_mbps" :min="0" controls-position="right" style="width:100%" /></el-form-item></el-col>
            <el-col :span="8"><el-form-item label="IP数量"><el-input-number v-model="form.ip_count" :min="0" controls-position="right" style="width:100%" /></el-form-item></el-col>
          </el-row>
        </template>
        <template v-else>
          <el-divider content-position="left">边缘加速规格</el-divider>
          <el-row :gutter="16">
            <el-col :span="8">
              <el-form-item label="加速类型" prop="accel_types">
                <el-select
                  v-model="form.accel_types"
                  multiple
                  collapse-tags
                  collapse-tags-tooltip
                  placeholder="可多选 GRE / WireGuard"
                  style="width:100%"
                >
                  <el-option v-for="a in accelOptions" :key="a.value" :label="a.label" :value="a.value" />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="8"><el-form-item label="带宽(Mbps)"><el-input-number v-model="form.bandwidth_mbps" :min="0" controls-position="right" style="width:100%" /></el-form-item></el-col>
            <el-col :span="8"><el-form-item label="IP数量"><el-input-number v-model="form.ip_count" :min="0" controls-position="right" style="width:100%" /></el-form-item></el-col>
          </el-row>
        </template>
        <el-form-item label="描述"><el-input v-model="form.description" /></el-form-item>
        <el-form-item label="状态"><el-switch v-model="form.is_active" active-text="启用" inactive-text="停用" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">确认</el-button>
      </template>
    </el-dialog>

    <!-- 定价编辑 -->
    <el-dialog v-model="pricingVisible" :title="pricingEdit ? '编辑定价' : '新增定价'" width="520px" destroy-on-close>
      <el-form ref="pricingRef" :model="pricingForm" :rules="pricingRules" label-width="100px">
        <el-form-item v-if="pricingEdit" label="POP点" prop="pop_point">
          <el-select v-model="pricingForm.pop_point" filterable placeholder="选择POP点" style="width:100%" disabled>
            <el-option
              v-for="p in popOptions"
              :key="p.id"
              :label="p.ip_delivery_mode === 'nat' ? `${p.name}（NAT 交付）` : p.name"
              :value="p.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item v-else label="POP点" prop="pop_points">
          <el-select v-model="pricingForm.pop_points" multiple filterable collapse-tags collapse-tags-tooltip
            placeholder="可一次选择多个POP点" style="width:100%">
            <el-option
              v-for="p in availablePopOptions"
              :key="p.id"
              :label="p.ip_delivery_mode === 'nat' ? `${p.name}（NAT 交付）` : p.name"
              :value="p.id"
            />
          </el-select>
          <div v-if="pricedPopCount" class="muted" style="margin-top:4px">已定价 {{ pricedPopCount }} 个 POP，列表中已自动排除</div>
          <el-alert
            v-if="selectedPricingHasNat"
            type="warning"
            :closable="false"
            show-icon
            style="margin-top:8px"
            title="所选 POP 含 NAT 交付节点"
            description="这些节点的产品上线后，门户选购与调用 IP 时会提示 NAT 交付。"
          />
        </el-form-item>
        <el-form-item label="月租价($)" prop="monthly_price"><el-input-number v-model="pricingForm.monthly_price" :min="0" :precision="2" controls-position="right" style="width:100%" /></el-form-item>
        <el-form-item label="信誉值" prop="reputation_price"><el-input-number v-model="pricingForm.reputation_price" :min="0" :precision="2" controls-position="right" style="width:100%" /></el-form-item>
        <el-form-item label="安装费($)"><el-input-number v-model="pricingForm.setup_price" :min="0" :precision="2" controls-position="right" style="width:100%" /></el-form-item>
        <el-form-item label="币种"><el-input v-model="pricingForm.currency" style="width:160px" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pricingVisible = false">取消</el-button>
        <el-button type="primary" :loading="pricingSaving" @click="savePricing">确认</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { gbToMb, mbToGb, ramText } from '@/utils/ram'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search } from '@element-plus/icons-vue'
import {
  getTemplates, createTemplate, updateTemplate, deleteTemplate,
  createTemplatePricing, updateTemplatePricing, deleteTemplatePricing,
} from '@/api/product'
import { getPopPoints } from '@/api/resource'
import ExcelImportExport from '@/components/ExcelImportExport.vue'

const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const formRef = ref(null)
const editRow = ref(null)
const tableData = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const popOptions = ref([])

const accelOptions = [
  { value: 'gre', label: 'GRE' }, { value: 'wireguard', label: 'WireGuard' },
]
const searchForm = reactive({ category: '', search: '' })
const excelParams = computed(() => {
  const params = {}
  if (searchForm.category) params.category = searchForm.category
  if (searchForm.search) params.search = searchForm.search
  return params
})

const defaultForm = () => ({
  name: '', category: 'vps',
  cpu_cores: 1, ram_mb: 1024, disk_gb: 40, nic_count: 1, bandwidth_mbps: 5, ip_count: 1,
  accel_types: ['gre', 'wireguard'], description: '', is_active: true,
})
const form = reactive(defaultForm())
const formRules = {
  name: [{ required: true, message: '请输入模版名称' }],
  accel_types: [{
    validator: (_rule, value, callback) => {
      if (form.category === 'edge' && !(value || []).length) {
        callback(new Error('请至少选择一种加速类型'))
      } else {
        callback()
      }
    },
    trigger: 'change',
  }],
}

// 定价
const pricingVisible = ref(false)
const pricingSaving = ref(false)
const pricingRef = ref(null)
const pricingEdit = ref(null)
const pricingTemplate = ref(null)
const pricingForm = reactive({
  pop_point: null, pop_points: [],
  monthly_price: 0, reputation_price: 0, setup_price: 0, currency: 'USD',
})
const pricingRules = {
  pop_point: [{ required: true, message: '请选择POP点' }],
  pop_points: [{ type: 'array', required: true, min: 1, message: '请至少选择一个POP点' }],
}

const pricedPopIds = computed(() => {
  const tpl = pricingTemplate.value
  if (!tpl) return new Set()
  return new Set((tpl.pricings || []).map((p) => p.pop_point))
})
const pricedPopCount = computed(() => pricedPopIds.value.size)
const availablePopOptions = computed(() =>
  popOptions.value.filter((p) => !pricedPopIds.value.has(p.id)))
const selectedPricingHasNat = computed(() => {
  const ids = new Set(pricingForm.pop_points || [])
  if (pricingForm.pop_point) ids.add(pricingForm.pop_point)
  return popOptions.value.some((p) => ids.has(p.id) && p.ip_delivery_mode === 'nat')
})

const ramGb = computed({
  get: () => mbToGb(form.ram_mb),
  set: (gb) => { form.ram_mb = gbToMb(gb) },
})
function parseAccelTypes(v) {
  const items = Array.isArray(v) ? v : String(v || '').split(/[,;/]/)
  const out = []
  for (const item of items) {
    let value = String(item || '').trim().toLowerCase()
    if (value === 'wg') value = 'wireguard'
    if ((value === 'gre' || value === 'wireguard') && !out.includes(value)) out.push(value)
  }
  return out
}
function accelLabel(v) {
  const map = { gre: 'GRE', wireguard: 'WireGuard' }
  const types = parseAccelTypes(v)
  return types.length ? types.map((t) => map[t]).join(' / ') : '-'
}
function onCategoryChange() {
  if (form.category === 'edge' && !(form.accel_types || []).length) {
    form.accel_types = ['gre', 'wireguard']
  }
}

async function loadOptions() {
  const pops = await getPopPoints({ page_size: 200 }).catch(() => null)
  popOptions.value = pops?.results || pops || []
}

async function loadData() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (searchForm.category) params.category = searchForm.category
    if (searchForm.search) params.search = searchForm.search
    const res = await getTemplates(params)
    tableData.value = res?.results || []
    total.value = res?.count || 0
  } finally { loading.value = false }
}

function resetSearch() { Object.assign(searchForm, { category: '', search: '' }); loadData() }

function openDialog(row = null) {
  editRow.value = row
  Object.assign(form, defaultForm())
  if (row) {
    Object.assign(form, {
      name: row.name, category: row.category,
      cpu_cores: row.cpu_cores || 1, ram_mb: row.ram_mb || 1024, disk_gb: row.disk_gb || 40,
      nic_count: row.nic_count || 1, bandwidth_mbps: row.bandwidth_mbps || 0, ip_count: row.ip_count || 0,
      accel_types: parseAccelTypes(row.accel_types || row.accel_type), description: row.description || '', is_active: row.is_active,
    })
  }
  dialogVisible.value = true
}

async function handleSave() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    if (editRow.value) { await updateTemplate(editRow.value.id, form); ElMessage.success('已更新') }
    else { await createTemplate(form); ElMessage.success('模版创建成功') }
    dialogVisible.value = false
    loadData()
  } finally { saving.value = false }
}

async function handleDelete(row) {
  await ElMessageBox.confirm(`确定删除模版 "${row.name}"？`, '警告', { type: 'warning' })
  await deleteTemplate(row.id)
  ElMessage.success('已删除')
  loadData()
}

function openPricing(template, pricing = null) {
  pricingTemplate.value = template
  pricingEdit.value = pricing
  if (pricing) {
    Object.assign(pricingForm, {
      pop_point: pricing.pop_point, pop_points: [],
      monthly_price: parseFloat(pricing.monthly_price) || 0,
      reputation_price: parseFloat(pricing.reputation_price) || 0,
      setup_price: parseFloat(pricing.setup_price) || 0,
      currency: pricing.currency || 'USD',
    })
  } else {
    Object.assign(pricingForm, {
      pop_point: null, pop_points: [],
      monthly_price: 0, reputation_price: 0, setup_price: 0, currency: 'USD',
    })
  }
  pricingVisible.value = true
}

async function savePricing() {
  const valid = await pricingRef.value.validate().catch(() => false)
  if (!valid) return
  pricingSaving.value = true
  try {
    if (pricingEdit.value) {
      const payload = {
        pop_point: pricingForm.pop_point,
        monthly_price: pricingForm.monthly_price,
        reputation_price: pricingForm.reputation_price,
        setup_price: pricingForm.setup_price,
        currency: pricingForm.currency,
        template: pricingTemplate.value.id,
      }
      await updateTemplatePricing(pricingEdit.value.id, payload)
      ElMessage.success('已更新')
    } else {
      const existing = pricedPopIds.value
      const toCreate = pricingForm.pop_points.filter((id) => !existing.has(id))
      if (!toCreate.length) {
        ElMessage.warning('所选 POP 均已存在定价')
        return
      }
      await Promise.all(toCreate.map((popId) => createTemplatePricing({
        template: pricingTemplate.value.id,
        pop_point: popId,
        monthly_price: pricingForm.monthly_price,
        reputation_price: pricingForm.reputation_price,
        setup_price: pricingForm.setup_price,
        currency: pricingForm.currency,
      })))
      ElMessage.success(`已为 ${toCreate.length} 个 POP 点新增定价`)
    }
    pricingVisible.value = false
    loadData()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败（部分 POP 可能已存在定价）')
  } finally { pricingSaving.value = false }
}

async function removePricing(template, pricing) {
  await ElMessageBox.confirm(`确定删除 ${pricing.pop_name} 的定价？`, '警告', { type: 'warning' })
  await deleteTemplatePricing(pricing.id)
  ElMessage.success('已删除')
  loadData()
}

onMounted(() => { loadOptions(); loadData() })
</script>

<style scoped>
.page-container { min-height: 100%; }
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.page-header-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.page-title { font-size: 18px; font-weight: 700; color: #1a1a2e; margin: 0; }
.search-card :deep(.el-card__body) { padding: 16px 16px 0; }
.pagination { margin-top: 16px; justify-content: flex-end; }
.pricing-panel { padding: 8px 16px 16px; }
.pricing-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; font-weight: 600; color: #606266; }
.muted { color: #909399; font-size: 12px; }
</style>
