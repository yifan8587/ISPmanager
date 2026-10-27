<template>
  <div class="page-container">
    <div class="page-header">
      <h2 class="page-title">
        系统设置
        <el-tag v-if="overview?.version_display" type="primary" effect="plain" size="small" class="ver-tag">
          {{ overview.version_display }}
        </el-tag>
      </h2>
      <el-button
        v-if="activeTab !== '__base_data__' && activeTab !== '__payment_channels__'"
        type="primary"
        :loading="saving"
        :disabled="!hasEditableChanges || !dirty"
        @click="handleSave"
      >
        保存更改
      </el-button>
    </div>

    <!-- 概览 -->
    <el-row :gutter="12" class="stat-row" v-if="overview">
      <el-col :xs="12" :sm="6">
        <div class="stat-card"><span class="stat-num">{{ overview.setting_count }}</span><span class="stat-lbl">配置项</span></div>
      </el-col>
      <el-col :xs="12" :sm="6">
        <div class="stat-card"><span class="stat-num">{{ overview.active_user_count }}</span><span class="stat-lbl">活跃用户</span></div>
      </el-col>
      <el-col :xs="12" :sm="6">
        <div class="stat-card"><span class="stat-num">{{ overview.customer_count }}</span><span class="stat-lbl">客户总数</span></div>
      </el-col>
      <el-col :xs="12" :sm="6">
        <div class="stat-card"><span class="stat-num">{{ systemAccounts }}</span><span class="stat-lbl">系统账号</span></div>
      </el-col>
    </el-row>

    <el-card shadow="never" v-loading="loading">
      <el-tabs v-model="activeTab">
        <el-tab-pane
          v-for="group in groups"
          :key="group.category"
          :label="group.category_label"
          :name="group.category"
        >
          <el-form label-width="200px" class="setting-form">
            <template v-for="item in visibleGroupItems(group)" :key="item.key">
              <el-form-item v-if="item.key === 'default_currency'" :label="item.label">
                <div class="field-wrap">
                  <el-radio-group
                    v-model="formValues[item.key]"
                    :disabled="!canEditItem(item)"
                    class="currency-radios"
                    @change="onSettlementCurrencyChange"
                  >
                    <el-radio
                      v-for="opt in (item.options || [])"
                      :key="opt.value"
                      :value="opt.value"
                    >{{ opt.label }}</el-radio>
                  </el-radio-group>
                  <div class="field-desc">确认后，账单、钱包、渠道财务汇总都按该币种统计和折算。</div>
                  <div class="field-meta"><el-tag size="small" type="info">{{ item.key }}</el-tag></div>
                </div>
              </el-form-item>
              <el-form-item v-else-if="isRateSetting(item.key)" :label="rateFieldLabel(item.key)">
                <div class="field-wrap">
                  <el-input-number
                    v-model="displayRates[rateCodeOf(item.key)]"
                    :disabled="!canEditItem(item)"
                    :precision="6"
                    :min="0"
                    :step="0.01"
                    controls-position="right"
                    style="width:240px"
                  />
                  <div class="field-desc">{{ rateFieldHint(item.key) }}</div>
                </div>
              </el-form-item>
              <el-form-item v-else-if="item.key === '__settlement_cny_rate__'" :label="`人民币汇率 (1 CNY = ? ${settlementCurrency})`">
                <div class="field-wrap">
                  <el-input-number
                    v-model="displayRates.CNY"
                    :precision="6"
                    :min="0"
                    :step="0.0001"
                    controls-position="right"
                    style="width:240px"
                  />
                  <div class="field-desc">用于把人民币折算到当前结算币种；保存时会回写库内 CNY 基准汇率。</div>
                </div>
              </el-form-item>
              <el-form-item v-else :label="item.label">
                <div class="field-wrap">
                  <template v-if="item.options?.length">
                    <el-select
                      v-model="formValues[item.key]"
                      :disabled="!canEditItem(item)"
                      style="width:280px"
                    >
                      <el-option v-for="opt in item.options" :key="opt.value" :label="opt.label" :value="opt.value" />
                    </el-select>
                  </template>
                  <template v-else-if="item.value_type === 'boolean'">
                    <el-switch
                      v-model="formValues[item.key]"
                      :disabled="!canEditItem(item)"
                      active-value="true"
                      inactive-value="false"
                    />
                  </template>
                  <template v-else-if="item.value_type === 'integer'">
                    <el-input-number
                      v-model="formValues[item.key]"
                      :disabled="!canEditItem(item)"
                      controls-position="right"
                      style="width:220px"
                    />
                  </template>
                  <template v-else-if="item.value_type === 'decimal'">
                    <el-input-number
                      v-model="formValues[item.key]"
                      :disabled="!canEditItem(item)"
                      :precision="2"
                      controls-position="right"
                      style="width:220px"
                    />
                  </template>
                  <template v-else-if="item.value_type === 'text'">
                    <el-input
                      v-model="formValues[item.key]"
                      type="textarea"
                      :rows="3"
                      :disabled="!canEditItem(item)"
                      style="max-width:520px"
                    />
                  </template>
                  <template v-else-if="item.secret">
                    <el-input
                      v-model="formValues[item.key]"
                      type="password"
                      show-password
                      :disabled="!canEditItem(item)"
                      :placeholder="item.configured ? '已配置，留空保存则不修改' : '输入密码或授权码'"
                      autocomplete="new-password"
                      style="max-width:420px"
                    />
                  </template>
                  <template v-else>
                    <el-input
                      v-model="formValues[item.key]"
                      :disabled="!canEditItem(item)"
                      style="max-width:420px"
                    />
                  </template>
                  <div v-if="item.description" class="field-desc">{{ item.description }}</div>
                  <div class="field-meta">
                    <el-tag size="small" type="info">{{ item.key }}</el-tag>
                    <el-tag v-if="item.is_public" size="small" type="success" style="margin-left:6px">公开</el-tag>
                    <el-tag v-if="!item.is_editable" size="small" type="warning" style="margin-left:6px">系统只读</el-tag>
                    <el-tag v-else-if="!canEditSetting(item.key)" size="small" type="danger" style="margin-left:6px">无修改权限</el-tag>
                  </div>
                </div>
              </el-form-item>
            </template>
          </el-form>
        </el-tab-pane>

        <!-- 系统数据库基础设置：地理位置（大区/国家/城市）与运营商 -->
        <el-tab-pane label="基础数据库设置" name="__base_data__">
          <BaseDataSettings />
        </el-tab-pane>

        <el-tab-pane label="在线支付" name="__payment_channels__">
          <PaymentChannelSettings />
        </el-tab-pane>
      </el-tabs>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { usePermission } from '@/composables/usePermission'
