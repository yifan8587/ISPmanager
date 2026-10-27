<template>
  <div class="vnc-page">
    <div class="vnc-toolbar">
      <div class="vnc-title">
        <el-button :icon="ArrowLeft" size="small" @click="goBack">返回</el-button>
        <span class="vnc-name">noVNC 控制台 — {{ vmName }}</span>
        <el-tag :type="stateTag.type" size="small" effect="plain">{{ stateTag.label }}</el-tag>
      </div>
      <div class="vnc-actions">
        <el-button size="small" @click="sendCtrlAltDel" :disabled="!connected">Ctrl+Alt+Del</el-button>
        <el-button size="small" @click="toggleFullscreen">全屏</el-button>
        <el-button size="small" type="primary" :loading="connecting" @click="connectVnc">重新连接</el-button>
      </div>
    </div>

    <div v-loading="connecting" class="vnc-wrap">
      <div ref="screenRef" class="vnc-screen" />
      <el-empty v-if="errorMsg && !connected" :description="errorMsg" class="vnc-error" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft } from '@element-plus/icons-vue'
import { getVPSDetail, getVPSVncTicket } from '@/api/resource'

const route = useRoute()
const router = useRouter()
const vpsId = ref(route.params.id)

const screenRef = ref(null)
const connecting = ref(false)
const connected = ref(false)
const errorMsg = ref('')
const vmName = ref('')
let rfb = null

const stateTag = computed(() => {
  if (connected.value) return { type: 'success', label: '已连接' }
  if (connecting.value) return { type: 'warning', label: '连接中' }
  return { type: 'info', label: '未连接' }
})

function goBack() {
  router.push('/resource/vps')
}

async function loadVm() {
  try {
    const data = await getVPSDetail(vpsId.value)
    vmName.value = data?.name || `VPS ${vpsId.value}`
    if (data?.status && data.status !== 'running') {
      ElMessage.warning('虚机未处于运行状态，控制台可能无法连接')
    }
  } catch {
    vmName.value = `VPS ${vpsId.value}`
  }
}

async function connectVnc() {
  if (!screenRef.value) return
  errorMsg.value = ''
  connecting.value = true
  connected.value = false

  if (rfb) {
    try { rfb.disconnect() } catch { /* ignore */ }
    rfb = null
  }
  while (screenRef.value.firstChild) screenRef.value.removeChild(screenRef.value.firstChild)

  // 提前加载 noVNC，避免拿到票据后再动态 import 的延迟（PVE 票据约 30 秒失效）
  let RFB
  try {
    const mod = await import('@novnc/novnc')
    RFB = mod.default || mod.RFB || mod
  } catch (e) {
    connecting.value = false
    errorMsg.value = e?.message || '无法加载 noVNC'
    ElMessage.error(errorMsg.value)
    return
  }

  // 1) 取票据，并拿到与节点控制台相同的 WebSocket 地址
  let wsUrl = ''
  let vncCred = null
  try {
    const resp = await getVPSVncTicket(vpsId.value)
    wsUrl = resp?.ws_url || ''
    vncCred = resp?.password || resp?.ticket || null
  } catch (e) {
    connecting.value = false
    errorMsg.value = '获取 VNC 票据失败：' + (e?.response?.data?.detail || e?.message || '')
    ElMessage.error(errorMsg.value)
    return
  }
  if (!wsUrl) {
    connecting.value = false
    errorMsg.value = '节点未返回控制台地址'
    ElMessage.error(errorMsg.value)
    return
  }

  // 2) 与 vpopmanager 控制台相同：noVNC 直接连接节点
  try {
    const opts = { wsProtocols: ['binary'] }
    if (vncCred) opts.credentials = { password: vncCred }
    rfb = new RFB(screenRef.value, wsUrl, opts)
    rfb.scaleViewport = true
    rfb.resizeSession = true
    rfb.background = '#1a1a1a'
    rfb.addEventListener('connect', () => {
      connecting.value = false
      connected.value = true
      ElMessage.success('VNC 已连接')
    })
    rfb.addEventListener('disconnect', (e) => {
      connected.value = false
      connecting.value = false
      if (e.detail?.clean) return
      const reason = e.detail?.reason ? `：${e.detail.reason}` : ''
      errorMsg.value = `VNC 连接断开${reason}`
      ElMessage.error(errorMsg.value)
    })
    rfb.addEventListener('credentialsrequired', () => {
      if (vncCred) {
        try { rfb.sendCredentials({ password: vncCred }) } catch { /* ignore */ }
      }
    })
    rfb.addEventListener('securityfailure', (e) => {
      connecting.value = false
      errorMsg.value = `VNC 鉴权失败：${e?.detail?.reason || '未知'}`
      ElMessage.error(errorMsg.value)
    })
  } catch (e) {
    connecting.value = false
    errorMsg.value = e?.message || '建立 VNC 连接失败'
    ElMessage.error(errorMsg.value)
  }
}

function toggleFullscreen() {
  const el = screenRef.value
  if (!el) return
  if (!document.fullscreenElement) el.requestFullscreen?.()
  else document.exitFullscreen?.()
}

function sendCtrlAltDel() {
  if (rfb && rfb.sendCtrlAltDel) rfb.sendCtrlAltDel()
}

function disconnect() {
  if (rfb) {
    try { rfb.disconnect() } catch { /* ignore */ }
    rfb = null
  }
}

onMounted(async () => {
  await loadVm()
  await connectVnc()
})
onUnmounted(disconnect)
</script>

<style scoped>
.vnc-page { height: calc(100vh - 100px); display: flex; flex-direction: column; }
.vnc-toolbar {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; flex-wrap: wrap; margin-bottom: 12px;
}
.vnc-title { display: flex; align-items: center; gap: 10px; }
.vnc-name { font-size: 16px; font-weight: 700; color: #1a1a2e; }
.vnc-actions { display: flex; gap: 6px; }
.vnc-wrap {
  position: relative; flex: 1; min-height: 420px;
  background: #1a1a1a; border-radius: 8px; overflow: hidden;
}
.vnc-screen { width: 100%; height: 100%; min-height: 480px; }
.vnc-screen :deep(canvas) { margin: 0 auto; display: block; }
.vnc-error {
  position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
}
</style>
