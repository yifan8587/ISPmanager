<template>
  <div class="page-container">
    <div class="page-header">
      <h2 class="page-title">快速查找国家</h2>
      <el-button type="primary" :icon="Plus" @click="openDialog()">添加国家</el-button>
    </div>
    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="这里勾选的国家会出现在门户首页产品卡片上方。客户点击国家名后，只显示该国家的产品。显示名称留空时使用国家原名。"
      style="margin-bottom:12px"
    />
    <el-card shadow="never">
      <el-table :data="tableData" v-loading="loading" stripe border>
        <el-table-column prop="sort_order" label="排序" width="80" />
        <el-table-column prop="region_name" label="大区" width="120" />
        <el-table-column prop="country_name" label="国家" min-width="140" />
        <el-table-column label="门户显示名" min-width="140">
          <template #default="{ row }">{{ row.label }}</template>
        </el-table-column>
        <el-table-column label="门户显示" width="100">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'" size="small">{{ row.is_active ? '显示' : '隐藏' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDialog(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="removeRow(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="dialogVisible" :title="editRow ? '编辑快速查找' : '添加快速查找'" width="480px" destroy-on-close>
      <el-form ref="formRef" :model="form" :rules="rules" label-width="96px">
        <el-form-item label="国家" prop="country">
          <el-select v-model="form.country" filterable placeholder="选择国家" style="width:100%" :disabled="!!editRow">
            <el-option
              v-for="c in countries"
              :key="c.id"
              :label="countryLabel(c)"
              :value="c.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="显示名称">
          <el-input v-model="form.display_name" placeholder="留空则使用国家名称" maxlength="64" />
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="form.sort_order" :min="0" :step="1" controls-position="right" style="width:100%" />
        </el-form-item>
        <el-form-item label="门户显示">
          <el-switch v-model="form.is_active" active-text="显示" inactive-text="隐藏" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { getCountries } from '@/api/resource'
import {
  createCountryShortcut, deleteCountryShortcut, getCountryShortcuts, updateCountryShortcut,
} from '@/api/product'

const loading = ref(false)
const saving = ref(false)
const tableData = ref([])
const countries = ref([])
const dialogVisible = ref(false)
const editRow = ref(null)
const formRef = ref(null)
const form = reactive({ country: null, display_name: '', sort_order: 0, is_active: true })
const rules = { country: [{ required: true, message: '请选择国家' }] }

function countryLabel(c) {
  const region = c.region_name || ''
  return region ? `${region} / ${c.name}` : c.name
}

async function loadData() {
  loading.value = true
  try {
    const res = await getCountryShortcuts({ page_size: 200, ordering: 'sort_order' })
    tableData.value = res?.results || res || []
  } finally {
    loading.value = false
  }
}

async function loadCountries() {
  const res = await getCountries({ page_size: 500 }).catch(() => null)
  countries.value = res?.results || res || []
}

function openDialog(row = null) {
  editRow.value = row
  Object.assign(form, row
    ? { country: row.country, display_name: row.display_name || '', sort_order: row.sort_order || 0, is_active: row.is_active !== false }
    : { country: null, display_name: '', sort_order: tableData.value.length, is_active: true })
  dialogVisible.value = true
}

async function handleSave() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    const payload = {
      country: form.country,
      display_name: (form.display_name || '').trim(),
      sort_order: form.sort_order || 0,
      is_active: form.is_active,
    }
    if (editRow.value) {
      await updateCountryShortcut(editRow.value.id, payload)
      ElMessage.success('已更新')
    } else {
      await createCountryShortcut(payload)
      ElMessage.success('已添加')
    }
    dialogVisible.value = false
    loadData()
  } catch (e) {
    const detail = e?.response?.data
    ElMessage.error(detail?.country?.[0] || detail?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function removeRow(row) {
  await ElMessageBox.confirm(`从快速查找中移除「${row.label}」？`, '删除', { type: 'warning' })
  await deleteCountryShortcut(row.id)
  ElMessage.success('已删除')
  loadData()
}

onMounted(() => {
  loadCountries()
  loadData()
})
</script>

<style scoped>
.page-container { min-height: 100%; }
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.page-title { font-size: 18px; font-weight: 700; color: #1a1a2e; margin: 0; }
</style>
