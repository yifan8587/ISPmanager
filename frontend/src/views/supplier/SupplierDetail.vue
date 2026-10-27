<template>
  <div v-loading="loading && !current" class="supplier-detail-page">
    <!-- ── 标题栏 ── -->
    <div class="page-header">
      <el-button :icon="ArrowLeft" @click="goBack">返回</el-button>
      <template v-if="current">
        <span class="ph-name">{{ current.name }}</span>
        <el-tag size="small" :type="statusColor[current.status]">{{ current.status_label || current.status }}</el-tag>
        <el-tag size="small" type="info">{{ current.supplier_type_label || current.supplier_type }}</el-tag>
      </template>
      <div class="ph-actions" v-if="current">
        <el-button size="small" :icon="Refresh" :loading="loading" @click="reload">刷新</el-button>
        <el-button size="small" type="primary" @click="goEdit">编辑</el-button>
      </div>
    </div>

    <el-tabs v-if="current" v-model="activeTab">
      <el-tab-pane label="API 管理" name="api">
        <div class="rules-head">
          <span class="muted">配置供应商对接 API，可测试连通并自动拉取资源</span>
          <el-button size="small" type="primary" :icon="Plus" @click="openApi()">新增API</el-button>
        </div>
        <el-table :data="apis" size="small" border v-loading="apiLoading">
          <el-table-column prop="name" label="名称" width="120" />
          <el-table-column prop="base_url" label="API基址" min-width="180" show-overflow-tooltip />
          <el-table-column prop="auth_type_label" label="认证" width="110" />
          <el-table-column label="自动同步" width="90"><template #default="{ row }"><el-tag size="small" :type="row.sync_enabled ? 'success' : 'info'">{{ row.sync_enabled ? '开' : '关' }}</el-tag></template></el-table-column>
          <el-table-column label="最近同步" width="110"><template #default="{ row }"><el-tag size="small" :type="syncColor[row.last_sync_status]">{{ row.last_sync_status_label }}</el-tag></template></el-table-column>
          <el-table-column label="操作" width="220">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="testApi(row)">测试</el-button>
              <el-button link type="success" size="small" @click="pullApi(row)">拉取资源</el-button>
              <el-button link type="primary" size="small" @click="openApi(row)">编辑</el-button>
              <el-button link type="danger" size="small" @click="removeApi(row)">删除</el-button>
            </template>
          </el-table-column>
          <template #empty>暂无 API，请新增</template>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="POP 关联" name="pop">
        <div class="rules-head">
          <span class="muted">供应商与本地 POP 点关联，用于资源/产品对应同步</span>
          <el-button size="small" type="primary" :icon="Plus" @click="openBinding()">新增关联</el-button>
        </div>
        <el-table :data="bindings" size="small" border v-loading="bindingLoading">
          <el-table-column prop="pop_name" label="POP点" min-width="150" />
          <el-table-column prop="external_pop_id" label="供应商侧标识" min-width="140" />
          <el-table-column label="状态" width="80"><template #default="{ row }"><el-tag size="small" :type="row.is_active ? 'success' : 'info'">{{ row.is_active ? '生效' : '停用' }}</el-tag></template></el-table-column>
          <el-table-column label="操作" width="90">
            <template #default="{ row }"><el-button link type="danger" size="small" @click="removeBinding(row)">删除</el-button></template>
          </el-table-column>
          <template #empty>暂无关联</template>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="资源" name="resource">
        <el-table :data="resources" size="small" border v-loading="resLoading">
          <el-table-column prop="external_id" label="资源ID" width="120" />
          <el-table-column prop="name" label="名称" min-width="130" />
          <el-table-column prop="resource_type_label" label="类型" width="90" />
          <el-table-column label="规格" min-width="160">
            <template #default="{ row }">{{ row.cpu_cores || '-' }}核/{{ ramText(row.ram_mb) }}/{{ row.disk_gb || '-' }}G/{{ row.bandwidth_mbps || '-' }}M</template>
          </el-table-column>
          <el-table-column prop="sync_status_label" label="状态" width="90" />
          <template #empty>暂无资源（可在 API 管理中拉取）</template>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="账号管理" name="account">
        <div class="rules-head">
          <span class="muted">供应商账号必须绑定本供应商，登录后仅可维护本供应商产品、POP、IP 与隧道，并查看账单结算</span>
          <div>
            <el-button size="small" @click="openBindAccount()">关联已有账号</el-button>
            <el-button size="small" type="primary" :icon="Plus" @click="openAccount()">新增账号</el-button>
          </div>
        </div>
        <el-table :data="accounts" size="small" border v-loading="accountLoading">
          <el-table-column prop="username" label="用户名" width="140" />
          <el-table-column prop="email" label="邮箱" min-width="160" />
          <el-table-column prop="first_name" label="姓名" width="100" />
          <el-table-column label="状态" width="80">
            <template #default="{ row }">
              <el-tag size="small" :type="row.is_active ? 'success' : 'info'">{{ row.is_active ? '启用' : '停用' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="last_login" label="最近登录" width="170" />
          <el-table-column label="操作" width="240">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="resetAccountPwd(row)">重置密码</el-button>
              <el-button v-if="row.is_active" link type="danger" size="small" @click="disableAccount(row)">停用</el-button>
              <el-button link type="warning" size="small" @click="unbindAccount(row)">解除关联</el-button>
            </template>
          </el-table-column>
          <template #empty>暂无供应商账号</template>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="同步日志" name="logs">
        <el-timeline>
          <el-timeline-item v-for="log in syncLogs" :key="log.id" :timestamp="log.created_at" placement="top">
            [{{ log.action_label }}] {{ log.result_label }} — 收 {{ log.received_count }} / 新增 {{ log.created_count }} / 更新 {{ log.updated_count }}
            <div class="muted">{{ log.message }}</div>
          </el-timeline-item>
        </el-timeline>
      </el-tab-pane>
    </el-tabs>

    <!-- API 编辑 -->
    <el-dialog v-model="apiVisible" :title="apiEdit ? '编辑API' : '新增API'" width="660px" destroy-on-close>
      <el-form ref="apiRef" :model="apiForm" :rules="apiRules" label-width="120px">
        <el-form-item label="名称" prop="name"><el-input v-model="apiForm.name" /></el-form-item>
        <el-form-item label="API基址" prop="base_url"><el-input v-model="apiForm.base_url" placeholder="https://api.vendor.com" /></el-form-item>
        <el-form-item label="认证方式">
          <el-select v-model="apiForm.auth_type" style="width:100%">
            <el-option v-for="a in authTypes" :key="a.value" :label="a.label" :value="a.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="API Key/Token"><el-input v-model="apiForm.api_key" :placeholder="apiEdit && apiEdit.has_secret ? '已设置，留空不修改' : ''" show-password /></el-form-item>
        <el-form-item v-if="apiForm.auth_type === 'apikey'" label="自定义头名"><el-input v-model="apiForm.api_header_name" placeholder="X-Api-Key" /></el-form-item>
        <el-form-item v-if="apiForm.auth_type === 'basic'" label="用户名"><el-input v-model="apiForm.username" /></el-form-item>
        <el-form-item v-if="apiForm.auth_type === 'basic'" label="密码"><el-input v-model="apiForm.password" show-password /></el-form-item>
        <el-form-item label="资源列表路径"><el-input v-model="apiForm.resources_path" placeholder="/api/resources/" /></el-form-item>
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item label="校验SSL"><el-switch v-model="apiForm.verify_ssl" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="启用自动同步"><el-switch v-model="apiForm.sync_enabled" /></el-form-item></el-col>
        </el-row>
      </el-form>
      <template #footer>
        <el-button @click="apiVisible = false">取消</el-button>
        <el-button type="primary" :loading="apiSaving" @click="saveApi">确认</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="bindVisible" title="关联已有账号" width="440px" destroy-on-close>
      <el-form ref="bindRef" :model="bindForm" :rules="bindRules" label-width="88px">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="bindForm.username" placeholder="系统中已有的用户名" />
        </el-form-item>
      </el-form>
      <p class="muted">将该系统账号绑定为本供应商门户账号，之后仅能访问本供应商数据。</p>
      <template #footer>
        <el-button @click="bindVisible = false">取消</el-button>
        <el-button type="primary" :loading="bindSaving" @click="saveBindAccount">确认关联</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="accountVisible" title="新增供应商账号" width="480px" destroy-on-close>
      <el-form ref="accountRef" :model="accountForm" :rules="accountRules" label-width="88px">
        <el-form-item label="用户名" prop="username"><el-input v-model="accountForm.username" /></el-form-item>
        <el-form-item label="密码" prop="password"><el-input v-model="accountForm.password" type="password" show-password /></el-form-item>
        <el-form-item label="姓名"><el-input v-model="accountForm.first_name" /></el-form-item>
        <el-form-item label="邮箱"><el-input v-model="accountForm.email" /></el-form-item>
        <el-form-item label="电话"><el-input v-model="accountForm.phone" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="accountVisible = false">取消</el-button>
        <el-button type="primary" :loading="accountSaving" @click="saveAccount">确认</el-button>
      </template>
    </el-dialog>

    <!-- POP 关联编辑 -->
    <el-dialog v-model="bindingVisible" title="新增POP关联" width="520px" destroy-on-close>
      <el-form ref="bindingRef" :model="bindingForm" :rules="bindingRules" label-width="120px">
        <el-form-item label="POP点" prop="pop_point">
          <el-select v-model="bindingForm.pop_point" filterable placeholder="选择POP点" style="width:100%">
            <el-option v-for="p in popOptions" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="供应商侧标识"><el-input v-model="bindingForm.external_pop_id" /></el-form-item>
        <el-form-item label="供应商侧名称"><el-input v-model="bindingForm.external_pop_name" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="bindingVisible = false">取消</el-button>
        <el-button type="primary" :loading="bindingSaving" @click="saveBinding">确认</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed } from 'vue'
import { ramText } from '@/utils/ram'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, ArrowLeft } from '@element-plus/icons-vue'
import {
  getSupplier,
  getSupplierAPIs, createSupplierAPI, updateSupplierAPI, deleteSupplierAPI, testSupplierAPI, pullSupplierResources,
  getPopBindings, createPopBinding, deletePopBinding,
  getSupplierResources, getSupplierSyncLogs,
  getSupplierAccounts, createSupplierAccount, bindSupplierAccount, unbindSupplierAccount,
  resetSupplierAccountPassword, disableSupplierAccount,
} from '@/api/supplier'
import { getPopPoints } from '@/api/resource'

