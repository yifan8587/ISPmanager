<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <h2 class="page-title">
          系统维护
          <el-tag v-if="version.display" type="primary" effect="dark" class="ver-tag">{{ version.display }}</el-tag>
        </h2>
        <p class="page-desc">查看系统版本，在线备份 / 还原业务数据，上传更新包完成系统升级。</p>
      </div>
      <el-button :icon="Refresh" :loading="loading" @click="reloadAll">刷新</el-button>
    </div>

    <el-row :gutter="12" class="stat-row">
      <el-col :xs="12" :sm="6">
        <div class="stat-card">
          <span class="stat-num">{{ version.display || '-' }}</span>
          <span class="stat-lbl">当前版本</span>
        </div>
      </el-col>
      <el-col :xs="12" :sm="6">
        <div class="stat-card">
          <span class="stat-num">{{ version.backup_count ?? '-' }}</span>
          <span class="stat-lbl">备份记录</span>
        </div>
      </el-col>
      <el-col :xs="12" :sm="6">
        <div class="stat-card">
          <span class="stat-num">{{ formatTime(version.latest_backup_at) }}</span>
          <span class="stat-lbl">最近备份</span>
        </div>
      </el-col>
      <el-col :xs="12" :sm="6">
        <div class="stat-card">
          <span class="stat-num">{{ formatTime(version.latest_upgrade_at) }}</span>
          <span class="stat-lbl">最近升级</span>
        </div>
      </el-col>
    </el-row>

    <el-alert
      v-if="version.busy"
      title="正在执行备份或升级任务，完成后会自动刷新状态。请勿关闭页面或重复提交。"
      type="warning"
      show-icon
      :closable="false"
      class="busy-alert"
    />

    <el-card shadow="never">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="业务数据备份" name="backup">
          <div class="toolbar">
            <el-input v-model="backupNote" placeholder="备份备注（可选）" style="width:260px" :disabled="!canManage" />
            <el-button type="primary" :icon="FolderAdd" :disabled="!canManage" :loading="creatingBackup" @click="createBackup">
              立即备份
            </el-button>
          </div>
          <ArchivePackageUpload
            :disabled="!canManage || uploadingBackup"
            tip="上传已有备份包用于恢复。支持 .tar.gz / .tgz / .tar。"
            @upload="uploadBackup"
          />
          <p class="hint">备份包含当前业务数据库。还原会先自动再备份一次当前数据，然后导入所选备份。</p>
          <el-table :data="backups" v-loading="backupLoading" border stripe>
            <el-table-column prop="created_at" label="时间" width="180">
              <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column prop="name" label="名称" min-width="200" />
            <el-table-column label="类型" width="120">
              <template #default="{ row }">
                <el-tag size="small" effect="plain">{{ row.backup_type_label }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="版本" width="90">
              <template #default="{ row }">{{ row.source_version ? `V${row.source_version}` : '-' }}</template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <el-tag size="small" :type="statusType(row.status)">{{ row.status_label }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="file_size_display" label="大小" width="100" />
            <el-table-column prop="created_by_name" label="操作人" width="110" />
            <el-table-column prop="note" label="备注" min-width="140" show-overflow-tooltip />
            <el-table-column label="操作" width="220" fixed="right">
              <template #default="{ row }">
                <el-button
                  link
                  type="primary"
                  :disabled="!row.download_ready"
                  @click="downloadBackup(row)"
                >下载</el-button>
                <el-button
                  link
                  type="warning"
                  :disabled="!canManage || !row.download_ready"
                  @click="restoreBackup(row)"
                >还原</el-button>
                <el-button
                  link
                  type="danger"
                  :disabled="!canManage || row.status === 'pending' || row.status === 'running'"
                  @click="removeBackup(row)"
                >删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <div class="pager">
            <el-pagination
              v-model:current-page="backupPage"
              :page-size="20"
              :total="backupTotal"
              layout="total, prev, pager, next"
              @current-change="loadBackups"
            />
          </div>
        </el-tab-pane>

        <el-tab-pane label="在线升级" name="upgrade">
          <el-alert
            type="info"
            :closable="false"
            show-icon
            class="pkg-tip"
            title="更新包为 .tar.gz，根目录需包含 VERSION（如 1.2）或 manifest.json。可选 apply.sh、backend/、frontend/。升级前会自动备份业务数据。"
          />
          <div class="toolbar upload-block">
            <ArchivePackageUpload
              :disabled="!canManage || uploadingUpgrade"
              tip="上传官方更新包（ispmanager-update-*.tar.gz）。支持 .tar.gz / .tgz / .tar。"
              @upload="uploadUpgrade"
            />
            <el-progress
              v-if="upgradePct > 0 && upgradePct < 100"
              :percentage="upgradePct"
              style="width:240px"
            />
          </div>
          <el-table :data="upgrades" v-loading="upgradeLoading" border stripe @row-click="openUpgradeLog">
            <el-table-column prop="uploaded_at" label="上传时间" width="180">
              <template #default="{ row }">{{ formatTime(row.uploaded_at) }}</template>
            </el-table-column>
            <el-table-column prop="filename" label="更新包" min-width="200" show-overflow-tooltip />
            <el-table-column label="包版本" width="90">
              <template #default="{ row }">{{ row.package_version ? `V${row.package_version}` : '-' }}</template>
            </el-table-column>
            <el-table-column label="版本变化" width="140">
              <template #default="{ row }">
                <span v-if="row.from_version || row.to_version">
                  {{ row.from_version ? `V${row.from_version}` : '-' }}
                  →
                  {{ row.to_version ? `V${row.to_version}` : '-' }}
                </span>
                <span v-else>-</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <el-tag size="small" :type="statusType(row.status)">{{ row.status_label }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="file_size_display" label="大小" width="100" />
            <el-table-column prop="uploaded_by_name" label="上传人" width="110" />
            <el-table-column prop="changelog" label="说明" min-width="160" show-overflow-tooltip />
            <el-table-column label="操作" width="180" fixed="right">
              <template #default="{ row }">
                <el-button
                  link
                  type="primary"
                  :disabled="!canManage || (row.status !== 'ready' && row.status !== 'failed')"
                  @click.stop="applyUpgrade(row)"
                >执行升级</el-button>
                <el-button
                  link
                  type="danger"
                  :disabled="!canManage || row.status === 'applying'"
                  @click.stop="removeUpgrade(row)"
                >删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <div class="pager">
            <el-pagination
              v-model:current-page="upgradePage"
              :page-size="20"
              :total="upgradeTotal"
              layout="total, prev, pager, next"
              @current-change="loadUpgrades"
            />
          </div>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <el-drawer v-model="logVisible" title="升级日志" size="520px">
      <div v-if="currentUpgrade" class="log-meta">
        <div>文件：{{ currentUpgrade.filename }}</div>
        <div>状态：{{ currentUpgrade.status_label }}</div>
        <div v-if="currentUpgrade.error_message" class="err">{{ currentUpgrade.error_message }}</div>
      </div>
      <pre class="log-box">{{ currentUpgrade?.apply_log || '暂无日志' }}</pre>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { FolderAdd, Refresh } from '@element-plus/icons-vue'
import ArchivePackageUpload from '@/components/ArchivePackageUpload.vue'
import dayjs from 'dayjs'
import { useAuthStore } from '@/stores/auth'
import {
  applySystemUpgrade,
  createSystemBackup,
  deleteSystemBackup,
  deleteSystemUpgrade,
  downloadSystemBackup,
  getSystemBackups,
  getSystemUpgrade,
  getSystemUpgrades,
  getSystemVersion,
  restoreSystemBackup,
  uploadSystemBackup,
  uploadSystemUpgrade,
} from '@/api/system'

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('system.maintenance.manage') || authStore.user?.is_superuser)

const loading = ref(false)
const activeTab = ref('backup')
const version = ref({})
const backups = ref([])
const upgrades = ref([])
const backupTotal = ref(0)
const upgradeTotal = ref(0)
const backupPage = ref(1)
const upgradePage = ref(1)
const backupLoading = ref(false)
const upgradeLoading = ref(false)
const creatingBackup = ref(false)
const uploadingBackup = ref(false)
const uploadingUpgrade = ref(false)
const upgradePct = ref(0)
const backupNote = ref('')
const logVisible = ref(false)
const currentUpgrade = ref(null)
let timer = null

function formatTime(value) {
  if (!value) return '-'
  return dayjs(value).format('YYYY-MM-DD HH:mm')
}

function statusType(status) {
  return {
    success: 'success',
    ready: 'success',
    failed: 'danger',
    rejected: 'danger',
    running: 'warning',
    pending: 'warning',
    applying: 'warning',
    uploaded: 'info',
  }[status] || 'info'
}

function isBusyStatus(row) {
  return ['pending', 'running', 'applying'].includes(row?.status)
}

async function loadVersion() {
  try {
    version.value = await getSystemVersion() || {}
  } catch {
    version.value = {}
  }
}

async function loadBackups() {
  backupLoading.value = true
  try {
    const res = await getSystemBackups({ page: backupPage.value, page_size: 20 })
    backups.value = res.results || res || []
    backupTotal.value = res.count || backups.value.length
  } finally {
    backupLoading.value = false
  }
}

async function loadUpgrades() {
  upgradeLoading.value = true
  try {
    const res = await getSystemUpgrades({ page: upgradePage.value, page_size: 20 })
    upgrades.value = res.results || res || []
    upgradeTotal.value = res.count || upgrades.value.length
  } finally {
    upgradeLoading.value = false
  }
}

async function reloadAll() {
  loading.value = true
  try {
    await Promise.all([loadVersion(), loadBackups(), loadUpgrades()])
  } finally {
    loading.value = false
  }
}

async function createBackup() {
  creatingBackup.value = true
  try {
    await createSystemBackup({ note: backupNote.value })
    ElMessage.success('已开始备份')
    backupNote.value = ''
    await reloadAll()
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || '备份失败')
  } finally {
    creatingBackup.value = false
  }
}

