<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <h2>自助任务</h2>
        <p>门户重装 / 快照任务与镜像目录。本步只记账，不调用节点重装或 PVE 快照。</p>
      </div>
      <el-button @click="reload">刷新</el-button>
    </div>
    <el-card shadow="never">
      <el-tabs v-model="tab">
        <el-tab-pane label="任务" name="jobs">
          <div class="toolbar">
            <el-select v-model="jobFilters.job_type" clearable placeholder="类型" @change="loadJobs">
              <el-option label="重装系统" value="reinstall" />
              <el-option label="创建快照" value="snapshot_create" />
              <el-option label="恢复快照" value="snapshot_restore" />
            </el-select>
            <el-select v-model="jobFilters.status" clearable placeholder="状态" @change="loadJobs">
              <el-option label="待处理" value="pending" />
              <el-option label="进行中" value="running" />
              <el-option label="已完成" value="succeeded" />
              <el-option label="失败" value="failed" />
              <el-option label="已取消" value="cancelled" />
            </el-select>
          </div>
          <el-table :data="jobs" v-loading="jobLoading" border stripe>
            <el-table-column prop="job_no" label="编号" width="170" />
            <el-table-column prop="customer_name" label="客户" width="140" />
            <el-table-column prop="vps_no" label="VPS" width="140" />
            <el-table-column prop="job_type_label" label="类型" width="120" />
            <el-table-column label="状态" width="110">
              <template #default="{ row }">
                <el-tag size="small" :type="jobStatusType[row.status]">{{ row.status_label }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="result_message" label="摘要" min-width="200" show-overflow-tooltip />
            <el-table-column label="操作" width="200" fixed="right">
              <template #default="{ row }">
                <template v-if="canManage && (row.status === 'pending' || row.status === 'running')">
                  <el-button link type="success" @click="completeJob(row)">完成</el-button>
                  <el-button link type="danger" @click="failJob(row)">失败</el-button>
                  <el-button link @click="cancelJob(row)">取消</el-button>
                </template>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="镜像目录" name="images">
          <div class="toolbar">
            <el-button v-if="canManage" type="primary" @click="openImage()">新增镜像</el-button>
          </div>
          <el-table :data="images" v-loading="imageLoading" border stripe>
            <el-table-column prop="code" label="代码" width="140" />
            <el-table-column prop="name" label="名称" min-width="160" />
            <el-table-column prop="os_type_label" label="系统" width="120" />
            <el-table-column prop="version" label="版本" width="100" />
            <el-table-column label="上架" width="80">
              <template #default="{ row }">{{ row.enabled ? '是' : '否' }}</template>
            </el-table-column>
            <el-table-column label="操作" width="140" fixed="right">
              <template #default="{ row }">
                <el-button v-if="canManage" link type="primary" @click="openImage(row)">编辑</el-button>
                <el-button v-if="canManage" link type="danger" @click="removeImage(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="快照档案" name="snapshots">
          <el-table :data="snapshots" v-loading="snapLoading" border stripe>
            <el-table-column prop="snapshot_no" label="编号" width="170" />
            <el-table-column prop="customer_name" label="客户" width="140" />
            <el-table-column prop="vps_no" label="VPS" width="140" />
            <el-table-column prop="name" label="名称" min-width="160" />
            <el-table-column prop="status_label" label="状态" width="100" />
            <el-table-column label="操作" width="90" fixed="right">
              <template #default="{ row }">
                <el-button
                  v-if="canManage && row.status !== 'deleted' && row.status !== 'restoring'"
                  link
                  type="danger"
                  @click="removeSnapshot(row)"
                >删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <el-dialog v-model="imageVisible" :title="imageForm.id ? '编辑镜像' : '新增镜像'" width="480px" destroy-on-close>
      <el-form label-position="top">
        <el-form-item label="代码">
          <el-input v-model="imageForm.code" :disabled="Boolean(imageForm.id)" />
        </el-form-item>
        <el-form-item label="名称"><el-input v-model="imageForm.name" /></el-form-item>
        <el-form-item label="系统">
          <el-select v-model="imageForm.os_type" style="width:100%">
            <el-option label="Debian" value="debian" />
            <el-option label="Ubuntu" value="ubuntu" />
            <el-option label="CentOS" value="centos" />
            <el-option label="RHEL" value="rhel" />
            <el-option label="Windows Server" value="windows" />
            <el-option label="其他" value="other" />
          </el-select>
        </el-form-item>
        <el-form-item label="版本"><el-input v-model="imageForm.version" /></el-form-item>
        <el-form-item label="上架"><el-switch v-model="imageForm.enabled" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="imageVisible = false">取消</el-button>
        <el-button type="primary" :loading="imageSaving" @click="saveImage">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import {
  cancelSelfServiceJob, completeSelfServiceJob, createOsImage, deleteInstanceSnapshot,
  deleteOsImage, failSelfServiceJob, getInstanceSnapshots, getOsImages, getSelfServiceJobs,
  updateOsImage,
} from '@/api/service'

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('service.selfservice.manage') || authStore.hasPermission('service.manage') || authStore.user?.is_superuser)
const tab = ref('jobs')
const jobs = ref([])
const images = ref([])
const snapshots = ref([])
const jobLoading = ref(false)
const imageLoading = ref(false)
const snapLoading = ref(false)
const jobFilters = reactive({ job_type: '', status: '' })
const imageVisible = ref(false)
const imageSaving = ref(false)
const imageForm = reactive({ id: null, code: '', name: '', os_type: 'debian', version: '', enabled: true })
const jobStatusType = { pending: 'warning', running: '', succeeded: 'success', failed: 'danger', cancelled: 'info' }