const route = useRoute()
const router = useRouter()
const supplierId = computed(() => route.params.id)

const loading = ref(false)
const activeTab = ref('api')
const current = ref(null)
const popOptions = ref([])
const apis = ref([])
const apiLoading = ref(false)
const bindings = ref([])
const bindingLoading = ref(false)
const resources = ref([])
const resLoading = ref(false)
const syncLogs = ref([])
const accounts = ref([])
const accountLoading = ref(false)
const accountVisible = ref(false)
const accountSaving = ref(false)
const accountRef = ref(null)
const accountForm = reactive({ username: '', password: '', email: '', first_name: '', phone: '' })
const accountRules = {
  username: [{ required: true, message: '请输入用户名' }],
  password: [{ required: true, message: '请输入密码' }, { min: 8, message: '至少 8 位' }],
}

const statusColor = { active: 'success', suspended: 'warning', disabled: 'info', pending: '' }
const authTypes = [
  { value: 'none', label: '无认证' }, { value: 'token', label: 'Token' }, { value: 'bearer', label: 'Bearer(JWT)' },
  { value: 'basic', label: 'Basic' }, { value: 'apikey', label: 'API Key(自定义头)' },
]
const syncColor = { never: 'info', success: 'success', partial: 'warning', failed: 'danger' }

