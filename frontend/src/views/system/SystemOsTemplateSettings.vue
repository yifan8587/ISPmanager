<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <h2 class="page-title">系统模版</h2>
        <p class="page-desc">
          维护 VPS 系统名称、版本与发行版 Cloud 镜像（.qcow2 / .img）。产品管理与门户下单共用此目录；开通时若 POP 没有对应版本，会按 URL 下载到节点 /var/lib/vz/template/iso/。可导入官方目录或从指定 POP 同步；已配好的 Cloud URL 不会被覆盖，同名的 .vma.zst / PVE 备份会就地升级。
        </p>
      </div>
      <div class="header-actions">
        <el-select v-if="canManage" v-model="syncPopId" filterable placeholder="选择 POP 同步 Cloud 目录" style="width:240px">
          <el-option v-for="item in pops" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-button v-if="canManage" :loading="seeding" @click="seedDefaults">导入官方 Cloud 目录</el-button>
        <el-button v-if="canManage" :loading="syncing" @click="syncFromPop">从 POP 同步</el-button>
        <el-button v-if="canManage" type="primary" :icon="Plus" @click="openCreate">新增系统模版</el-button>
      </div>
    </div>

    <el-card shadow="never">
      <el-table :data="templates" v-loading="loading" border stripe>
        <el-table-column prop="name" label="系统名称" min-width="140" />
        <el-table-column prop="version" label="版本" width="120" />
        <el-table-column label="默认用户" width="110">
          <template #default="{ row }">{{ row.default_user || '—' }}</template>
        </el-table-column>
        <el-table-column label="系统类型" width="130">
          <template #default="{ row }">{{ row.os_type_label || row.os_type }}</template>
        </el-table-column>
        <el-table-column label="类型" width="130">
          <template #default="{ row }">
            <el-tag size="small" :type="row.is_stale ? 'danger' : (row.source_type === 'cloud' ? 'success' : 'info')">
              {{ row.is_stale ? '待升级备份' : (row.source_type_label || row.source_type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="backup_filename" label="镜像文件名" min-width="220" show-overflow-tooltip />
        <el-table-column prop="download_url" label="下载地址" min-width="260" show-overflow-tooltip />
        <el-table-column label="启用" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="row.enabled ? 'success' : 'info'">
              {{ row.enabled ? '已启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="sort_order" label="排序" width="80" />
        <el-table-column v-if="canManage" label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="removeTemplate(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!templates.length && !loading" description="尚未配置系统模版" />
    </el-card>

    <el-dialog
      v-model="dialogVisible"
      :title="editing ? '编辑系统模版' : '新增系统模版'"
      width="640px"
      destroy-on-close
    >
      <el-form ref="formRef" :model="form" :rules="rules" label-width="120px">
        <el-form-item label="系统名称" prop="name">
          <el-input v-model="form.name" maxlength="128" placeholder="例如：Ubuntu" />
        </el-form-item>
        <el-form-item label="版本" prop="version">
          <el-input v-model="form.version" maxlength="64" placeholder="例如：22.04" />
        </el-form-item>
        <el-form-item label="系统类型" prop="os_type">
          <el-select v-model="form.os_type" style="width:100%">
            <el-option v-for="item in osTypes" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="镜像文件名" prop="backup_filename">
          <el-input v-model="form.backup_filename" maxlength="255" placeholder="例如：noble-server-cloudimg-amd64.img，可从 URL 自动推断" />
        </el-form-item>
        <el-form-item label="下载地址" prop="download_url">
          <el-input v-model="form.download_url" maxlength="512" placeholder="https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img" />
        </el-form-item>
        <el-form-item label="文件类型">
          <el-select v-model="form.source_type" style="width:100%">
            <el-option label="Cloud 镜像" value="cloud" />
            <el-option label="LXC 模板" value="vztmpl" />
            <el-option label="ISO 安装盘" value="iso" />
          </el-select>
        </el-form-item>
        <el-form-item label="默认用户">
          <el-input :model-value="inferredUser" disabled placeholder="按系统名称自动推断，开通时写入 cloud-init" />
        </el-form-item>
        <el-form-item label="模版代码">
          <el-input v-model="form.code" maxlength="32" placeholder="留空则按名称和版本自动生成" />
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="form.sort_order" :min="0" controls-position="right" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" maxlength="255" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveTemplate">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { usePermission } from '@/composables/usePermission'
import {
  createOsTemplate,
  deleteOsTemplate,
  getOsTemplateTypes,
  getOsTemplates,
  seedOsTemplates,
  syncOsTemplatesFromPop,
  updateOsTemplate,
} from '@/api/system'
import { getPopPoints } from '@/api/resource'

const { canManagePageAction } = usePermission()
const canManage = computed(() => canManagePageAction('system', 'os_template'))
const loading = ref(false)
const saving = ref(false)
const syncing = ref(false)
const seeding = ref(false)
const syncPopId = ref(null)
const pops = ref([])
const templates = ref([])
const osTypes = ref([
  { value: 'ubuntu', label: 'Ubuntu' },
  { value: 'debian', label: 'Debian' },
  { value: 'centos', label: 'CentOS' },
  { value: 'rhel', label: 'RHEL' },
  { value: 'windows', label: 'Windows Server' },
  { value: 'other', label: '其他' },
])
const dialogVisible = ref(false)
const editing = ref(null)
const formRef = ref(null)
const form = reactive(emptyForm())
const inferredUser = computed(() => {
  const text = `${form.name} ${form.backup_filename} ${form.code}`.toLowerCase()
  if (text.includes('ubuntu')) return 'ubuntu'
  if (text.includes('debian')) return 'debian'
  if (text.includes('alma')) return 'almalinux'
  if (text.includes('rocky')) return 'rocky'
  if (/(centos|rhel|fedora)/.test(text)) return 'cloud-user'
  return 'ubuntu'
})
const rules = {
  name: [{ required: true, message: '请输入系统名称', trigger: 'blur' }],
  version: [{ required: true, message: '请输入版本', trigger: 'blur' }],
  download_url: [
    { required: true, message: '请输入下载地址', trigger: 'blur' },
    { type: 'url', message: '请输入合法 URL，例如 https://cloud-images.ubuntu.com/.../noble-server-cloudimg-amd64.img', trigger: 'blur' },
  ],
}

function emptyForm() {
  return {
    name: '',
    version: '',
    backup_filename: '',
    download_url: '',
    code: '',
    os_type: 'other',
    source_type: 'cloud',
    enabled: true,
    sort_order: 0,
    remark: '',
  }
}

async function loadData() {
  loading.value = true
  try {
    const [listRes, typeRes, popRes] = await Promise.all([
      getOsTemplates({ page_size: 200 }),
      getOsTemplateTypes().catch(() => null),
      getPopPoints({ page_size: 200 }).catch(() => null),
    ])
    templates.value = listRes?.results || listRes || []
    pops.value = popRes?.results || popRes || []
    if (!syncPopId.value && pops.value.length) syncPopId.value = pops.value[0].id
    if (Array.isArray(typeRes) && typeRes.length) osTypes.value = typeRes
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '加载系统模版失败')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  Object.assign(form, emptyForm())
  dialogVisible.value = true
}

function openEdit(row) {
  editing.value = row
  Object.assign(form, {
    name: row.name,
    version: row.version,
    backup_filename: row.backup_filename,
    download_url: row.download_url,
    code: row.code,
    os_type: row.os_type || 'other',
    source_type: row.source_type || 'cloud',
    enabled: Boolean(row.enabled),
    sort_order: row.sort_order || 0,
    remark: row.remark || '',
  })
  dialogVisible.value = true
}

async function saveTemplate() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    const payload = { ...form }
    if (!payload.code) delete payload.code
    if (editing.value) {
      await updateOsTemplate(editing.value.id, payload)
      ElMessage.success('已更新')
    } else {
      await createOsTemplate(payload)
      ElMessage.success('已创建')
    }
    dialogVisible.value = false
    await loadData()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || formatErrors(error) || '保存失败')
  } finally {
    saving.value = false
  }
}

function formatErrors(error) {
  const data = error?.response?.data
  if (!data || typeof data !== 'object') return ''
  if (typeof data.detail === 'string') return data.detail
  return Object.entries(data).map(([key, value]) => `${key}: ${[].concat(value).join('、')}`).join('；')
}

async function syncFromPop() {
  if (!syncPopId.value) {
    ElMessage.warning('请选择要同步的 POP 点')
    return
  }
  syncing.value = true
  try {
    const res = await syncOsTemplatesFromPop({ pop_id: syncPopId.value })
    ElMessage.success(formatSyncMessage(res, res?.pop_name || 'POP'))
    await loadData()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '同步失败')
  } finally {
    syncing.value = false
  }
}

function formatSyncMessage(res, source) {
  const catalog = res?.catalog_count || 0
  const created = res?.created_count || 0
  const upgraded = res?.upgraded_count || 0
  if (created || upgraded) {
    const parts = [`已从 ${source} 核对 ${catalog} 个 Cloud 版本`]
    if (created) parts.push(`新增 ${created} 条`)
    if (upgraded) parts.push(`升级 ${upgraded} 条旧备份`)
    return parts.join('，')
  }
  return `已核对 ${source} 的 ${catalog} 个 Cloud 版本，没有新模版`
}

async function seedDefaults() {
  seeding.value = true
  try {
    const res = await seedOsTemplates()
    ElMessage.success(formatSyncMessage(res, '官方 Cloud 目录'))
    await loadData()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '导入官方目录失败')
  } finally {
    seeding.value = false
  }
}

async function removeTemplate(row) {
  try {
    await ElMessageBox.confirm(`确定删除系统模版「${row.label || `${row.name} ${row.version}`}」？`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await deleteOsTemplate(row.id)
    ElMessage.success('已删除')
    await loadData()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '删除失败')
  }
}

onMounted(loadData)
</script>

<style scoped>
.page-desc {
  margin: 6px 0 0;
  color: #909399;
  font-size: 13px;
  line-height: 1.5;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