import { getSystemOverview, getSystemSettingsGrouped, bulkUpdateSystemSettings } from '@/api/system'
import BaseDataSettings from './BaseDataSettings.vue'
import PaymentChannelSettings from './PaymentChannelSettings.vue'

const authStore = useAuthStore()
const { canEditSetting } = usePermission()
const loading = ref(false)
const saving = ref(false)
const groups = ref([])
const overview = ref(null)
const activeTab = ref('general')
const formValues = reactive({})
const originalValues = reactive({})

const canManage = computed(() => authStore.hasPermission('system.settings.manage') || authStore.user?.is_superuser)

function canEditItem(item) {
  return item.is_editable && canEditSetting(item.key)
}

const hasEditableChanges = computed(() => {
  const settingChanged = Object.keys(formValues).some((k) => {
    if (String(formValues[k] ?? '') === String(originalValues[k] ?? '')) return false
    const item = findSettingItem(k)
    return item && canEditItem(item)
  })
  return settingChanged || ratesDirty()
})

function findSettingItem(key) {
  for (const group of groups.value) {
    const item = group.items?.find((i) => i.key === key)
    if (item) return item
  }
  return null
}
const systemAccounts = computed(() => {
  const types = overview.value?.account_types || []
  return types.find(t => t.value === 'system')?.count ?? '-'
})
const RATE_SETTING_KEYS = {
  exchange_rate_usd_cny: 'USD',
  exchange_rate_eur_cny: 'EUR',
  exchange_rate_gbp_cny: 'GBP',
  exchange_rate_hkd_cny: 'HKD',
  exchange_rate_jpy_cny: 'JPY',
  exchange_rate_aud_cny: 'AUD',
  exchange_rate_sgd_cny: 'SGD',
  credit_rate_cny: 'CREDIT',
}
const RATE_LABELS = {
  USD: '美元', EUR: '欧元', GBP: '英镑', HKD: '港币', JPY: '日元',
  AUD: '澳元', SGD: '新加坡元', CREDIT: '信誉额', CNY: '人民币',
}
const displayRates = reactive({})
const originalDisplayRates = reactive({})
const settlementCurrency = computed(() => String(formValues.default_currency || 'USD').toUpperCase())

