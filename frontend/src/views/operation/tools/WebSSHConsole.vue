<template>
  <div class="page">
    <div class="page-toolbar">
      <div class="pt-left">
        <span class="pt-title">WebSSH 终端</span>
        <el-text type="info" size="small">可从资源选择目标，或手动填写主机与账号密码；命令模板在下方快速下发。</el-text>
      </div>
      <el-button link type="primary" @click="goTemplates">管理命令模版 →</el-button>
    </div>

    <div class="layout-stack">
      <!-- 1. 连接方式 -->
      <el-card shadow="never" class="section-card">
        <template #header>连接方式</template>
        <el-form label-width="88px" size="default" class="compact-form">
          <el-form-item label="方式">
            <el-radio-group v-model="connectMode" :disabled="connected" @change="onConnectModeChange">
              <el-radio-button value="resource">从资源选择</el-radio-button>
              <el-radio-button value="manual">手动填写</el-radio-button>
            </el-radio-group>
          </el-form-item>

          <template v-if="connectMode === 'resource'">
            <el-form-item label="目标类型">
              <el-radio-group v-model="targetType" :disabled="connected" @change="onTargetTypeChange">
                <el-radio-button value="monitoring_host">监控主机</el-radio-button>
                <el-radio-button value="pop">POP 节点</el-radio-button>
                <el-radio-button value="vps">VPS</el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="目标">
              <el-select
                v-model="targetId"
                filterable
                remote
                reserve-keyword
                :remote-method="searchTargets"
                :loading="targetLoading"
                placeholder="搜索并选择目标"
                class="field-wide"
                :disabled="connected"
                @change="loadContext"
              >
                <el-option
                  v-for="item in targetOptions"
                  :key="`${targetType}-${item.id}`"
                  :label="item.label"
                  :value="item.id"
                />
              </el-select>
            </el-form-item>
            <el-form-item v-if="context?.label" label="说明">
              <el-text size="small">{{ context.label }}</el-text>
            </el-form-item>
          </template>

          <el-alert v-else type="info" :closable="false" show-icon
            title="手动模式：直接在下方填写 SSH 信息，无需选择系统内资源。" />
        </el-form>
      </el-card>

      <!-- 2. SSH 连接 -->
      <el-card shadow="never" class="section-card">
        <template #header>SSH 连接</template>
        <el-form :model="ssh" label-width="72px" size="default" class="ssh-form">
          <el-row :gutter="16">
            <el-col :xs="24" :sm="12" :md="8" :lg="6">
              <el-form-item label="主机">
                <el-input v-model="ssh.host" placeholder="IP / 域名" :disabled="connected" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :sm="12" :md="4" :lg="3">
              <el-form-item label="端口">
                <el-input-number v-model="ssh.port" :min="1" :max="65535" :controls="false"
                  style="width:100%" :disabled="connected" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :sm="12" :md="4" :lg="4">
              <el-form-item label="用户名">
                <el-input v-model="ssh.username" :disabled="connected" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :sm="12" :md="8" :lg="5">
              <el-form-item label="密码">
                <el-input v-model="ssh.password" type="password" show-password :disabled="connected" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :sm="24" :md="24" :lg="6">
              <el-form-item label=" ">
                <el-button v-if="!connected" type="primary" :icon="Connection"
                  :loading="connecting" :disabled="!canConnect" @click="doConnect">连接</el-button>
                <el-button v-else type="danger" :icon="SwitchButton" @click="doDisconnect">断开</el-button>
                <el-tag :type="connected ? 'success' : 'info'" size="small" style="margin-left:8px">
                  {{ connected ? '已连接' : '未连接' }}
                </el-tag>
              </el-form-item>
            </el-col>
          </el-row>
        </el-form>
      </el-card>

      <!-- 3. 终端 -->
      <el-card
        shadow="never"
        class="section-card terminal-card"
        :class="{ 'is-maximized': terminalMaximized, 'is-sticky': !terminalMaximized }"
      >
        <template #header>
          <div class="terminal-toolbar">
            <div class="terminal-toolbar-left">
              <span class="terminal-title">终端</span>
              <el-text type="info" size="small">{{ terminalLabel }}</el-text>
              <el-tag v-if="connected" type="success" size="small" effect="plain">已连接</el-tag>
            </div>
            <div class="terminal-toolbar-actions">
              <el-tooltip :content="terminalMaximized ? '还原窗口 (Esc)' : '最大化终端'" placement="top">
                <el-button
                  circle
                  size="small"
                  :type="terminalMaximized ? 'warning' : 'default'"
                  @click="toggleTerminalMaximize"
                >
                  <el-icon><FullScreen v-if="!terminalMaximized" /><Close v-else /></el-icon>
                </el-button>
              </el-tooltip>
            </div>
          </div>
        </template>
        <WebSSHTerminal
          ref="terminalRef"
          :ws-url="wsUrl"
          @connected="onTerminalConnected"
          @disconnected="connected = false"
        />
      </el-card>

      <!-- 4. 命令模板 -->
      <el-card shadow="never" class="section-card">
        <template #header>
          <div class="section-header">
            <span>命令模板</span>
            <el-select v-model="tplCategory" size="small" clearable placeholder="分类" style="width:120px">
              <el-option v-for="c in categories" :key="c" :label="c" :value="c" />
            </el-select>
          </div>
        </template>
        <div v-loading="tplLoading" class="tpl-grid">
          <el-empty v-if="!filteredTemplates.length" description="暂无可用模板" :image-size="48" />
          <el-button
            v-for="tpl in filteredTemplates"
            :key="tpl.id"
            class="tpl-btn"
            size="default"
            :disabled="!connected"
            @click="openTemplate(tpl)"
          >
            <div class="tpl-name">{{ tpl.name }}</div>
            <div class="tpl-desc">{{ tpl.description || tpl.command }}</div>
          </el-button>
        </div>
        <el-alert type="info" :closable="false" show-icon style="margin-top:12px"
          title="模板命令经 WebSSH 发送到目标 shell；交互类命令请在终端内继续输入。" />
      </el-card>
    </div>

    <el-dialog v-model="tplDialogVisible" :title="activeTemplate?.name || '执行命令'" width="480px" destroy-on-close>
      <el-form v-if="activeTemplate" label-width="96px">
        <el-form-item v-for="field in activeTemplate.fields" :key="field.key" :label="field.label" :required="field.required">
          <el-input v-model="tplValues[field.key]" :placeholder="field.default || ''" />
        </el-form-item>
        <el-form-item label="预览">
          <el-input :model-value="previewCommand" type="textarea" :rows="3" readonly />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="tplDialogVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!previewCommand" @click="runTemplate">发送到终端</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Connection, SwitchButton, FullScreen, Close } from '@element-plus/icons-vue'