async function uploadBackup({ file }) {
  if (!file) {
    ElMessage.error('未选择文件')
    return
  }
  uploadingBackup.value = true
  const form = new FormData()
  form.append('file', file, file.name)
  try {
    await uploadSystemBackup(form)
    ElMessage.success('备份包已上传')
    await reloadAll()
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || '上传失败')
  } finally {
    uploadingBackup.value = false
  }
}

async function downloadBackup(row) {
  try {
    await downloadSystemBackup(row.id, row.file_name)
  } catch (err) {
    ElMessage.error(err?.message || '下载失败')
  }
}

async function restoreBackup(row) {
  try {
    await ElMessageBox.prompt(
      `将还原备份「${row.name}」。系统会先自动备份当前业务数据。请输入 RESTORE 确认。`,
      '确认还原',
      { confirmButtonText: '开始还原', cancelButtonText: '取消', inputPattern: /^RESTORE$/, inputErrorMessage: '请输入 RESTORE' },
    )
  } catch {
    return
  }
  try {
    await restoreSystemBackup(row.id, { confirm: 'RESTORE' })
    ElMessage.success('已开始还原，完成后服务可能自动重启')
    await reloadAll()
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || '还原失败')
  }
}

async function removeBackup(row) {
  await ElMessageBox.confirm(`确定删除备份「${row.name}」？`, '删除备份', { type: 'warning' })
  await deleteSystemBackup(row.id)
  ElMessage.success('已删除')
  await loadBackups()
}

