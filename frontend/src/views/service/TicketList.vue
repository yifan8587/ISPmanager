<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <h2>技术工单</h2>
        <p>与服务开通工单分离：技术故障 / 滥用 / 财务 / 接入，含 SLA 响应与解决时效。</p>
      </div>
      <div class="header-actions">
        <el-button v-if="canManage" type="primary" @click="openCreate">新建工单</el-button>
        <el-button @click="load">刷新</el-button>
      </div>
    </div>
    <el-card shadow="never">
      <div class="toolbar">
        <el-select v-model="filters.category" clearable placeholder="分类" @change="load">
          <el-option v-for="item in categoryOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-select v-model="filters.priority" clearable placeholder="优先级" @change="load">
          <el-option v-for="item in priorityOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-select v-model="filters.status" clearable placeholder="状态" @change="load">
          <el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-input v-model="filters.search" clearable placeholder="搜索编号、主题或描述" @keyup.enter="load" @clear="load" />
      </div>
      <el-table :data="items" v-loading="loading" border stripe>
        <el-table-column prop="ticket_no" label="编号" width="170" />
        <el-table-column prop="customer_name" label="客户" min-width="140" />
        <el-table-column prop="subject" label="主题" min-width="200" show-overflow-tooltip />
        <el-table-column prop="category_label" label="分类" width="110" />
        <el-table-column label="优先级" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="priorityType[row.priority]">{{ row.priority_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType[row.status]">{{ row.status_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="SLA" min-width="180">
          <template #default="{ row }">
            <div class="sla">
              <el-tag v-if="row.sla_response_overdue || row.sla_response_breached" size="small" type="danger">响应超时</el-tag>
              <el-tag v-else-if="!row.first_responded_at" size="small" type="warning">待响应 {{ formatTime(row.first_response_due) }}</el-tag>
              <el-tag v-else size="small" type="success">已响应</el-tag>
              <el-tag v-if="row.sla_resolve_overdue || row.sla_resolve_breached" size="small" type="danger">解决超时</el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="处理人" width="110">
          <template #default="{ row }">{{ row.handler_name || '待分配' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="110" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDetail(row)">{{ canManage ? '处理' : '查看' }}</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-if="total > pageSize"
        class="pager"
        background
        layout="prev, pager, next"
        :total="total"
        :page-size="pageSize"
        :current-page="page"
        @current-change="changePage"
      />
    </el-card>

    <el-dialog v-model="createVisible" title="新建技术工单" width="560px" destroy-on-close>
      <el-form label-position="top">
        <el-form-item label="客户" required>
          <el-select v-model="createForm.customer" filterable placeholder="选择客户" style="width:100%">
            <el-option
              v-for="customer in customers"
              :key="customer.id"
              :value="customer.id"
              :label="`${customer.customer_no || ''} ${customer.name}`.trim()"
            />
          </el-select>
        </el-form-item>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="分类">
              <el-select v-model="createForm.category" style="width:100%">
                <el-option v-for="item in categoryOptions" :key="item.value" :label="item.label" :value="item.value" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="优先级">
              <el-select v-model="createForm.priority" style="width:100%">
                <el-option v-for="item in priorityOptions" :key="item.value" :label="item.label" :value="item.value" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="主题" required>
          <el-input v-model="createForm.subject" maxlength="200" />
        </el-form-item>
        <el-form-item label="问题描述" required>
          <el-input v-model="createForm.description" type="textarea" :rows="4" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="saveCreate">提交</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="detailVisible" title="技术工单详情" width="780px" destroy-on-close>
      <template v-if="current">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="编号">{{ current.ticket_no }}</el-descriptions-item>
          <el-descriptions-item label="客户">{{ current.customer_name }}</el-descriptions-item>
          <el-descriptions-item label="分类">{{ current.category_label }}</el-descriptions-item>
          <el-descriptions-item label="优先级">{{ current.priority_label }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ current.status_label }}</el-descriptions-item>
          <el-descriptions-item label="处理人">{{ current.handler_name || '待分配' }}</el-descriptions-item>
          <el-descriptions-item label="响应截止">{{ formatTime(current.first_response_due) }}</el-descriptions-item>
          <el-descriptions-item label="解决截止">{{ formatTime(current.resolve_due) }}</el-descriptions-item>
          <el-descriptions-item label="主题" :span="2">{{ current.subject }}</el-descriptions-item>
          <el-descriptions-item label="描述" :span="2">{{ current.description }}</el-descriptions-item>
        </el-descriptions>
        <template v-if="canManage">
          <el-divider content-position="left">处理</el-divider>
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
                <el-button type="warning" plain :loading="escalating" @click="saveEscalate">升级</el-button>
              </el-col>
            </el-row>
            <el-form-item label="回复类型">
              <el-radio-group v-model="replyForm.is_internal">
                <el-radio-button :label="false">对客户回复</el-radio-button>
                <el-radio-button :label="true">内部备注</el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="内容">
              <el-input v-model="replyForm.content" type="textarea" :rows="3" />
            </el-form-item>
            <el-form-item label="处理后状态">
              <el-select v-model="replyForm.status" clearable placeholder="保持当前" style="width:240px">
                <el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" />
              </el-select>
            </el-form-item>
          </el-form>
        </template>
        <el-divider content-position="left">往来记录</el-divider>
        <el-timeline class="reply-history">
          <el-timeline-item v-for="item in current.messages || []" :key="item.id" :timestamp="formatTime(item.created_at)" placement="top">
            <div class="history-title">
              <el-tag size="small" :type="item.is_internal ? 'warning' : (item.author_type === 'staff' ? 'success' : 'info')">
                {{ item.is_internal ? '内部备注' : item.author_type_label }}
              </el-tag>
              <span>{{ item.author_name }}</span>
            </div>
            <div class="history-content">{{ item.content }}</div>
          </el-timeline-item>
        </el-timeline>
      </template>
      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
        <template v-if="canManage && current">
          <el-button :loading="saving" @click="saveReply">保存回复</el-button>
          <el-button type="success" :loading="resolving" @click="saveResolve">标记已解决</el-button>
          <el-button type="danger" plain :loading="closing" @click="saveClose">关闭工单</el-button>
        </template>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { getCustomers } from '@/api/customer'
import { getUsers } from '@/api/accounts'
import {
  assignSupportTicket, closeSupportTicket, createSupportTicket, escalateSupportTicket,
  getSupportTicket, getSupportTickets, replySupportTicket, resolveSupportTicket,
} from '@/api/service'

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('service.ticket.manage') || authStore.hasPermission('service.manage') || authStore.user?.is_superuser)
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const loading = ref(false)
const filters = reactive({ category: '', priority: '', status: '', search: '' })
const customers = ref([])
const handlers = ref([])
const createVisible = ref(false)
const creating = ref(false)
const createForm = reactive({ customer: null, category: 'technical', priority: 'p3', subject: '', description: '' })
const detailVisible = ref(false)
const current = ref(null)
const saving = ref(false)
const assigning = ref(false)
const escalating = ref(false)
const resolving = ref(false)
const closing = ref(false)
const assignForm = reactive({ handler: null })
const replyForm = reactive({ content: '', is_internal: false, status: '' })