import WebSSHTerminal from '@/components/WebSSHTerminal.vue'
import {
  getWebSSHCommandTemplates,
  resolveWebSSHTarget,
  operationWebSSHWebSocketURL,
  getMonitoringHosts,
} from '@/api/operation'
import { getPopPoints, getVPS } from '@/api/resource'

const route = useRoute()
const router = useRouter()

const connectMode = ref('resource')
const targetType = ref('monitoring_host')
const targetId = ref(null)
const targetOptions = ref([])
const targetLoading = ref(false)
const context = ref(null)
const templates = ref([])
const tplLoading = ref(false)
const tplCategory = ref('')
const tplDialogVisible = ref(false)
const activeTemplate = ref(null)
const tplValues = reactive({})

const ssh = reactive({ host: '', port: 22, username: 'root', password: '' })
const connected = ref(false)
const terminalMaximized = ref(false)
const terminalRef = ref(null)

const connecting = computed(() => terminalRef.value?.connecting?.value ?? false)
const wsUrl = computed(() => {
  if (connectMode.value === 'manual') {
    return operationWebSSHWebSocketURL('custom', 0)
  }
  if (!targetType.value || !targetId.value) return ''
  return operationWebSSHWebSocketURL(targetType.value, targetId.value)
})
const canConnect = computed(() => {
  if (!ssh.host || !wsUrl.value) return false
  if (connectMode.value === 'manual') return true
  return Boolean(targetId.value)
})
const terminalLabel = computed(() => {
  if (connectMode.value === 'manual') return ssh.host ? `${ssh.username}@${ssh.host}:${ssh.port}` : '手动连接'
  return context.value?.label || '未选择目标'
})