function isRateSetting(key) {
  return Boolean(RATE_SETTING_KEYS[key])
}
function rateCodeOf(key) {
  return RATE_SETTING_KEYS[key]
}
function toCnyTable() {
  return {
    CNY: 1,
    USD: Number(formValues.exchange_rate_usd_cny) || 0,
    EUR: Number(formValues.exchange_rate_eur_cny) || 0,
    GBP: Number(formValues.exchange_rate_gbp_cny) || 0,
    HKD: Number(formValues.exchange_rate_hkd_cny) || 0,
    JPY: Number(formValues.exchange_rate_jpy_cny) || 0,
    AUD: Number(formValues.exchange_rate_aud_cny) || 0,
    SGD: Number(formValues.exchange_rate_sgd_cny) || 0,
    CREDIT: Number(formValues.credit_rate_cny) || 0,
  }
}
function snapshotDisplayRates() {
  Object.keys(originalDisplayRates).forEach((key) => delete originalDisplayRates[key])
  Object.assign(originalDisplayRates, { ...displayRates })
}
function rebuildDisplayRates() {
  const settlement = settlementCurrency.value
  const toCny = toCnyTable()
  const dest = Number(toCny[settlement]) || 0
  Object.keys(displayRates).forEach((key) => delete displayRates[key])
  Object.entries(toCny).forEach(([code, from]) => {
    if (code === settlement) displayRates[code] = 1
    else if (from > 0 && dest > 0) displayRates[code] = Number((from / dest).toFixed(8))
    else displayRates[code] = 0
  })
}
function applyDisplayRatesToForm() {
  const settlement = settlementCurrency.value
  const shown = { ...displayRates }
  if (settlement === 'CNY') {
    Object.entries(RATE_SETTING_KEYS).forEach(([key, code]) => {
      formValues[key] = shown[code] ?? 0
    })
    return
  }
  const cnyShown = Number(shown.CNY) || 0
  const cnyPerSettlement = cnyShown > 0 ? 1 / cnyShown : 0
  Object.entries(RATE_SETTING_KEYS).forEach(([key, code]) => {
    if (code === settlement) {
      formValues[key] = cnyPerSettlement
    } else {
      const amount = Number(shown[code]) || 0
      formValues[key] = amount > 0 && cnyPerSettlement > 0 ? amount * cnyPerSettlement : 0
    }
  })
}
function onSettlementCurrencyChange() {
  rebuildDisplayRates()
}
function rateFieldLabel(key) {
  const code = rateCodeOf(key)
  return `${RATE_LABELS[code] || code}汇率 (1 ${code} = ? ${settlementCurrency.value})`
}
function rateFieldHint(key) {
  const code = rateCodeOf(key)
  if (code === settlementCurrency.value) return '当前结算币种，固定为 1，无需填写'
  return `填写 1 ${code} 可兑换多少 ${settlementCurrency.value}。保存后财务按 ${settlementCurrency.value} 汇总。`
}
function visibleGroupItems(group) {
  const items = group.items || []
  if (group.category !== 'billing') return items
  const filtered = items.filter((item) => {
    if (!isRateSetting(item.key)) return true
    return rateCodeOf(item.key) !== settlementCurrency.value
  })
  if (settlementCurrency.value !== 'CNY') {
    const currencyItem = items.find((item) => item.key === 'default_currency')
    const insertAt = currencyItem ? filtered.indexOf(currencyItem) + 1 : 1
    filtered.splice(insertAt, 0, {
      key: '__settlement_cny_rate__',
      label: '人民币汇率',
      value_type: 'decimal',
      is_editable: true,
    })
  }
  return filtered
}

