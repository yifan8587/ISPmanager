<template>
  <div class="page-container">
    <div class="page-header">
      <div><h2>官网咨询</h2><p>集中处理官网“需要帮助”和“申请测试”提交的客户需求。</p></div>
      <el-button @click="load">刷新</el-button>
    </div>
    <el-card shadow="never">
      <div class="toolbar">
        <el-select v-model="filters.inquiry_type" clearable placeholder="需求类型" @change="load">
          <el-option label="需要帮助" value="help" /><el-option label="申请测试" value="trial" />
        </el-select>
        <el-select v-model="filters.status" clearable placeholder="处理状态" @change="load">
          <el-option label="待处理" value="new" /><el-option label="跟进中" value="processing" />
          <el-option label="已回复" value="replied" /><el-option label="已关闭" value="closed" />
        </el-select>
        <el-input v-model="filters.search" clearable placeholder="搜索联系人、公司、联系方式或需求" @keyup.enter="load" @clear="load" />
      </div>
      <el-table :data="items" v-loading="loading" border stripe>
        <el-table-column prop="inquiry_no" label="编号" width="180" />
        <el-table-column prop="inquiry_type_label" label="类型" width="110" />
        <el-table-column label="联系人" min-width="150">
          <template #default="{ row }"><b>{{ row.contact_name }}</b><div class="muted">{{ row.company_name || '个人咨询' }}</div></template>
        </el-table-column>
        <el-table-column label="联系方式" min-width="180">
          <template #default="{ row }">{{ row.phone || row.email }}<div v-if="row.phone && row.email" class="muted">{{ row.email }}</div></template>
        </el-table-column>
        <el-table-column prop="requirements" label="需求内容" min-width="260" show-overflow-tooltip />
        <el-table-column label="状态" width="110"><template #default="{ row }"><el-tag :type="statusType[row.status]">{{ row.status_label }}</el-tag></template></el-table-column>
        <el-table-column label="处理人" width="120"><template #default="{ row }">{{ row.handler_name || '待分配' }}</template></el-table-column>
        <el-table-column label="跟进" width="110" align="center">
          <template #default="{ row }"><el-tag size="small" type="info">{{ row.reply_count || 0 }} 条</el-tag></template>
        </el-table-column>
        <el-table-column prop="created_at" label="提交时间" width="170"><template #default="{ row }">{{ formatTime(row.created_at) }}</template></el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openReply(row)">{{ canManage ? '处理/回复' : '查看详情' }}</el-button>
            <el-button v-if="canManage && !row.related_order" link type="success" @click="openConvert(row)">转服务工单</el-button>
            <el-button v-if="canManage && !row.related_ticket" link type="warning" @click="openConvertTicket(row)">转技术工单</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination v-if="total > pageSize" class="pager" background layout="prev, pager, next" :total="total" :page-size="pageSize" :current-page="page" @current-change="changePage" />
    </el-card>

    <el-dialog v-model="replyVisible" title="处理官网咨询" width="760px" destroy-on-close>
      <template v-if="current">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="联系人">{{ current.contact_name }}</el-descriptions-item>
          <el-descriptions-item label="联系方式">{{ current.phone || current.email }}</el-descriptions-item>
          <el-descriptions-item label="公司/组织" :span="2">{{ current.company_name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="关联客户" :span="2">{{ current.customer_name || '匿名官网咨询（转工单时需选择客户）' }}</el-descriptions-item>
          <el-descriptions-item label="需求内容" :span="2">{{ current.requirements }}</el-descriptions-item>
          <el-descriptions-item label="来源页面" :span="2">{{ current.source_url || '-' }}</el-descriptions-item>
          <el-descriptions-item v-if="current.related_order_no" label="服务工单" :span="2">
            <el-tag type="success">已转服务工单：{{ current.related_order_no }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item v-if="current.related_ticket_no" label="技术工单" :span="2">
            <el-tag type="warning">已转技术工单：{{ current.related_ticket_no }}</el-tag>
          </el-descriptions-item>
        </el-descriptions>
        <template v-if="canManage">
          <el-divider content-position="left">处理与回复</el-divider>
          <el-form label-position="top">
          <el-row :gutter="16">
            <el-col :span="12">
              <el-form-item label="处理人">
                <el-select v-model="assignForm.handler" filterable clearable placeholder="选择处理人" style="width:100%">
                  <el-option v-for="user in handlers" :key="user.id" :label="userLabel(user)" :value="user.id" />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="12" class="assign-action">
              <el-button type="primary" plain :loading="assigning" :disabled="!assignForm.handler" @click="saveAssign">更新处理人</el-button>
              <el-button v-if="!current.related_order" type="success" plain @click="openConvert(current)">转为服务工单</el-button>
              <el-button v-if="!current.related_ticket" type="warning" plain @click="openConvertTicket(current)">转为技术工单</el-button>
            </el-col>
          </el-row>
          </el-form>
          <el-form label-position="top">
          <el-form-item label="跟进类型">
            <el-radio-group v-model="replyForm.entry_type">
              <el-radio-button label="reply">记录客户回复</el-radio-button>
              <el-radio-button label="note">记录内部跟进</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-form-item :label="replyForm.entry_type === 'note' ? '内部跟进内容' : '回复内容'">
            <el-input v-model="replyForm.reply_content" type="textarea" :rows="4" :placeholder="replyForm.entry_type === 'note' ? '记录电话、微信、邮件等内部跟进情况' : '记录已向客户提供的回复或处理结果'" />
          </el-form-item>
          <el-form-item label="处理状态">
            <el-radio-group v-model="replyForm.status">
              <el-radio-button label="processing">继续跟进</el-radio-button>
              <el-radio-button label="replied">已回复</el-radio-button>
              <el-radio-button label="closed">已关闭</el-radio-button>
            </el-radio-group>
          </el-form-item>
          </el-form>
        </template>
        <el-divider content-position="left">跟进记录</el-divider>
        <el-timeline class="reply-history">
          <el-timeline-item v-for="item in current.reply_records || []" :key="item.id" :timestamp="formatTime(item.created_at)" placement="top">
            <div class="history-title">
              <el-tag size="small" :type="entryType[item.entry_type]?.type">{{ item.entry_type_label }}</el-tag>
              <span>{{ item.operator_name || '系统' }}</span>
              <span class="muted">→ {{ item.status_after_label }}</span>
            </div>
            <div class="history-content">{{ item.content }}</div>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-if="!(current.reply_records || []).length" description="暂无跟进记录" :image-size="52" />
      </template>
      <template #footer>
        <el-button @click="replyVisible = false">{{ canManage ? '取消' : '关闭' }}</el-button>
        <el-button v-if="canManage" type="primary" :loading="saving" @click="saveReply">保存回复</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="convertVisible" title="官网咨询转服务工单" width="600px" destroy-on-close append-to-body>
      <el-alert type="info" :closable="false" show-icon title="将保留原咨询与联系人信息，并创建一张待审批的正式服务工单。" />
      <el-form class="convert-form" label-position="top">
        <el-form-item label="客户" required>
          <el-select v-model="convertForm.customer" filterable placeholder="选择服务所属客户" style="width:100%">
            <el-option v-for="customer in customers" :key="customer.id" :value="customer.id" :label="`${customer.customer_no || ''} ${customer.name}`.trim()" />
          </el-select>
        </el-form-item>
        <el-form-item label="服务产品" required>
          <el-select v-model="convertForm.product" filterable placeholder="选择已上线产品" style="width:100%">
            <el-option v-for="product in products" :key="product.id" :value="product.id" :label="`${product.product_no || ''} ${product.name}`.trim()" />
          </el-select>
        </el-form-item>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="处理人">
              <el-select v-model="convertForm.handler" clearable filterable placeholder="默认沿用当前处理人" style="width:100%">
                <el-option v-for="user in handlers" :key="user.id" :label="userLabel(user)" :value="user.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="需求数量">
              <el-input-number v-model="convertForm.requested_quantity" :min="1" :max="100" style="width:100%" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
      <template #footer>
        <el-button @click="convertVisible = false">取消</el-button>
        <el-button type="success" :loading="converting" @click="saveConvert">确认转工单</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="convertTicketVisible" title="咨询转技术工单" width="480px" destroy-on-close append-to-body>
      <el-alert type="info" :closable="false" show-icon title="咨询与服务工单仍保留；将另建一张带 SLA 的技术工单。" />
      <el-form class="convert-form" label-position="top">
        <el-form-item label="分类">
          <el-select v-model="convertTicketForm.category" style="width:100%">
            <el-option label="技术故障" value="technical" />
            <el-option label="滥用投诉" value="abuse" />
            <el-option label="财务" value="billing" />
            <el-option label="接入" value="access" />
          </el-select>
        </el-form-item>
        <el-form-item label="优先级">
          <el-select v-model="convertTicketForm.priority" style="width:100%">
            <el-option label="P1 紧急" value="p1" />
            <el-option label="P2 高" value="p2" />
            <el-option label="P3 普通" value="p3" />
            <el-option label="P4 低" value="p4" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="convertTicketVisible = false">取消</el-button>
        <el-button type="warning" :loading="convertingTicket" @click="saveConvertTicket">确认转技术工单</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { getCustomers } from '@/api/customer'
import { getProducts } from '@/api/product'
import {
  assignServiceInquiry, convertServiceInquiryToOrder, convertServiceInquiryToTicket,
  getServiceInquiries, replyServiceInquiry,
} from '@/api/service'
import { getUsers } from '@/api/accounts'

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('service.inquiry.manage') || authStore.hasPermission('service.manage') || authStore.user?.is_superuser)
const items = ref([]); const total = ref(0); const page = ref(1); const pageSize = 20; const loading = ref(false)
const filters = reactive({ inquiry_type: '', status: '', search: '' })
const replyVisible = ref(false); const current = ref(null); const saving = ref(false)
const assigning = ref(false)
const handlers = ref([])
const customers = ref([])
const products = ref([])
const assignForm = reactive({ handler: null })
const replyForm = reactive({ reply_content: '', entry_type: 'reply', status: 'replied' })
const convertVisible = ref(false)
const converting = ref(false)
const convertForm = reactive({ customer: null, product: null, handler: null, requested_quantity: 1 })
const convertTicketVisible = ref(false)
const convertingTicket = ref(false)
const convertTicketForm = reactive({ category: 'technical', priority: 'p3' })
const statusType = { new: 'danger', processing: 'warning', replied: 'success', closed: 'info' }
const entryType = { reply: { type: 'success' }, note: { type: 'warning' }, assign: { type: 'info' } }

function formatTime(value) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '' }
function userLabel(user) {
  const name = [user.first_name, user.last_name].filter(Boolean).join(' ') || user.username
  return `${name} (${user.username})`
}
async function load() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize }
    Object.entries(filters).forEach(([key, value]) => { if (value) params[key] = value })
    const res = await getServiceInquiries(params)
    items.value = res.results || res; total.value = res.count || items.value.length
  } finally { loading.value = false }
}
function changePage(value) { page.value = value; load() }
function openReply(row) {
  current.value = row
  assignForm.handler = row.handler || null
  Object.assign(replyForm, {
    reply_content: '',
    entry_type: 'reply',
    status: row.status === 'closed' ? 'closed' : row.status === 'processing' ? 'processing' : 'replied',
  })
  replyVisible.value = true
}
function openConvertTicket(row) {
  if (!canManage.value) return
  if (row.related_ticket) return ElMessage.info(`该咨询已转为技术工单 ${row.related_ticket_no || ''}`)
  if (!row.customer) return ElMessage.warning('请先为咨询绑定客户后再转技术工单')
  current.value = row
  Object.assign(convertTicketForm, { category: 'technical', priority: 'p3' })
  convertTicketVisible.value = true
}
function openConvert(row) {
  if (!canManage.value) return
  if (row.related_order) return ElMessage.info(`该咨询已转为工单 ${row.related_order_no || ''}`)
  current.value = row
  Object.assign(convertForm, {
    customer: row.customer || null,
    product: null,
    handler: row.handler || null,
    requested_quantity: 1,
  })
  convertVisible.value = true
}
async function saveAssign() {
  if (!current.value || !assignForm.handler) return
  assigning.value = true
  try {
    const updated = await assignServiceInquiry(current.value.id, { handler: assignForm.handler })
    current.value = updated
    ElMessage.success('处理人已更新')
    await load()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '更新处理人失败')
  } finally {
    assigning.value = false
  }
}
async function saveReply() {
  if (!replyForm.reply_content.trim()) return ElMessage.warning('请填写回复内容')
  saving.value = true
  try {
    const updated = await replyServiceInquiry(current.value.id, replyForm)
    current.value = updated
    ElMessage.success(replyForm.entry_type === 'note' ? '跟进记录已保存' : '回复已保存')
    replyForm.reply_content = ''
    await load()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '保存失败')
  } finally { saving.value = false }
}
async function saveConvert() {
  if (!current.value || !convertForm.customer || !convertForm.product) {
    return ElMessage.warning('请选择客户和服务产品')
  }
  converting.value = true
  try {
    const result = await convertServiceInquiryToOrder(current.value.id, convertForm)
    ElMessage.success(`已创建待审批工单 ${result.order_no}`)
    convertVisible.value = false
    replyVisible.value = false
    await load()
  } catch (error) {
    const detail = error?.response?.data?.detail || error?.response?.data
    ElMessage.error(typeof detail === 'string' ? detail : '转服务工单失败')
  } finally {
    converting.value = false
  }
}
async function saveConvertTicket() {
  if (!current.value) return
  convertingTicket.value = true
  try {
    const result = await convertServiceInquiryToTicket(current.value.id, convertTicketForm)
    ElMessage.success(`已创建技术工单 ${result.ticket_no}`)
    convertTicketVisible.value = false
    replyVisible.value = false
    await load()
  } catch (error) {
    const detail = error?.response?.data?.detail || error?.response?.data
    ElMessage.error(typeof detail === 'string' ? detail : '转技术工单失败')
  } finally {
    convertingTicket.value = false
  }
}
onMounted(async () => {
  await Promise.all([
    load(),
    getUsers({ page_size: 500, is_active: true }).then((res) => {
      handlers.value = res?.results || []
    }).catch(() => { handlers.value = [] }),
    getCustomers({ page_size: 500 }).then((res) => {
      customers.value = res?.results || []
    }).catch(() => { customers.value = [] }),
    getProducts({ status: 'online', page_size: 500 }).then((res) => {
      products.value = res?.results || []
    }).catch(() => { products.value = [] }),
  ])
})
</script>

<style scoped>
.page-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:16px; }.page-header h2{margin:0;font-size:20px}.page-header p{margin:6px 0 0;color:#909399;font-size:13px}.toolbar{display:flex;gap:12px;margin-bottom:16px}.toolbar .el-input{max-width:320px}.muted{margin-top:4px;color:#909399;font-size:12px}.pager{justify-content:center;margin-top:20px}.assign-action{display:flex;align-items:end;gap:8px;padding-bottom:18px}.reply-history{max-height:260px;overflow:auto;padding-top:4px}.history-title{display:flex;align-items:center;gap:8px;font-size:13px}.history-content{white-space:pre-wrap;line-height:1.65;margin-top:6px;color:#303133}.convert-form{margin-top:18px}@media(max-width:768px){.toolbar{flex-wrap:wrap}.toolbar .el-input{max-width:none;flex:1 1 100%}}
</style>
