<template>
  <div class="page">
    <div class="page-toolbar">
      <div class="pt-left">
        <span class="pt-title">命令模版</span>
        <el-text type="info" size="small">自定义 WebSSH 快捷命令，支持 {参数} 占位符，在 WebSSH 终端一键下发。</el-text>
      </div>
      <el-button type="primary" :icon="Plus" @click="openEdit()">新增模版</el-button>
    </div>

    <div class="filters">
      <el-input v-model="search" placeholder="搜索名称 / 编码 / 命令" :prefix-icon="Search" clearable
        style="width:280px" @keyup.enter="load" @clear="load" />
      <el-select v-model="filterCategory" placeholder="全部分类" clearable size="default"
        style="width:140px;margin-left:12px" @change="load">
        <el-option v-for="c in categoryOptions" :key="c" :label="c" :value="c" />
      </el-select>
      <el-checkbox v-model="showInactive" style="margin-left:12px" @change="load">显示已停用</el-checkbox>
    </div>

    <el-table v-loading="loading" :data="list" border stripe>
      <el-table-column prop="code" label="编码" width="160" show-overflow-tooltip>
        <template #default="{ row }">
          <el-text type="primary">{{ row.code }}</el-text>
          <el-tag v-if="row.is_system" size="small" type="info" effect="plain" style="margin-left:4px">内置</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="name" label="名称" min-width="140" />
      <el-table-column prop="category" label="分类" width="90" />
      <el-table-column prop="description" label="说明" min-width="180" show-overflow-tooltip />
      <el-table-column prop="command" label="命令" min-width="220" show-overflow-tooltip />
      <el-table-column label="参数" width="70" align="center">
        <template #default="{ row }">{{ (row.fields || []).length }}</template>
      </el-table-column>
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'info'" size="small">{{ row.is_active ? '启用' : '停用' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="sort_order" label="排序" width="70" align="center" />
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
          <el-button link type="danger" size="small" :disabled="row.is_system" @click="doDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" :title="editRow ? '编辑模版' : '新增模版'" width="760px" destroy-on-close>
      <el-form :model="form" label-width="96px">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="编码" required>
              <el-input v-model="form.code" :disabled="!!editRow?.is_system" placeholder="如 custom_ping" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="名称" required>
              <el-input v-model="form.name" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="分类">
              <el-select v-model="form.category" filterable allow-create default-first-option style="width:100%">
                <el-option v-for="c in categoryOptions" :key="c" :label="c" :value="c" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="6">
            <el-form-item label="排序">
              <el-input-number v-model="form.sort_order" :min="0" :step="10" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :span="6">
            <el-form-item label="启用">
              <el-switch v-model="form.is_active" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="说明">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="命令" required>
          <el-input v-model="form.command" type="textarea" :rows="3"
            placeholder="如 ping -c 4 {host} 或 iperf3 -c {server} -p {port}" />
        </el-form-item>
        <el-form-item label="交互命令">
          <el-switch v-model="form.interactive" />
          <el-text type="info" size="small" style="margin-left:8px">如 passwd，需在终端继续输入</el-text>
        </el-form-item>

        <el-divider content-position="left">参数字段（命令中用 {key} 引用）</el-divider>
        <div class="field-list">
          <div v-for="(field, idx) in form.fields" :key="idx" class="field-row">
            <el-input v-model="field.key" placeholder="key" style="width:120px" />
            <el-input v-model="field.label" placeholder="显示名" style="width:120px" />
            <el-input v-model="field.default" placeholder="默认值" style="flex:1" />
            <el-checkbox v-model="field.required">必填</el-checkbox>
            <el-button link type="danger" @click="removeField(idx)">删除</el-button>
          </div>
          <el-button :icon="Plus" @click="addField">添加参数</el-button>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search } from '@element-plus/icons-vue'
import {
  getWebSSHCommandTemplates,
  createWebSSHCommandTemplate,
  updateWebSSHCommandTemplate,
  deleteWebSSHCommandTemplate,
} from '@/api/operation'