async function uploadUpgrade({ file }) {
  if (!file) {
    ElMessage.error('未选择文件')
    return
  }
  const name = file.name || ''
  if (/install/i.test(name) && !/update/i.test(name)) {
    ElMessage.warning('这是安装包。在线升级请优先使用 ispmanager-update-*.tar.gz')
  }
  uploadingUpgrade.value = true
  upgradePct.value = 0
  const form = new FormData()
  form.append('file', file, file.name)
  try {
    await uploadSystemUpgrade(form, (evt) => {
      if (evt.total) upgradePct.value = Math.round((evt.loaded / evt.total) * 100)
    })
    ElMessage.success('更新包已校验并就绪')
    await reloadAll()
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || '上传失败')
  } finally {
    uploadingUpgrade.value = false
    upgradePct.value = 0
  }
}

async function applyUpgrade(row) {
  try {
    await ElMessageBox.prompt(
      `将从 ${version.value.display || '当前版本'} 升级到 V${row.package_version}。升级前会自动备份业务数据，完成后可能重启服务。请输入 UPGRADE 确认。`,
      '确认升级',
      { confirmButtonText: '开始升级', cancelButtonText: '取消', inputPattern: /^UPGRADE$/, inputErrorMessage: '请输入 UPGRADE' },
    )
  } catch {
    return
  }
  try {
    await applySystemUpgrade(row.id, { confirm: 'UPGRADE' })
    ElMessage.success('已开始升级')
    await reloadAll()
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || '升级失败')
  }
}

