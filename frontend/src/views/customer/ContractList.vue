<template>
  <div class="page-container">
    <div class="page-header">
      <h2 class="page-title">合同管理</h2>
      <el-button type="primary" :icon="Plus" @click="openDialog()">新增合同</el-button>
    </div>

    <el-card shadow="never" class="search-card">
      <el-form :model="searchForm" inline>
        <el-form-item label="状态">
          <el-select v-model="searchForm.status" clearable placeholder="全部" style="width:120px">
            <el-option v-for="s in statusOptions" :key="s.value" :label="s.label" :value="s.value" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="loadData">搜索</el-button>
          <el-button :icon="RefreshLeft" @click="resetSearch">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" style="margin-top:12px;">
      <el-table :data="tableData" v-loading="loading" stripe border>
        <el-table-column prop="contract_no" label="合同编号" width="160" />
        <el-table-column prop="contract_name" label="合同名称" min-width="180" />
        <el-table-column prop="customer_name" label="客户" width="130" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusMap[row.status]?.type" size="small">{{ statusMap[row.status]?.label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="amount" label="合同金额" width="120" align="right">
          <template #default="{ row }">¥{{ Number(row.amount || 0).toFixed(2) }}</template>
        </el-table-column>
        <el-table-column prop="start_date" label="起始日期" width="110" />
        <el-table-column prop="end_date" label="结束日期" width="110" />
        <el-table-column prop="sign_date" label="签署日期" width="110" />
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDialog(row)">编辑</el-button>
            <el-button link type="success" size="small" v-if="row.status === 'reviewing'" @click="handleSign(row)">签署</el-button>
            <el-button link type="danger" size="small" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total"
        layout="total, prev, pager, next" class="pagination" @change="loadData" />
    </el-card>

    <el-dialog v-model="dialogVisible" :title="editRow ? '编辑合同' : '新增合同'" width="600px" destroy-on-close>
      <el-form ref="formRef" :model="form" :rules="formRules" label-width="100px">
        <el-form-item label="合同名称" prop="contract_name">
          <el-input v-model="form.contract_name" />
        </el-form-item>
        <el-form-item label="关联客户" prop="customer">
          <el-select v-model="form.customer" filterable placeholder="选择客户" style="width:100%">
            <el-option v-for="c in customers" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="合同金额" prop="amount">
              <el-input-number v-model="form.amount" :min="0" :precision="2" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="签署日期">
              <el-date-picker v-model="form.sign_date" type="date" value-format="YYYY-MM-DD" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="起始日期" prop="start_date">
              <el-date-picker v-model="form.start_date" type="date" value-format="YYYY-MM-DD" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="结束日期" prop="end_date">
              <el-date-picker v-model="form.end_date" type="date" value-format="YYYY-MM-DD" style="width:100%" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="文件链接">
          <el-input v-model="form.file_url" placeholder="合同文件URL" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">确认</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search, RefreshLeft } from '@element-plus/icons-vue'
import { getContracts, createContract, updateContract, deleteContract, signContract } from '@/api/customer'
import { getCustomers } from '@/api/customer'

const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const formRef = ref(null)
const editRow = ref(null)
const tableData = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const customers = ref([])

const searchForm = reactive({ status: '' })
const form = reactive({ contract_name: '', customer: null, amount: 0, sign_date: '', start_date: '', end_date: '', file_url: '', remark: '' })

const statusOptions = [
  { value: 'draft', label: '草稿' }, { value: 'reviewing', label: '审核中' },
  { value: 'active', label: '生效' }, { value: 'expired', label: '已过期' }, { value: 'terminated', label: '已终止' }
]

const statusMap = {
  draft: { label: '草稿', type: 'info' }, reviewing: { label: '审核中', type: 'warning' },
  active: { label: '生效', type: 'success' }, expired: { label: '已过期', type: 'danger' }, terminated: { label: '已终止', type: 'danger' }
}

const formRules = {
  contract_name: [{ required: true, message: '请输入合同名称' }],
  customer: [{ required: true, message: '请选择客户' }],
  amount: [{ required: true, message: '请输入金额' }],
  start_date: [{ required: true, message: '请选择起始日期' }],
  end_date: [{ required: true, message: '请选择结束日期' }]
}

async function loadData() {
  loading.value = true
  try {
    const res = await getContracts({ ...searchForm, page: page.value, page_size: pageSize.value })
    tableData.value = res?.results || []
    total.value = res?.count || 0
  } finally { loading.value = false }
}

function resetSearch() {
  searchForm.status = ''
  loadData()
}

function openDialog(row = null) {
  editRow.value = row
  if (row) Object.assign(form, row)
  else Object.assign(form, { contract_name: '', customer: null, amount: 0, sign_date: '', start_date: '', end_date: '', file_url: '', remark: '' })
  dialogVisible.value = true
}

async function handleSave() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    if (editRow.value) { await updateContract(editRow.value.id, form); ElMessage.success('已更新') }
    else { await createContract(form); ElMessage.success('创建成功') }
    dialogVisible.value = false
    loadData()
  } finally { saving.value = false }
}

async function handleSign(row) {
  await ElMessageBox.confirm('确定签署该合同？', '提示', { type: 'info' })
  await signContract(row.id)
  ElMessage.success('合同已签署')
  loadData()
}

async function handleDelete(row) {
  await ElMessageBox.confirm('确定删除？', '警告', { type: 'warning' })
  await deleteContract(row.id)
  ElMessage.success('已删除')
  loadData()
}

onMounted(async () => {
  loadData()
  const res = await getCustomers({ page_size: 200 })
  customers.value = res?.results || []
})
</script>

<style scoped>
.page-container { min-height: 100%; }
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.page-title { font-size: 18px; font-weight: 700; color: #1a1a2e; margin: 0; }
.search-card :deep(.el-card__body) { padding: 16px 16px 0; }
.pagination { margin-top: 16px; justify-content: flex-end; }
</style>