function goBack() {
  router.push('/supplier/list')
}
function goEdit() {
  if (current.value) router.push({ path: '/supplier/list', query: { edit: current.value.id } })
}

async function reload() {
  if (!supplierId.value) return
  loading.value = true
  try {
    current.value = await getSupplier(supplierId.value)
    await refreshData()
  } catch (e) {
    ElMessage.error('加载失败：' + (e?.response?.data?.detail || e.message))
  } finally { loading.value = false }
}

async function refreshData() {
  const sid = supplierId.value
  apiLoading.value = true; bindingLoading.value = true; resLoading.value = true; accountLoading.value = true
  try {
    const [a, b, r, l, acc] = await Promise.all([
      getSupplierAPIs({ supplier: sid, page_size: 100 }).catch(() => null),
      getPopBindings({ supplier: sid, page_size: 100 }).catch(() => null),
      getSupplierResources(sid, { page_size: 100 }).catch(() => null),
      getSupplierSyncLogs(sid).catch(() => []),
      getSupplierAccounts(sid).catch(() => []),
    ])
    apis.value = a?.results || a || []
    bindings.value = b?.results || b || []
    resources.value = r?.results || r || []
    syncLogs.value = l || []
    accounts.value = acc?.results || acc || []
  } finally { apiLoading.value = false; bindingLoading.value = false; resLoading.value = false; accountLoading.value = false }
}