function ratesDirty() {
  return Object.keys({ ...originalDisplayRates, ...displayRates }).some(
    (code) => Number(displayRates[code] || 0) !== Number(originalDisplayRates[code] || 0),
  )
}

const dirty = computed(() =>
  Object.keys(formValues).some(k => String(formValues[k] ?? '') !== String(originalValues[k] ?? ''))
  || ratesDirty()
)

function initFormValues(data) {
  groups.value = data || []
  for (const group of groups.value) {
    for (const item of group.items) {
      const val = normalizeValue(item)
      formValues[item.key] = val
      originalValues[item.key] = val
    }
  }
  if (groups.value.length && !groups.value.find(g => g.category === activeTab.value)) {
    activeTab.value = groups.value[0].category
  }
  rebuildDisplayRates()
  snapshotDisplayRates()
}

function normalizeValue(item) {
  const raw = item.value !== '' && item.value != null ? item.value : item.default_value
  if (item.value_type === 'boolean') return String(raw).toLowerCase() === 'true' ? 'true' : 'false'
  if (item.value_type === 'integer') return raw === '' ? 0 : Number(raw)
  if (item.value_type === 'decimal') return raw === '' ? 0 : Number(raw)
  return raw ?? ''
}

async function loadData() {
  loading.value = true
  try {
    const [overviewRes, groupedRes] = await Promise.all([
      getSystemOverview().catch(() => null),
      getSystemSettingsGrouped(),
    ])
    overview.value = overviewRes ?? null
    initFormValues(Array.isArray(groupedRes) ? groupedRes : [])
  } finally {
    loading.value = false
  }
}

async function handleSave() {
  applyDisplayRatesToForm()
  const items = Object.keys(formValues)
    .filter((k) => String(formValues[k] ?? '') !== String(originalValues[k] ?? ''))
    .map((k) => ({ key: k, value: String(formValues[k] ?? '') }))
    .filter(({ key }) => {
      const item = findSettingItem(key)
      return item && canEditItem(item)
    })
  if (!items.length) return
  saving.value = true
  try {
    await bulkUpdateSystemSettings(items)
    ElMessage.success('系统设置已保存')
    await loadData()
  } catch (err) {
    ElMessage.error(err?.response?.data?.message || err?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(loadData)
</script>

<style scoped>
.page-container { min-height: 100%; }
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.page-title { font-size: 18px; font-weight: 700; color: #1a1a2e; margin: 0; display: flex; align-items: center; gap: 8px; }
.ver-tag { font-weight: 600; }
.stat-row { margin-bottom: 16px; }
.stat-card {
  background: #f8f9ff; border: 1px solid #ebeef5; border-radius: 8px;
  padding: 14px 16px; text-align: center;
}
.stat-num { display: block; font-size: 22px; font-weight: 700; color: #303133; }
.stat-lbl { display: block; font-size: 12px; color: #909399; margin-top: 4px; }
.setting-form { max-width: 760px; padding-top: 8px; }
.field-wrap { width: 100%; }
.field-desc { font-size: 12px; color: #909399; margin-top: 6px; line-height: 1.5; }
.field-meta { margin-top: 8px; }
.currency-radios { display: flex; flex-wrap: wrap; gap: 6px 16px; max-width: 640px; }
</style>
