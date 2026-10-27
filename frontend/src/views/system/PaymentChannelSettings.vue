<template>
  <div class="payment-settings" v-loading="loading">
    <div class="toolbar">
      <el-alert
        title="支付密钥仅可写入，不会在页面或 API 中回显。生产环境请设置 PAYMENT_CONFIG_ENCRYPTION_KEY 与 PAYMENT_FRONTEND_URL。"
        type="info"
        :closable="false"
        show-icon
      />
      <el-button v-if="canManage" type="primary" :icon="Plus" @click="openCreate">新增支付渠道</el-button>
    </div>

    <el-empty v-if="!channels.length && !loading" description="尚未配置在线支付渠道" />
    <el-table v-else :data="channels" border stripe>
      <el-table-column prop="name" label="显示名称" min-width="150" />
      <el-table-column label="支付方式" min-width="145">
        <template #default="{ row }">{{ row.provider_label }}</template>
      </el-table-column>
      <el-table-column label="运行环境" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="row.sandbox ? 'warning' : 'success'">
            {{ row.sandbox ? '沙箱' : '生产' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="允许币种" min-width="135">
        <template #default="{ row }">
          <span v-if="row.allowed_currencies?.length">{{ row.allowed_currencies.join('、') }}</span>
          <span v-else class="muted">不限</span>
        </template>
      </el-table-column>
      <el-table-column label="配置状态" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="row.configured ? 'success' : 'info'">
            {{ row.configured ? '已完整配置' : '草稿' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="启用" width="80">
        <template #default="{ row }">
          <el-tag size="small" :type="row.enabled ? 'success' : 'info'">
            {{ row.enabled ? '已启用' : '未启用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="支付回调 URL" min-width="270">
        <template #default="{ row }">
          <code class="callback-url">{{ callbackUrl(row) }}</code>
          <el-button link type="primary" size="small" @click="copyCallbackUrl(row)">复制</el-button>
        </template>
      </el-table-column>
      <el-table-column label="更新时间" prop="updated_at" width="180" />
      <el-table-column v-if="canManage" label="操作" width="140" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
          <el-button link type="danger" size="small" @click="removeChannel(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog
      v-model="dialogVisible"
      :title="editing ? '编辑支付渠道' : '新增支付渠道'"
      width="720px"
      destroy-on-close
    >
      <el-form ref="formRef" :model="form" :rules="rules" label-width="145px">
        <el-form-item label="支付服务商" prop="provider">
          <el-select v-model="form.provider" :disabled="Boolean(editing)" style="width:100%" @change="changeProvider">
            <el-option
              v-for="item in providers"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
          <div class="field-help">{{ selectedProvider?.description || '选择要对接的支付服务商' }}</div>
        </el-form-item>
        <el-form-item label="显示名称" prop="name">
          <el-input v-model="form.name" maxlength="64" placeholder="例如：Stripe 国际信用卡" />
        </el-form-item>
        <el-form-item label="启用线上支付">
          <el-switch v-model="form.enabled" />
          <span class="inline-help">启用后，该渠道才会出现在账单在线支付选项中。</span>
        </el-form-item>
        <el-form-item label="沙箱模式">
          <el-switch v-model="form.sandbox" />
          <span class="inline-help">正式收款前请先使用沙箱测试。</span>
        </el-form-item>
        <el-form-item label="允许支付币种">
          <el-select
            v-model="form.allowed_currencies"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="留空表示不限制"
            style="width:100%"
          >
            <el-option v-for="currency in currencyOptions" :key="currency" :label="currency" :value="currency" />
          </el-select>
        </el-form-item>
        <el-form-item label="渠道收款币种">
          <el-select
            v-model="form.settlement_currency"
            clearable
            filterable
            allow-create
            default-first-option
            placeholder="留空时自动选择"
            style="width:100%"
          >
            <el-option
              v-for="currency in form.allowed_currencies"
              :key="currency"
              :label="currency"
              :value="currency"
            />
          </el-select>
          <div class="field-help">账单币种不同时，系统按汇率换算后使用该币种向网关下单。</div>
        </el-form-item>
        <el-divider content-position="left">接口凭据</el-divider>
        <el-alert
          v-if="form.provider === 'paypal'"
          :title="`PayPal ${form.sandbox ? 'Sandbox' : 'Live'} 配置`"
          :description="paypalSetupHint"
          type="warning"
          :closable="false"
          show-icon
          style="margin-bottom:16px"
        />
        <el-form-item v-if="editing" label="支付回调 URL">
          <div class="callback-row">
            <code class="callback-url">{{ callbackUrl(editing) }}</code>
            <el-button link type="primary" size="small" @click="copyCallbackUrl(editing)">复制</el-button>
          </div>
          <div class="field-help">{{ callbackHelp }}</div>
        </el-form-item>
        <el-alert
          v-else
          title="保存渠道后，这里会给出支付回调 URL。请把该地址配置到支付平台；支付成功后平台会通知本系统，确认到账并继续交付。"
          type="info"
          :closable="false"
          show-icon
          style="margin-bottom:16px"
        />
        <el-form-item
          v-for="field in selectedProvider?.fields || []"
          :key="field.key"
          :label="field.label"
          :required="field.required"
        >
          <el-input
            v-if="field.multiline"
            v-model="form.config[field.key]"
            type="textarea"
            :rows="4"
            :placeholder="fieldPlaceholder(field)"
            :show-password="field.secret"
          />
          <el-input
            v-else
            v-model="form.config[field.key]"
            :type="field.secret ? 'password' : 'text'"
            :show-password="field.secret"
            :placeholder="fieldPlaceholder(field)"
          />
          <div v-if="field.secret && editing && editing.config_view?.[`${field.key}_configured`]" class="field-help">
            已保存；留空表示保持原值。
          </div>
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="form.sort_order" :min="0" controls-position="right" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" maxlength="500" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveChannel">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import {
  createPaymentChannel,
  deletePaymentChannel,
  getPaymentChannels,
  getPaymentProviderSchemas,
  updatePaymentChannel,
} from '@/api/system'

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('system.settings.manage') || authStore.user?.is_superuser)
const loading = ref(false)
const saving = ref(false)
const channels = ref([])
const providers = ref([])
const dialogVisible = ref(false)
const editing = ref(null)
const formRef = ref(null)
const currencyOptions = ['CNY', 'USD', 'EUR', 'GBP', 'HKD', 'JPY', 'AUD', 'SGD']
const form = reactive({
  provider: '',
  name: '',
  enabled: false,
  sandbox: true,
  allowed_currencies: [],
  settlement_currency: '',
  sort_order: 0,
  remark: '',
  config: {},
})

const rules = {
  provider: [{ required: true, message: '请选择支付服务商', trigger: 'change' }],
  name: [{ required: true, message: '请输入显示名称', trigger: 'blur' }],
}

const selectedProvider = computed(() => providers.value.find(item => item.value === form.provider))
const paypalSetupHint = computed(() => {
  const events = selectedProvider.value?.recommended_events || []
  return `Client ID、Secret 与 Webhook ID 必须来自同一 ${form.sandbox ? 'Sandbox' : 'Live'} 应用。建议订阅：${events.join('、')}。`
})
const callbackHelp = computed(() => (
  selectedProvider.value?.callback_usage
  || editing.value?.callback_usage
  || '把该地址配置到支付平台。支付成功后平台通知此地址，系统验签确认到账并自动交付。该地址必须能从公网访问。'
))

function resetForm() {
  Object.assign(form, {
    provider: providers.value[0]?.value || '',
    name: '',
    enabled: false,
    sandbox: true,
    allowed_currencies: [],
    settlement_currency: '',
    sort_order: 0,
    remark: '',
    config: {},
  })
  changeProvider()
}

function changeProvider() {
  const fields = selectedProvider.value?.fields || []
  const next = {}
  fields.forEach((field) => { next[field.key] = form.config?.[field.key] || '' })
  form.config = next
  if (!editing.value && !form.name && selectedProvider.value) form.name = selectedProvider.value.label
}

function fieldPlaceholder(field) {
  if (field.secret && editing.value?.config_view?.[`${field.key}_configured`]) return '已配置，留空不修改'
  return field.secret ? `请输入 ${field.label}` : `请输入 ${field.label}`
}

async function loadData() {
  loading.value = true
  try {
    const [channelRes, providerRes] = await Promise.all([
      getPaymentChannels({ page_size: 100 }),
      getPaymentProviderSchemas(),
    ])
    channels.value = channelRes?.results || channelRes || []
    providers.value = providerRes || []
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '加载支付渠道失败')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  resetForm()
  dialogVisible.value = true
}

function openEdit(row) {
  editing.value = row
  Object.assign(form, {
    provider: row.provider,
    name: row.name,
    enabled: Boolean(row.enabled),
    sandbox: Boolean(row.sandbox),
    allowed_currencies: [...(row.allowed_currencies || [])],
    settlement_currency: row.settlement_currency || '',
    sort_order: row.sort_order || 0,
    remark: row.remark || '',
    config: { ...(row.config_view || {}) },
  })
  changeProvider()
  dialogVisible.value = true
}

function formPayload() {
  const config = {}
  const fields = selectedProvider.value?.fields || []
  fields.forEach((field) => {
    const value = form.config?.[field.key]
    if (value !== undefined && value !== null) config[field.key] = String(value)
  })
  return {
    provider: form.provider,
    name: form.name.trim(),
    enabled: Boolean(form.enabled),
    sandbox: Boolean(form.sandbox),
    allowed_currencies: form.allowed_currencies.map(value => String(value).toUpperCase()),
    settlement_currency: String(form.settlement_currency || '').toUpperCase(),
    sort_order: Number(form.sort_order || 0),
    remark: form.remark,
    config,
  }
}

async function saveChannel() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    const payload = formPayload()
    if (editing.value) await updatePaymentChannel(editing.value.id, payload)
    else await createPaymentChannel(payload)
    ElMessage.success('支付渠道已保存')
    dialogVisible.value = false
    await loadData()
  } catch (error) {
    const detail = error?.response?.data?.config?.[0]
      || error?.response?.data?.detail
      || '保存支付渠道失败'
    ElMessage.error(detail)
  } finally {
    saving.value = false
  }
}

async function removeChannel(row) {
  try {
    await ElMessageBox.confirm(
      `确认删除支付渠道「${row.name}」吗？历史支付交易不会删除。`,
      '删除支付渠道',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await deletePaymentChannel(row.id)
    ElMessage.success('支付渠道已删除')
    await loadData()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '删除失败')
  }
}

function callbackUrl(row) {
  return row?.callback_url || ''
}

async function copyCallbackUrl(row) {
  try {
    await navigator.clipboard.writeText(callbackUrl(row))
    ElMessage.success('异步回调地址已复制')
  } catch {
    ElMessage.warning('复制失败，请手动复制回调地址')
  }
}

onMounted(loadData)
</script>

<style scoped>
.payment-settings { padding-top: 8px; }
.toolbar { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 16px; }
.toolbar .el-alert { flex: 1; }
.muted, .field-help, .inline-help { color: #909399; font-size: 12px; }
.field-help { margin-top: 6px; line-height: 1.5; }
.inline-help { margin-left: 10px; }
.callback-row { display: flex; align-items: flex-start; gap: 8px; width: 100%; }
.callback-url { color: #606266; font-size: 12px; word-break: break-all; line-height: 1.5; }
@media (max-width: 720px) {
  .toolbar { align-items: stretch; flex-direction: column; }
}
</style>