// ── API 管理 ──────────────────────────────────────────────
const apiVisible = ref(false)
const apiSaving = ref(false)
const apiRef = ref(null)
const apiEdit = ref(null)
const apiForm = reactive({
  name: '默认接口', base_url: '', auth_type: 'token', api_key: '', api_header_name: '',
  username: '', password: '', resources_path: '/api/resources/', verify_ssl: true, sync_enabled: false,
})
const apiRules = {
  name: [{ required: true, message: '请输入名称' }],
  base_url: [{ required: true, message: '请输入API基址' }],
}

function openApi(row = null) {
  apiEdit.value = row
  if (row) {
    Object.assign(apiForm, {
      name: row.name, base_url: row.base_url, auth_type: row.auth_type, api_key: '', api_header_name: row.api_header_name || '',
      username: row.username || '', password: '', resources_path: row.resources_path || '/api/resources/',
      verify_ssl: row.verify_ssl, sync_enabled: row.sync_enabled,
    })
  } else {
    Object.assign(apiForm, {
      name: '默认接口', base_url: '', auth_type: 'token', api_key: '', api_header_name: '',
      username: '', password: '', resources_path: '/api/resources/', verify_ssl: true, sync_enabled: false,
    })
  }
  apiVisible.value = true
}

async function saveApi() {
  const valid = await apiRef.value.validate().catch(() => false)
  if (!valid) return
  apiSaving.value = true
  try {
    const payload = { ...apiForm, supplier: supplierId.value }
    if (apiEdit.value) { await updateSupplierAPI(apiEdit.value.id, payload); ElMessage.success('已更新') }
    else { await createSupplierAPI(payload); ElMessage.success('已新增') }
    apiVisible.value = false
    refreshData()
  } finally { apiSaving.value = false }
}

async function removeApi(row) {
  await ElMessageBox.confirm(`确定删除API "${row.name}"？`, '警告', { type: 'warning' })
  await deleteSupplierAPI(row.id)
  ElMessage.success('已删除')
  refreshData()
}

async function testApi(row) {
  try {
    const res = await testSupplierAPI(row.id)
    res?.success ? ElMessage.success(res.message || '连接成功') : ElMessage.warning(res?.message || '连接失败')
  } catch (e) { ElMessage.error(e?.response?.data?.message || '连接失败') }
}

async function pullApi(row) {
  try {
    const res = await pullSupplierResources(row.id, {})
    ElMessage.success(res?.message || '拉取完成')
    refreshData()
  } catch (e) { ElMessage.error(e?.response?.data?.detail || '拉取失败') }
}

const bindVisible = ref(false)
const bindSaving = ref(false)
const bindRef = ref(null)
const bindForm = reactive({ username: '' })
const bindRules = { username: [{ required: true, message: '请输入用户名' }] }