const categoryOptions = [
  { value: 'technical', label: '技术故障' },
  { value: 'abuse', label: '滥用投诉' },
  { value: 'billing', label: '财务' },
  { value: 'access', label: '接入' },
]
const priorityOptions = [
  { value: 'p1', label: 'P1 紧急' },
  { value: 'p2', label: 'P2 高' },
  { value: 'p3', label: 'P3 普通' },
  { value: 'p4', label: 'P4 低' },
]
const statusOptions = [
  { value: 'open', label: '待处理' },
  { value: 'in_progress', label: '处理中' },
  { value: 'pending_customer', label: '待客户回复' },
  { value: 'waiting', label: '挂起' },
  { value: 'resolved', label: '已解决' },
  { value: 'closed', label: '已关闭' },
]
const statusType = {
  open: 'danger',
  in_progress: 'warning',
  pending_customer: 'info',
  waiting: 'info',
  resolved: 'success',
  closed: 'info',
}
const priorityType = { p1: 'danger', p2: 'warning', p3: '', p4: 'info' }

function formatTime(value) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : ''
}
function userLabel(user) {
  const name = [user.first_name, user.last_name].filter(Boolean).join(' ') || user.username
  return `${name} (${user.username})`
}
async function load() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize }
    Object.entries(filters).forEach(([key, value]) => { if (value) params[key] = value })
    const res = await getSupportTickets(params)
    items.value = res.results || res
    total.value = res.count || items.value.length
  } finally {
    loading.value = false
  }
}
function changePage(value) { page.value = value; load() }
function openCreate() {
  Object.assign(createForm, { customer: null, category: 'technical', priority: 'p3', subject: '', description: '' })
  createVisible.value = true
}
async function saveCreate() {
  if (!createForm.customer || !createForm.subject.trim() || !createForm.description.trim()) {
    return ElMessage.warning('请填写客户、主题和描述')
  }
  creating.value = true
  try {
    await createSupportTicket(createForm)
    ElMessage.success('工单已创建')
    createVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '创建失败')
  } finally {
    creating.value = false
  }
}
async function openDetail(row) {
  const detail = await getSupportTicket(row.id)
  current.value = detail
  assignForm.handler = detail.handler || null
  Object.assign(replyForm, { content: '', is_internal: false, status: '' })
  detailVisible.value = true
}
async function saveAssign() {
  if (!current.value || !assignForm.handler) return
  assigning.value = true
  try {
    current.value = await assignSupportTicket(current.value.id, { handler: assignForm.handler })
    ElMessage.success('处理人已更新')
    await load()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '更新失败')
  } finally {
    assigning.value = false
  }
}
async function saveReply() {
  if (!replyForm.content.trim()) return ElMessage.warning('请填写回复内容')
  saving.value = true
  try {
    const payload = { content: replyForm.content, is_internal: replyForm.is_internal }
    if (replyForm.status) payload.status = replyForm.status
    current.value = await replySupportTicket(current.value.id, payload)
    ElMessage.success(replyForm.is_internal ? '内部备注已保存' : '回复已保存')
    replyForm.content = ''
    await load()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}
async function saveResolve() {
  resolving.value = true
  try {
    current.value = await resolveSupportTicket(current.value.id, { content: replyForm.content })
    ElMessage.success('已标记解决')
    await load()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '操作失败')
  } finally {
    resolving.value = false
  }
}
async function saveClose() {
  closing.value = true
  try {
    current.value = await closeSupportTicket(current.value.id, { content: replyForm.content })
    ElMessage.success('工单已关闭')
    await load()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '操作失败')
  } finally {
    closing.value = false
  }
}
async function saveEscalate() {
  escalating.value = true
  try {
    current.value = await escalateSupportTicket(current.value.id)
    ElMessage.success(`已升级至级别 ${current.value.escalation_level}`)
    await load()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '升级失败')
  } finally {
    escalating.value = false
  }
}
onMounted(async () => {
  await Promise.all([
    load(),
    getUsers({ page_size: 500, is_active: true }).then((res) => { handlers.value = res?.results || [] }).catch(() => { handlers.value = [] }),
    getCustomers({ page_size: 500 }).then((res) => { customers.value = res?.results || [] }).catch(() => { customers.value = [] }),
  ])
})
</script>

<style scoped>
.page-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:16px; }
.page-header h2{margin:0;font-size:20px}
.page-header p{margin:6px 0 0;color:#909399;font-size:13px}
.header-actions{display:flex;gap:8px}
.toolbar{display:flex;gap:12px;margin-bottom:16px}
.toolbar .el-input{max-width:320px}
.pager{justify-content:center;margin-top:20px}
.assign-action{display:flex;align-items:end;gap:8px;padding-bottom:18px}
.reply-history{max-height:260px;overflow:auto;padding-top:4px}
.history-title{display:flex;align-items:center;gap:8px;font-size:13px}
.history-content{white-space:pre-wrap;line-height:1.65;margin-top:6px;color:#303133}
.sla{display:flex;flex-wrap:wrap;gap:4px}
@media(max-width:768px){.toolbar{flex-wrap:wrap}.toolbar .el-input{max-width:none;flex:1 1 100%}}
</style>