async function loadJobs() {
  jobLoading.value = true
  try {
    const params = { page_size: 50 }
    Object.entries(jobFilters).forEach(([k, v]) => { if (v) params[k] = v })
    const res = await getSelfServiceJobs(params)
    jobs.value = res.results || res
  } finally {
    jobLoading.value = false
  }
}
async function loadImages() {
  imageLoading.value = true
  try {
    const res = await getOsImages({ page_size: 100 })
    images.value = res.results || res
  } finally {
    imageLoading.value = false
  }
}
async function loadSnapshots() {
  snapLoading.value = true
  try {
    const res = await getInstanceSnapshots({ page_size: 50 })
    snapshots.value = res.results || res
  } finally {
    snapLoading.value = false
  }
}
function reload() {
  if (tab.value === 'jobs') return loadJobs()
  if (tab.value === 'images') return loadImages()
  return loadSnapshots()
}
watch(tab, reload)
async function completeJob(row) {
  await ElMessageBox.confirm(
    `确认将任务 ${row.job_no} 标记为完成？骨架完成会改写 VPS 系统档案，且不会调用节点。`,
    '完成自助任务',
    { type: 'warning', confirmButtonText: '确认完成' },
  )
  await completeSelfServiceJob(row.id, { message: '管理端骨架完成' })
  ElMessage.success('已标记完成')
  await loadJobs()
}
async function failJob(row) {
  await ElMessageBox.confirm(`确认将任务 ${row.job_no} 标记为失败？`, '标记失败', { type: 'warning' })
  await failSelfServiceJob(row.id, { message: '管理端标记失败' })
  ElMessage.success('已标记失败')
  await loadJobs()
}
async function cancelJob(row) {
  await ElMessageBox.confirm(`确认取消任务 ${row.job_no}？`, '取消任务', { type: 'warning' })
  await cancelSelfServiceJob(row.id, {})
  ElMessage.success('已取消')
  await loadJobs()
}
function openImage(row) {
  Object.assign(imageForm, row ? { ...row } : { id: null, code: '', name: '', os_type: 'debian', version: '', enabled: true })
  imageVisible.value = true
}
async function saveImage() {
  imageSaving.value = true
  try {
    const payload = { code: imageForm.code, name: imageForm.name, os_type: imageForm.os_type, version: imageForm.version, enabled: imageForm.enabled }
    if (imageForm.id) await updateOsImage(imageForm.id, payload)
    else await createOsImage(payload)
    ElMessage.success('已保存')
    imageVisible.value = false
    await loadImages()
  } finally {
    imageSaving.value = false
  }
}
async function removeImage(row) {
  await ElMessageBox.confirm(`删除镜像 ${row.name}？`, '确认', { type: 'warning' })
  await deleteOsImage(row.id)
  ElMessage.success('已删除')
  await loadImages()
}
async function removeSnapshot(row) {
  await ElMessageBox.confirm(`删除快照 ${row.snapshot_no}？`, '确认', { type: 'warning' })
  await deleteInstanceSnapshot(row.id)
  ElMessage.success('已删除')
  await loadSnapshots()
}
onMounted(loadJobs)
</script>

<style scoped>
.page-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:16px; }
.page-header h2{margin:0;font-size:20px}
.page-header p{margin:6px 0 0;color:#909399;font-size:13px}
.toolbar{display:flex;gap:12px;margin-bottom:16px}
</style>