const categories = computed(() => {
  const set = new Set(templates.value.map((t) => t.category).filter(Boolean))
  return [...set]
})
const filteredTemplates = computed(() => {
  if (!tplCategory.value) return templates.value
  return templates.value.filter((t) => t.category === tplCategory.value)
})

const previewCommand = computed(() => buildCommand(activeTemplate.value, tplValues))

function buildCommand(tpl, values) {
  if (!tpl?.command) return ''
  let cmd = tpl.command
  for (const field of tpl.fields || []) {
    const val = (values[field.key] ?? field.default ?? '').toString().trim()
    if (field.required && !val) return ''
    cmd = cmd.replaceAll(`{${field.key}}`, val)
  }
  return cmd
}

function onTerminalConnected() {
  connected.value = true
  scheduleTerminalFit()
}

function scheduleTerminalFit() {
  nextTick(() => {
    terminalRef.value?.fit()
    setTimeout(() => terminalRef.value?.fit(), 80)
  })
}

function toggleTerminalMaximize() {
  terminalMaximized.value = !terminalMaximized.value
  document.body.style.overflow = terminalMaximized.value ? 'hidden' : ''
  scheduleTerminalFit()
}

function onTerminalKeydown(e) {
  if (e.key === 'Escape' && terminalMaximized.value) {
    terminalMaximized.value = false
    document.body.style.overflow = ''
    scheduleTerminalFit()
  }
}

function applyContext(ctx) {
  context.value = ctx
  if (!ctx) return
  ssh.host = ctx.host || ''
  ssh.port = ctx.port || 22
  ssh.username = ctx.username || 'root'
  ssh.password = ctx.password || ''
}

async function loadContext() {
  if (!targetId.value) {
    context.value = null
    return
  }
  try {
    const ctx = await resolveWebSSHTarget(targetType.value, targetId.value)
    applyContext(ctx)
  } catch (e) {
    ElMessage.error('加载目标失败：' + (e?.response?.data?.detail || e.message))
  }
}

async function searchTargets(keyword = '') {
  targetLoading.value = true
  try {
    const params = { page_size: 30 }
    if (keyword) params.search = keyword
    if (targetType.value === 'monitoring_host') {
      const res = await getMonitoringHosts(params)
      const rows = res.results || res
      targetOptions.value = rows.map((r) => ({
        id: r.id,
        label: `${r.visible_name || r.hostname || '主机#' + r.id} · ${r.monitored_ip || '-'}`,
      }))
    } else if (targetType.value === 'pop') {
      const res = await getPopPoints(params)
      const rows = res.results || res
      targetOptions.value = rows.map((r) => ({
        id: r.id,
        label: `[${r.code}] ${r.name} · ${r.mgmt_ip || '-'}`,
      }))
    } else {
      const res = await getVPS(params)
      const rows = res.results || res
      targetOptions.value = rows.map((r) => ({
        id: r.id,
        label: `[${r.vps_no || r.id}] ${r.name} · ${r.mgmt_ip || '-'}`,
      }))
    }
  } finally {
    targetLoading.value = false
  }
}

function onConnectModeChange() {
  doDisconnect()
  if (connectMode.value === 'manual') {
    targetId.value = null
    context.value = null
  } else {
    searchTargets('')
  }
}

function onTargetTypeChange() {
  targetId.value = null
  context.value = null
  ssh.host = ''
  ssh.port = 22
  ssh.username = 'root'
  ssh.password = ''
  doDisconnect()
  searchTargets('')
}

function doConnect() {
  terminalRef.value?.connect({ ...ssh })
}

function doDisconnect() {
  terminalRef.value?.disconnect()
  connected.value = false
}

function openTemplate(tpl) {
  activeTemplate.value = tpl
  Object.keys(tplValues).forEach((k) => delete tplValues[k])
  for (const field of tpl.fields || []) {
    tplValues[field.key] = field.default ?? ''
  }
  if (!tpl.fields?.length) {
    terminalRef.value?.sendCommand(tpl.command)
    return
  }
  tplDialogVisible.value = true
}