const loading = ref(false)
const saving = ref(false)
const list = ref([])
const search = ref('')
const filterCategory = ref('')
const showInactive = ref(false)
const dialogVisible = ref(false)
const editRow = ref(null)

const defaultCategories = ['系统', '网络', '维护', '自定义']
const form = reactive({
  code: '',
  name: '',
  category: '自定义',
  description: '',
  command: '',
  fields: [],
  interactive: false,
  is_active: true,
  sort_order: 100,
})

const categoryOptions = computed(() => {
  const set = new Set(defaultCategories)
  list.value.forEach((row) => { if (row.category) set.add(row.category) })
  return [...set]
})

function resetForm() {
  form.code = ''
  form.name = ''
  form.category = '自定义'
  form.description = ''
  form.command = ''
  form.fields = []
  form.interactive = false
  form.is_active = true
  form.sort_order = 100
}

async function load() {
  loading.value = true
  try {
    const params = { page_size: 200, ordering: 'sort_order,id' }
    if (search.value) params.search = search.value
    if (filterCategory.value) params.category = filterCategory.value
    if (!showInactive.value) params.is_active = true
    const res = await getWebSSHCommandTemplates(params)
    list.value = res.results || res || []
  } catch (e) {
    ElMessage.error('加载失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

function openEdit(row) {
  editRow.value = row || null
  resetForm()
  if (row) {
    form.code = row.code
    form.name = row.name
    form.category = row.category || '自定义'
    form.description = row.description || ''
    form.command = row.command || ''
    form.fields = (row.fields || []).map((f) => ({ ...f }))
    form.interactive = !!row.interactive
    form.is_active = row.is_active !== false
    form.sort_order = row.sort_order ?? 100
  }
  dialogVisible.value = true
}

function addField() {
  form.fields.push({ key: '', label: '', default: '', required: false })
}

function removeField(idx) {
  form.fields.splice(idx, 1)
}

async function save() {
  if (!form.code.trim() || !form.name.trim() || !form.command.trim()) {
    ElMessage.warning('请填写编码、名称和命令')
    return
  }
  saving.value = true
  const payload = {
    code: form.code.trim(),
    name: form.name.trim(),
    category: form.category || '自定义',
    description: form.description,
    command: form.command.trim(),
    fields: form.fields.filter((f) => f.key?.trim()).map((f) => ({
      key: f.key.trim(),
      label: (f.label || f.key).trim(),
      default: f.default ?? '',
      required: !!f.required,
    })),
    interactive: form.interactive,
    is_active: form.is_active,
    sort_order: form.sort_order,
  }
  try {
    if (editRow.value) {
      await updateWebSSHCommandTemplate(editRow.value.id, payload)
      ElMessage.success('已更新')
    } else {
      await createWebSSHCommandTemplate(payload)
      ElMessage.success('已创建')
    }
    dialogVisible.value = false
    await load()
  } catch (e) {
    const data = e?.response?.data
    const msg = data?.detail || Object.values(data || {}).flat().join('；') || e.message
    ElMessage.error('保存失败：' + msg)
  } finally {
    saving.value = false
  }
}

async function doDelete(row) {
  try {
    await ElMessageBox.confirm(`确定删除模版「${row.name}」？`, '确认删除', { type: 'warning' })
    await deleteWebSSHCommandTemplate(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (e) {
    if (e !== 'cancel') {
      ElMessage.error('删除失败：' + (e?.response?.data?.detail || e.message))
    }
  }
}

onMounted(load)
</script>

<style scoped>
.page { background: #fff; border-radius: 8px; padding: 16px 20px; min-height: 100%; }
.page-toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.pt-left { display: flex; flex-direction: column; gap: 4px; }
.pt-title { font-size: 18px; font-weight: 700; color: #1a1a2e; }
.filters { display: flex; align-items: center; margin-bottom: 12px; flex-wrap: wrap; gap: 8px; }
.field-list { display: flex; flex-direction: column; gap: 8px; }
.field-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
</style>
