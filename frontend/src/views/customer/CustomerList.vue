<template>
  <div class="page-container">
    <div class="page-header">
      <h2 class="page-title">客户列表</h2>
      <el-button type="primary" :icon="Plus" @click="openDialog()">新增客户</el-button>
    </div>

    <!-- 搜索栏 -->
    <el-card shadow="never" class="search-card">
      <el-form :model="searchForm" inline>
        <el-form-item label="关键词">
          <el-input v-model="searchForm.search" placeholder="客户名/联系人/手机" clearable style="width:200px" @keyup.enter="loadData" />
        </el-form-item>
        <el-form-item label="客户类型">
          <el-select v-model="searchForm.customer_type" clearable placeholder="全部" style="width:120px">
            <el-option v-for="t in customerTypes" :key="t.value" :label="t.label" :value="t.value" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="loadData">搜索</el-button>
          <el-button :icon="RefreshLeft" @click="resetSearch">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 表格 -->
    <el-card shadow="never" style="margin-top: 12px;">
      <el-table :data="tableData" v-loading="loading" stripe border size="default">
        <el-table-column prop="customer_no" label="客户编号" width="150" fixed />
        <el-table-column prop="name" label="客户名称" min-width="150" />
        <el-table-column prop="customer_type" label="类型" width="90">
          <template #default="{ row }">
            <el-tag :type="typeTagMap[row.customer_type]" size="small">
              {{ getTypeLabel(row.customer_type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="contact_name" label="联系人" width="100" />
        <el-table-column prop="contact_phone" label="联系电话" width="130" />
        <el-table-column label="渠道 / 推广信息" min-width="185">
          <template #default="{ row }">
            <template v-if="row.customer_type === 'channel' && row.channel_profile">
              <div>
                <el-tag size="small" type="success">{{ row.channel_profile.promotion_code }}</el-tag>
                <el-tag
                  size="small"
                  :type="row.channel_profile.is_active ? 'primary' : 'info'"
                  style="margin-left:4px"
                >
                  {{ row.channel_profile.is_active ? '推广中' : '已停用' }}
                </el-tag>
              </div>
              <div class="channel-meta">
                优惠 {{ Number(row.channel_profile.promotion_discount_rate || 0) }}%
                · 提成 {{ Number(row.channel_profile.commission_rate || 0) }}%
              </div>
            </template>
            <template v-else-if="row.channel_referral">
              <div>{{ row.channel_referral.channel_name }}</div>
              <div class="channel-meta">推荐码：{{ row.channel_referral.promotion_code }}</div>
            </template>
            <span v-else style="color:#c0c4cc">-</span>
          </template>
        </el-table-column>
        <el-table-column label="IM 账号" min-width="160">
          <template #default="{ row }">
            <template v-if="row.im_accounts && row.im_accounts.length">
              <el-tag
                v-for="im in row.im_accounts"
                :key="im.id"
                size="small"
                :type="im.is_primary ? 'primary' : 'info'"
                style="margin:1px 4px 1px 0"
              >
                {{ im.im_type_label }}：{{ im.account || im.group }}
              </el-tag>
            </template>
            <span v-else style="color:#c0c4cc">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="balance" label="账户余额" width="110" align="right">
          <template #default="{ row }">
            <span :class="row.balance < 0 ? 'text-danger' : 'text-success'">
              ¥{{ Number(row.balance || 0).toFixed(2) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="信誉值" width="150">
          <template #default="{ row }">
            <template v-if="row.reputation_mode && row.reputation_mode !== 'none'">
              <div>{{ reputationModeLabel(row.reputation_mode) }}</div>
              <div class="channel-meta">余额 {{ Number(row.reputation_balance || 0) }} / {{ Number(row.reputation_quota || 0) }}</div>
            </template>
            <span v-else style="color:#c0c4cc">关闭</span>
          </template>
        </el-table-column>
        <el-table-column prop="sales_manager_name" label="销售经理" width="100" />
        <el-table-column prop="created_at" label="创建时间" width="100">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="登录账号" width="130" show-overflow-tooltip>
          <template #default="{ row }">{{ row.account_username || '-' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="viewDetail(row)">详情</el-button>
            <el-button link type="primary" size="small" @click="openDialog(row)">编辑</el-button>
            <el-dropdown size="small" @command="(cmd) => handleCommand(cmd, row)">
              <el-button link type="primary" size="small">更多<el-icon class="el-icon--right"><ArrowDown /></el-icon></el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="portal-account" v-if="!row.account && !row.account_username">开通账号</el-dropdown-item>
                  <el-dropdown-item command="channel-settlement" v-if="row.customer_type === 'channel'">推广/结算</el-dropdown-item>
                  <el-dropdown-item
                    v-if="!(isChannelAccount && row.id === authStore.user?.channel_id)"
                    command="delete"
                    style="color:#f56c6c"
                  >删除</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50]"
        layout="total, sizes, prev, pager, next"
        class="pagination"
        @change="loadData"
      />
    </el-card>

    <!-- 新增/编辑对话框 -->
    <el-dialog v-model="dialogVisible" :title="editRow ? '编辑客户' : '新增客户'" width="640px" destroy-on-close>
      <el-form ref="formRef" :model="form" :rules="formRules" label-width="100px">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="客户名称" prop="name">
              <el-input v-model="form.name" placeholder="请输入客户名称" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="客户类型" prop="customer_type">
              <el-select v-model="form.customer_type" style="width:100%" :disabled="isChannelAccount && editRow?.id === authStore.user?.channel_id">
                <el-option v-for="t in formCustomerTypes" :key="t.value" :label="t.label" :value="t.value" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="联系人" prop="contact_name">
              <el-input v-model="form.contact_name" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="联系电话" prop="contact_phone">
              <el-input v-model="form.contact_phone" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="联系邮箱">
              <el-input v-model="form.contact_email" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="公司名称">
              <el-input v-model="form.company_name" />
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="!isChannelAccount">
            <el-form-item label="信用额度">
              <el-input-number v-model="form.credit_limit" :min="0" :precision="2" controls-position="right" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :span="24" v-if="!isChannelAccount">
            <el-divider content-position="left">信誉值支付</el-divider>
          </el-col>
          <el-col :span="12" v-if="!isChannelAccount">
            <el-form-item label="信誉值模式">
              <el-select v-model="form.reputation_mode" style="width:100%">
                <el-option v-for="m in reputationModeOptions" :key="m.value" :label="m.label" :value="m.value" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="!isChannelAccount">
            <el-form-item label="信誉值额度">
              <el-input-number
                v-model="form.reputation_quota"
                :min="0"
                :precision="2"
                :disabled="form.reputation_mode === 'none'"
                controls-position="right"
                style="width:100%"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="!isChannelAccount">
            <el-form-item label="当前余额">
              <el-input-number
                v-model="form.reputation_balance"
                :min="0"
                :precision="2"
                :disabled="form.reputation_mode === 'none'"
                controls-position="right"
                style="width:100%"
              />
            </el-form-item>
          </el-col>
          <template v-if="form.customer_type === 'channel' && !isChannelAccount">
            <el-col :span="24">
              <el-divider content-position="left">渠道推广规则</el-divider>
            </el-col>
            <el-col :span="12">
              <el-form-item label="推广优惠码">
                <el-input v-model="form.promotion_code" placeholder="留空自动生成；仅支持字母、数字、_、-" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="推广状态">
                <el-switch v-model="form.channel_promotion_enabled" active-text="启用" inactive-text="停用" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="客户优惠(%)">
                <el-input-number v-model="form.promotion_discount_rate" :min="0" :max="100" :precision="2" controls-position="right" style="width:100%" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="渠道提成(%)">
                <el-input-number v-model="form.commission_rate" :min="0" :max="100" :precision="2" controls-position="right" style="width:100%" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="注册授信模式">
                <el-select v-model="form.reputation_grant_mode" style="width:100%">
                  <el-option v-for="m in reputationModeOptions" :key="m.value" :label="m.label" :value="m.value" />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="注册授信额度">
                <el-input-number
                  v-model="form.reputation_grant_amount"
                  :min="0"
                  :precision="2"
                  :disabled="form.reputation_grant_mode === 'none'"
                  controls-position="right"
                  style="width:100%"
                />
              </el-form-item>
            </el-col>
            <el-col :span="24">
              <el-form-item label="渠道规则备注">
                <el-input v-model="form.channel_promotion_remark" type="textarea" :rows="2" />
              </el-form-item>
            </el-col>
          </template>
          <el-col :span="24">
            <el-divider content-position="left">IM 账号（可添加多个，便于后续 IM 对接）</el-divider>
          </el-col>
          <el-col :span="24">
            <div v-for="(im, idx) in form.im_accounts" :key="idx" class="im-row">
              <el-select v-model="im.im_type" placeholder="类型" style="width:120px">
                <el-option v-for="t in imTypes" :key="t.value" :label="t.label" :value="t.value" />
              </el-select>
              <el-input v-model="im.account" placeholder="账号/ID/chat_id" style="width:170px" />
              <el-input v-model="im.group" placeholder="群组(可选)" style="width:150px" />
              <el-input v-model="im.label" placeholder="用途备注" style="width:110px" />
              <el-radio v-model="primaryIndex" :label="idx">主账号</el-radio>
              <el-button link type="danger" @click="removeIM(idx)">移除</el-button>
            </div>
            <el-button size="small" :icon="Plus" @click="addIM">添加 IM 账号</el-button>
          </el-col>
          <el-col :span="24">
            <el-form-item label="备注">
              <el-input v-model="form.remark" type="textarea" :rows="2" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">确认</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="portalVisible" :title="`开通登录账号 - ${portalCustomer?.name || ''}`" width="460px" destroy-on-close>
      <el-form ref="portalFormRef" :model="portalForm" :rules="portalRules" label-width="90px">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="portalForm.username" autocomplete="off" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input v-model="portalForm.password" type="password" show-password autocomplete="new-password" />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input v-model="portalForm.email" />
        </el-form-item>
        <el-form-item label="姓名">
          <el-input v-model="portalForm.first_name" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="portalVisible = false">取消</el-button>
        <el-button type="primary" :loading="portalSaving" @click="submitPortalAccount">开通</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search, RefreshLeft, ArrowDown } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import {
  getCustomers, createCustomer, updateCustomer, deleteCustomer,
  createCustomerPortalAccount,
} from '@/api/customer'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()
const isChannelAccount = computed(() =>
  authStore.user?.role === 'channel' || authStore.user?.is_channel_account
)
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const formRef = ref(null)
const editRow = ref(null)
const tableData = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

const searchForm = reactive({ search: '', customer_type: '' })

const form = reactive({
  name: '', customer_type: 'enterprise', contact_name: '', contact_phone: '',
  contact_email: '', company_name: '', credit_limit: 0, remark: '',
  im_accounts: [],
  promotion_code: '', promotion_discount_rate: 0, commission_rate: 0,
  channel_promotion_enabled: true, channel_promotion_remark: '',
  reputation_mode: 'none', reputation_quota: 0, reputation_balance: 0,
  reputation_grant_mode: 'none', reputation_grant_amount: 0,
})
const primaryIndex = ref(-1)

function addIM() {
  form.im_accounts.push({ im_type: 'telegram', account: '', group: '', label: '', is_primary: false })
  if (primaryIndex.value < 0) primaryIndex.value = form.im_accounts.length - 1
}
function removeIM(idx) {
  form.im_accounts.splice(idx, 1)
  if (primaryIndex.value === idx) primaryIndex.value = form.im_accounts.length ? 0 : -1
  else if (primaryIndex.value > idx) primaryIndex.value -= 1
}

const imTypes = [
  { value: 'telegram', label: 'Telegram' },
  { value: 'wecom', label: '企业微信' },
  { value: 'dingtalk', label: '钉钉' },
  { value: 'wechat', label: '微信' },
  { value: 'whatsapp', label: 'WhatsApp' },
  { value: 'qq', label: 'QQ' },
  { value: 'other', label: '其他' }
]

const customerTypes = [
  { value: 'personal', label: '个人' },
  { value: 'enterprise', label: '企业' },
  { value: 'channel', label: '渠道商' },
  { value: 'provider', label: '供应商' },
  { value: 'other', label: '其他' }
]
const formCustomerTypes = computed(() =>
  isChannelAccount.value
    ? customerTypes.filter((t) => t.value !== 'channel' && t.value !== 'provider')
    : customerTypes
)

const typeTagMap = { personal: '', enterprise: 'primary', channel: 'success', provider: 'warning', other: 'info' }

const reputationModeOptions = [
  { value: 'none', label: '关闭（不可信誉支付）' },
  { value: 'one_time', label: '一次性' },
  { value: 'monthly', label: '按月更新' },
]

function reputationModeLabel(mode) {
  return reputationModeOptions.find((item) => item.value === mode)?.label || '关闭'
}

watch(() => form.reputation_mode, (mode, prev) => {
  if (mode === 'none') {
    form.reputation_quota = 0
    form.reputation_balance = 0
    return
  }
  if (prev === 'none' && Number(form.reputation_balance || 0) === 0) {
    form.reputation_balance = Number(form.reputation_quota || 0)
  }
})

const formRules = {
  name: [{ required: true, message: '请输入客户名称' }],
  customer_type: [{ required: true, message: '请选择客户类型' }],
  contact_name: [{ required: true, message: '请输入联系人' }],
  contact_phone: [{ required: true, message: '请输入联系电话' }]
}

function getTypeLabel(type) {
  return customerTypes.find(t => t.value === type)?.label || type
}

function formatDate(date) {
  return date ? dayjs(date).format('YYYY-MM-DD') : '-'
}

async function loadData() {
  loading.value = true
  try {
    const res = await getCustomers({
      ...searchForm,
      page: page.value,
      page_size: pageSize.value
    })
    tableData.value = res?.results || []
    total.value = res?.count || 0
  } catch {
    tableData.value = []
  } finally {
    loading.value = false
  }
}

function resetSearch() {
  Object.assign(searchForm, { search: '', customer_type: '' })
  page.value = 1
  loadData()
}

function viewDetail(row) {
  router.push(`/customer/${row.id}`)
}

function openDialog(row = null) {
  editRow.value = row
  if (row) {
    Object.assign(form, {
      name: row.name,
      customer_type: row.customer_type,
      contact_name: row.contact_name,
      contact_phone: row.contact_phone,
      contact_email: row.contact_email || '',
      company_name: row.company_name || '',
      credit_limit: row.credit_limit || 0,
      remark: row.remark || '',
      promotion_code: row.channel_profile?.promotion_code || '',
      promotion_discount_rate: Number(row.channel_profile?.promotion_discount_rate || 0),
      commission_rate: Number(row.channel_profile?.commission_rate || 0),
      channel_promotion_enabled: row.channel_profile?.is_active ?? true,
      channel_promotion_remark: row.channel_profile?.remark || '',
      reputation_mode: row.reputation_mode || 'none',
      reputation_quota: Number(row.reputation_quota || 0),
      reputation_balance: Number(row.reputation_balance || 0),
      reputation_grant_mode: row.channel_profile?.reputation_grant_mode || 'none',
      reputation_grant_amount: Number(row.channel_profile?.reputation_grant_amount || 0),
      im_accounts: (row.im_accounts || []).map(a => ({
        im_type: a.im_type, account: a.account || '', group: a.group || '',
        label: a.label || '', is_primary: a.is_primary
      }))
    })
    primaryIndex.value = form.im_accounts.findIndex(a => a.is_primary)
  } else {
    Object.assign(form, {
      name: '', customer_type: 'enterprise', contact_name: '', contact_phone: '',
      contact_email: '', company_name: '', credit_limit: 0, remark: '', im_accounts: [],
      promotion_code: '', promotion_discount_rate: 0, commission_rate: 0,
      channel_promotion_enabled: true, channel_promotion_remark: '',
      reputation_mode: 'none', reputation_quota: 0, reputation_balance: 0,
      reputation_grant_mode: 'none', reputation_grant_amount: 0,
    })
    primaryIndex.value = -1
  }
  dialogVisible.value = true
}

async function handleSave() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  const payload = {
    name: form.name,
    customer_type: form.customer_type,
    contact_name: form.contact_name,
    contact_phone: form.contact_phone,
    contact_email: form.contact_email,
    company_name: form.company_name,
    remark: form.remark,
    im_accounts: form.im_accounts
      .filter(a => a.account || a.group)
      .map((a, i) => ({ ...a, is_primary: i === primaryIndex.value }))
  }
  if (!isChannelAccount.value) {
    payload.credit_limit = form.credit_limit
    payload.reputation_mode = form.reputation_mode
    payload.reputation_quota = form.reputation_mode === 'none' ? 0 : form.reputation_quota
    payload.reputation_balance = form.reputation_mode === 'none' ? 0 : form.reputation_balance
  }
  if (form.customer_type === 'channel' && !isChannelAccount.value) {
    Object.assign(payload, {
      promotion_code: form.promotion_code,
      promotion_discount_rate: form.promotion_discount_rate,
      commission_rate: form.commission_rate,
      channel_promotion_enabled: form.channel_promotion_enabled,
      channel_promotion_remark: form.channel_promotion_remark,
      reputation_grant_mode: form.reputation_grant_mode,
      reputation_grant_amount: form.reputation_grant_mode === 'none' ? 0 : form.reputation_grant_amount,
    })
  }
  saving.value = true
  try {
    if (editRow.value) {
      await updateCustomer(editRow.value.id, payload)
      ElMessage.success('客户信息已更新')
    } else {
      await createCustomer(payload)
      ElMessage.success('客户创建成功')
    }
    dialogVisible.value = false
    loadData()
  } catch (err) {
    ElMessage.error(err?.response?.data?.message || '操作失败')
  } finally {
    saving.value = false
  }
}

const portalVisible = ref(false)
const portalSaving = ref(false)
const portalFormRef = ref(null)
const portalCustomer = ref(null)
const portalForm = reactive({ username: '', password: '', email: '', first_name: '' })
const portalRules = {
  username: [{ required: true, message: '请输入用户名' }],
  password: [{ required: true, message: '请输入密码' }, { min: 8, message: '密码至少8位' }],
}

function openPortalDialog(row) {
  portalCustomer.value = row
  Object.assign(portalForm, {
    username: '',
    password: '',
    email: row.contact_email || '',
    first_name: row.contact_name || '',
  })
  portalVisible.value = true
}

async function submitPortalAccount() {
  const valid = await portalFormRef.value.validate().catch(() => false)
  if (!valid || !portalCustomer.value) return
  portalSaving.value = true
  try {
    await createCustomerPortalAccount(portalCustomer.value.id, { ...portalForm })
    ElMessage.success('登录账号已开通')
    portalVisible.value = false
    loadData()
  } catch (err) {
    const data = err?.response?.data
    const msg = data?.username?.[0] || data?.password?.[0] || data?.detail || data?.message || '开通失败'
    ElMessage.error(typeof msg === 'string' ? msg : '开通失败')
  } finally {
    portalSaving.value = false
  }
}

async function handleCommand(cmd, row) {
  if (cmd === 'delete') {
    await ElMessageBox.confirm(`确定要删除客户 "${row.name}" 吗？`, '警告', { type: 'warning' })
    await deleteCustomer(row.id)
    ElMessage.success('删除成功')
    loadData()
  } else if (cmd === 'channel-settlement') {
    router.push({ path: '/customer/channels', query: { channel: row.id } })
  } else if (cmd === 'portal-account') {
    openPortalDialog(row)
  }
}

onMounted(loadData)
</script>

<style scoped>
.page-container { min-height: 100%; }
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.page-title { font-size: 18px; font-weight: 700; color: #1a1a2e; margin: 0; }
.search-card :deep(.el-card__body) { padding: 16px 16px 0; }
.pagination { margin-top: 16px; justify-content: flex-end; }
.text-success { color: #67C23A; }
.text-danger { color: #F56C6C; }
.im-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
.channel-meta { margin-top: 4px; color: #909399; font-size: 12px; }
</style>