async function removeUpgrade(row) {
  await ElMessageBox.confirm(`确定删除更新包「${row.filename}」？`, '删除更新包', { type: 'warning' })
  await deleteSystemUpgrade(row.id)
  ElMessage.success('已删除')
  await loadUpgrades()
}

async function openUpgradeLog(row) {
  try {
    currentUpgrade.value = await getSystemUpgrade(row.id)
  } catch {
    currentUpgrade.value = row
  }
  logVisible.value = true
}

onMounted(() => {
  reloadAll()
  timer = setInterval(async () => {
    const busy = version.value.busy
      || backups.value.some(isBusyStatus)
      || upgrades.value.some(isBusyStatus)
    if (!busy) return
    await Promise.all([loadVersion(), loadBackups(), loadUpgrades()])
    if (logVisible.value && currentUpgrade.value?.id) {
      currentUpgrade.value = await getSystemUpgrade(currentUpgrade.value.id).catch(() => currentUpgrade.value)
    }
  }, 3000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.page-header { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 16px; }
.page-title { font-size: 18px; font-weight: 700; color: #1a1a2e; margin: 0; display: flex; align-items: center; gap: 10px; }
.page-desc { margin: 6px 0 0; color: #909399; font-size: 13px; }
.ver-tag { font-weight: 600; }
.stat-row { margin-bottom: 16px; }
.stat-card {
  background: #f8f9ff; border: 1px solid #ebeef5; border-radius: 8px;
  padding: 14px 16px; text-align: center;
}
.stat-num { display: block; font-size: 20px; font-weight: 700; color: #303133; }
.stat-lbl { display: block; font-size: 12px; color: #909399; margin-top: 4px; }
.busy-alert, .pkg-tip { margin-bottom: 12px; }
.toolbar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
.upload-block { flex-direction: column; align-items: flex-start; }
.hint { color: #909399; font-size: 12px; margin: 0 0 12px; }
.pager { display: flex; justify-content: flex-end; margin-top: 12px; }
.log-meta { font-size: 13px; color: #606266; line-height: 1.8; margin-bottom: 12px; }
.log-meta .err { color: #f56c6c; }
.log-box {
  background: #0d1b3e; color: #d8e2ff; padding: 12px; border-radius: 8px;
  white-space: pre-wrap; word-break: break-all; font-size: 12px; min-height: 240px;
}
</style>
