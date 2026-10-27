<template>
  <div class="page">
    <div class="page-toolbar">
      <div class="pt-left">
        <span class="pt-title">Zabbix 服务器</span>
        <el-text type="info" size="small">配置对接的 Zabbix API 服务器，用于采集监控项与图形数据。</el-text>
      </div>
      <el-button type="primary" :icon="Plus" @click="openEdit()">新增服务器</el-button>
    </div>

    <el-table v-loading="loading" :data="list" border stripe size="default">
      <el-table-column prop="name" label="名称" min-width="140">
        <template #default="{ row }">
          {{ row.name }}
          <el-tag v-if="row.is_default" size="small" type="warning" effect="plain" style="margin-left:4px;">默认</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="api_url" label="API 地址" min-width="240" show-overflow-tooltip />
      <el-table-column prop="auth_type_label" label="认证方式" width="120" />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
            {{ row.is_active ? '启用' : '停用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="最近连接" width="170">
        <template #default="{ row }">
          <el-tag v-if="row.last_sync_status" :type="row.last_sync_status === 'success' ? 'success' : 'danger'" size="small">
            {{ row.last_sync_status === 'success' ? '正常' : '失败' }}
          </el-tag>
          <span style="font-size:12px;color:#909399;margin-left:4px;">
            {{ row.last_sync_at ? formatDate(row.last_sync_at) : '未测试' }}
          </span>
        </template>
      </el-table-column>
      <el-table-column label="最近同步" min-width="200" show-overflow-tooltip>
        <template #default="{ row }">
          <span style="font-size:12px;color:#909399;">{{ row.last_sync_message || '尚未同步' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="320" fixed="right">
        <template #default="{ row }">
          <el-button link type="success" size="small" :loading="testingId === row.id" @click="doTest(row)">测试连接</el-button>
          <el-button link type="warning" size="small" :loading="syncingId === row.id" @click="doSync(row)">同步</el-button>
          <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
          <el-button link type="danger" size="small" @click="doDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" :title="editRow ? '编辑 Zabbix 服务器' : '新增 Zabbix 服务器'" width="560px" destroy-on-close>
      <el-form :model="form" label-width="130px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="API 地址" required>
          <el-input v-model="form.api_url" placeholder="https://zabbix.example.com/api_jsonrpc.php" />
        </el-form-item>
        <el-form-item label="认证方式">
          <el-radio-group v-model="form.auth_type">
            <el-radio value="api_token">API Token</el-radio>
            <el-radio value="user_pass">用户名密码</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.auth_type === 'api_token'" label="API Token">
          <el-input v-model="form.api_token" type="password" show-password
            :placeholder="editRow ? '留空则不修改' : '请输入 API Token'" />
        </el-form-item>
        <template v-else>
          <el-form-item label="用户名">
            <el-input v-model="form.username" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input v-model="form.password" type="password" show-password
              :placeholder="editRow ? '留空则不修改' : '请输入密码'" />
          </el-form-item>
        </template>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="默认主机组ID">
              <el-input v-model="form.default_host_group_id" placeholder="可选" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="主机组名称">
              <el-input v-model="form.default_host_group_name" placeholder="可选" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="校验 SSL">
              <el-switch v-model="form.verify_ssl" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="超时(秒)">
              <el-input-number v-model="form.timeout_sec" :min="1" :max="120" style="width:100%" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="设为默认">
              <el-switch v-model="form.is_default" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="启用">
              <el-switch v-model="form.is_active" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="doSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import {
  getZabbixServers, createZabbixServer, updateZabbixServer,
  deleteZabbixServer, testZabbixConnection, syncZabbixServer,
} from '@/api/operation'

const loading = ref(false)
const list = ref([])
const dialogVisible = ref(false)
const saving = ref(false)
const editRow = ref(null)
const testingId = ref(null)
const syncingId = ref(null)

const defaultForm = () => ({
  name: '', api_url: '', auth_type: 'api_token', api_token: '', username: '', password: '',
  default_host_group_id: '', default_host_group_name: '',
  verify_ssl: true, timeout_sec: 15, is_default: false, is_active: true, remark: '',
})
const form = reactive(defaultForm())

function formatDate(dt) {
  if (!dt) return '-'
  return new Date(dt).toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-')
}

async function load() {
  loading.value = true
  try {
    const res = await getZabbixServers()
    list.value = res?.results || res || []
  } catch (e) {
    ElMessage.error('加载失败：' + (e?.response?.data?.detail || e.message))
  } finally { loading.value = false }
}

function openEdit(row = null) {
  editRow.value = row
  Object.assign(form, defaultForm())
  if (row) {
    Object.assign(form, {
      name: row.name, api_url: row.api_url, auth_type: row.auth_type,
      api_token: '', username: row.username || '', password: '',
      default_host_group_id: row.default_host_group_id || '',
      default_host_group_name: row.default_host_group_name || '',
      verify_ssl: row.verify_ssl ?? true, timeout_sec: row.timeout_sec || 15,
      is_default: row.is_default, is_active: row.is_active, remark: row.remark || '',
    })
  }
  dialogVisible.value = true
}

async function doSave() {
  if (!form.name || !form.api_url) { ElMessage.warning('请填写名称和 API 地址'); return }
  saving.value = true
  try {
    const payload = { ...form }
    if (!payload.api_token) delete payload.api_token
    if (!payload.password) delete payload.password
    if (editRow.value) await updateZabbixServer(editRow.value.id, payload)
    else await createZabbixServer(payload)
    ElMessage.success(editRow.value ? '已更新' : '已创建')
    dialogVisible.value = false
    load()
  } catch (e) {
    ElMessage.error('保存失败：' + (e?.response?.data?.detail || JSON.stringify(e?.response?.data) || e.message))
  } finally { saving.value = false }
}

async function doTest(row) {
  testingId.value = row.id
  try {
    const res = await testZabbixConnection(row.id)
    ElMessage.success(`连接成功，Zabbix 版本 ${res.version || ''}`)
    load()
  } catch (e) {
    ElMessage.error('连接失败：' + (e?.response?.data?.detail || e.message))
  } finally { testingId.value = null }
}

async function doSync(row) {
  syncingId.value = row.id
  try {
    const res = await syncZabbixServer(row.id)
    if (res?.status === 'queued') {
      ElMessage.success(res.message || '已在后台开始同步，请稍后刷新查看进度')
    } else {
      ElMessage.success(
        `同步完成：主机 ${res.hosts}（新增 ${res.host_created}），` +
        `监控项 ${res.items_total}，图形 ${res.graphs_total}`,
      )
    }
    load()
  } catch (e) {
    ElMessage.error('同步失败：' + (e?.response?.data?.detail || e.message))
  } finally { syncingId.value = null }
}

async function doDelete(row) {
  await ElMessageBox.confirm(`确定删除 Zabbix 服务器「${row.name}」？`, '警告', { type: 'warning' })
  await deleteZabbixServer(row.id)
  ElMessage.success('已删除')
  load()
}

onMounted(load)
</script>

<style scoped>
.page { background: #fff; border-radius: 8px; padding: 16px 20px; }
.page-toolbar { display: flex; align-items: center; margin-bottom: 14px; }
.pt-left { display: flex; flex-direction: column; gap: 2px; }
.pt-title { font-size: 16px; font-weight: 700; color: #1a1a2e; }
.page-toolbar > .el-button { margin-left: auto; }
</style>