function openBindAccount() {
  bindForm.username = ''
  bindVisible.value = true
}

async function saveBindAccount() {
  const valid = await bindRef.value.validate().catch(() => false)
  if (!valid) return
  bindSaving.value = true
  try {
    await bindSupplierAccount(supplierId.value, { username: bindForm.username })
    ElMessage.success('已关联到本供应商')
    bindVisible.value = false
    refreshData()
  } catch (e) {
    ElMessage.error(e?.response?.data?.username?.[0] || e?.response?.data?.detail || '关联失败')
  } finally { bindSaving.value = false }
}

async function unbindAccount(row) {
  await ElMessageBox.confirm(`确定解除 ${row.username} 与本供应商的关联？解除后该账号将不再能访问供应商门户数据。`, '警告', { type: 'warning' })
  await unbindSupplierAccount(supplierId.value, { user_id: row.id })
  ElMessage.success('已解除关联')
  refreshData()
}

function openAccount() {
  Object.assign(accountForm, { username: '', password: '', email: '', first_name: '', phone: '' })
  accountVisible.value = true
}

async function saveAccount() {
  const valid = await accountRef.value.validate().catch(() => false)
  if (!valid) return
  accountSaving.value = true
  try {
    await createSupplierAccount(supplierId.value, { ...accountForm })
    ElMessage.success('已创建供应商账号')
    accountVisible.value = false
    refreshData()
  } catch (e) {
    ElMessage.error(e?.response?.data?.username?.[0] || e?.response?.data?.detail || '创建失败')
  } finally { accountSaving.value = false }
}

async function resetAccountPwd(row) {
  const { value } = await ElMessageBox.prompt(`为 ${row.username} 设置新密码`, '重置密码', {
    inputType: 'password', inputPlaceholder: '至少 8 位',
  })
  if (!value) return
  await resetSupplierAccountPassword(row.id, { password: value })
  ElMessage.success('密码已重置')
}

async function disableAccount(row) {
  await ElMessageBox.confirm(`确定停用账号 ${row.username}？`, '警告', { type: 'warning' })
  await disableSupplierAccount(row.id)
  ElMessage.success('已停用')
  refreshData()
}

// ── POP 关联 ──────────────────────────────────────────────
const bindingVisible = ref(false)
const bindingSaving = ref(false)
const bindingRef = ref(null)
const bindingForm = reactive({ pop_point: null, external_pop_id: '', external_pop_name: '' })
const bindingRules = { pop_point: [{ required: true, message: '请选择POP点' }] }

function openBinding() {
  Object.assign(bindingForm, { pop_point: null, external_pop_id: '', external_pop_name: '' })
  bindingVisible.value = true
}

async function saveBinding() {
  const valid = await bindingRef.value.validate().catch(() => false)
  if (!valid) return
  bindingSaving.value = true
  try {
    await createPopBinding({ ...bindingForm, supplier: supplierId.value })
    ElMessage.success('已新增关联')
    bindingVisible.value = false
    refreshData()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败（可能已关联）')
  } finally { bindingSaving.value = false }
}

async function removeBinding(row) {
  await ElMessageBox.confirm('确定删除该关联？', '警告', { type: 'warning' })
  await deletePopBinding(row.id)
  ElMessage.success('已删除')
  refreshData()
}

onMounted(async () => {
  const pops = await getPopPoints({ page_size: 200 }).catch(() => null)
  popOptions.value = pops?.results || pops || []
  reload()
})
</script>

<style scoped>
.supplier-detail-page { background: #fff; border-radius: 8px; padding: 16px 20px 24px; min-height: calc(100vh - 120px); }
.page-header { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; padding-bottom: 14px; border-bottom: 1px solid #ebeef5; }
.ph-name { font-size: 18px; font-weight: 700; color: #1a1a2e; }
.ph-actions { margin-left: auto; display: flex; gap: 8px; }
.muted { color: #909399; font-size: 12px; }
.rules-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
</style>