function runTemplate() {
  if (!previewCommand.value) {
    ElMessage.warning('请填写模板参数')
    return
  }
  terminalRef.value?.sendCommand(previewCommand.value)
  tplDialogVisible.value = false
}

function goTemplates() {
  router.push({ name: 'WebSSHCommandTemplates' })
}

async function loadTemplates() {
  tplLoading.value = true
  try {
    const res = await getWebSSHCommandTemplates({ active_only: 1, page_size: 200 })
    templates.value = res.results || res || []
  } catch {
    templates.value = []
  } finally {
    tplLoading.value = false
  }
}

async function initFromQuery() {
  const qType = route.query.target_type
  const qId = route.query.target_id
  if (qType === 'manual') {
    connectMode.value = 'manual'
    return
  }
  if (qType && ['monitoring_host', 'pop', 'vps'].includes(qType)) {
    connectMode.value = 'resource'
    targetType.value = qType
  }
  await searchTargets('')
  if (qId) {
    targetId.value = Number(qId)
    await loadContext()
  }
}

watch(() => route.query, () => {
  if (route.query.target_type || route.query.target_id) initFromQuery()
})

onMounted(async () => {
  window.addEventListener('keydown', onTerminalKeydown)
  await Promise.all([loadTemplates(), initFromQuery()])
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onTerminalKeydown)
  document.body.style.overflow = ''
})
</script>

<style scoped>
.page {
  min-height: 100%;
  background: #fff;
  border-radius: 8px;
  padding: 16px 20px;
}
.page-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.pt-left { display: flex; flex-direction: column; gap: 4px; }
.pt-title { font-size: 18px; font-weight: 700; color: #1a1a2e; }

.layout-stack {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.section-card :deep(.el-card__header) {
  font-weight: 600;
  padding: 12px 16px;
}
.section-card :deep(.el-card__body) {
  padding: 16px;
}
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.compact-form :deep(.el-form-item) {
  margin-bottom: 14px;
}
.compact-form :deep(.el-form-item:last-child) {
  margin-bottom: 0;
}
.field-wide { width: 100%; max-width: 520px; }

.ssh-form :deep(.el-form-item) {
  margin-bottom: 0;
}

/* 终端：滚动时顶栏悬浮 */
.terminal-card.is-sticky :deep(.el-card__header) {
  position: sticky;
  top: 0;
  z-index: 30;
  background: #fff;
  box-shadow: 0 1px 0 rgba(0, 0, 0, 0.06);
}

.terminal-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.terminal-toolbar-left {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  min-width: 0;
}
.terminal-title { font-weight: 600; white-space: nowrap; }
.terminal-toolbar-actions { flex-shrink: 0; }

.terminal-card :deep(.el-card__body) {
  padding: 8px;
  height: 420px;
}
.terminal-card :deep(.webssh-terminal),
.terminal-card :deep(.terminal-box) {
  min-height: 0;
  height: 100%;
}

/* 最大化：铺满视口 */
.terminal-card.is-maximized {
  position: fixed;
  inset: 0;
  z-index: 4000;
  margin: 0;
  border-radius: 0;
  border: none;
  display: flex;
  flex-direction: column;
  background: #11111b;
}
.terminal-card.is-maximized :deep(.el-card__header) {
  flex-shrink: 0;
  background: #181825;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  color: #cdd6f4;
}
.terminal-card.is-maximized :deep(.el-card__body) {
  flex: 1;
  height: auto !important;
  min-height: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}
.terminal-card.is-maximized :deep(.webssh-terminal) {
  flex: 1;
  min-height: 0;
}
.terminal-card.is-maximized :deep(.terminal-box) {
  border-radius: 0;
  min-height: 0;
}

.tpl-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 10px;
  min-height: 60px;
}
.tpl-btn {
  margin-left: 0;
  height: auto;
  padding: 10px 12px;
  text-align: left;
  white-space: normal;
  justify-content: flex-start;
}
.tpl-name { font-weight: 600; line-height: 1.4; width: 100%; }
.tpl-desc {
  font-size: 12px;
  color: #909399;
  line-height: 1.3;
  margin-top: 4px;
  width: 100%;
}
</style>
