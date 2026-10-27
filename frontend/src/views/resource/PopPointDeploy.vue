<template>
  <div class="page-container" v-loading="pageLoading">
  <div class="page-header">
    <div class="page-header-left">
      <el-button :icon="ArrowLeft" @click="goBack">返回列表</el-button>
      <h2 class="page-title">POP 节点部署 / WebSSH</h2>
      <template v-if="pop">
        <el-tag type="primary" size="small">{{ pop.code }}</el-tag>
        <span class="pop-name">{{ pop.name }}</span>
      </template>
    </div>
    <div v-if="pop" class="page-header-right">
      <el-tag :type="deployTagType(pop.deploy_status)" size="small">
        {{ pop.deploy_status_label || '未部署' }}
      </el-tag>
      <el-button link type="primary" @click="loadPop">刷新节点信息</el-button>
    </div>
  </div>

  <template v-if="pop">
    <el-alert v-if="pop.config_dirty" type="warning" :closable="false" show-icon
      style="margin-bottom:12px">
      <template #title>
        <span>该 POP 点存在未下发到节点的配置变更{{ pop.config_dirty_at ? `（${formatTime(pop.config_dirty_at)}）` : '' }}，请预览并下发以保持前后端一致。</span>
        <el-button size="small" type="warning" :loading="pushing" style="margin-left:12px"
          @click="quickDeploy">一键预览并下发</el-button>
      </template>
    </el-alert>
    <el-row :gutter="16" class="deploy-layout">
      <!-- 左侧：连接与部署操作 -->
      <el-col :xs="24" :lg="8">
        <el-card shadow="never" class="panel-card">
          <template #header>节点信息</template>
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="管理 IP">{{ pop.mgmt_ip || '-' }}</el-descriptions-item>
            <el-descriptions-item label="SSH 端口">{{ pop.ssh_port || 22 }}</el-descriptions-item>
            <el-descriptions-item label="节点类型">{{ pop.pop_type_label || pop.pop_type }}</el-descriptions-item>
            <el-descriptions-item label="城市">
              {{ pop.city_info?.country_name }} · {{ pop.city_info?.name }}
            </el-descriptions-item>
            <el-descriptions-item label="API 基址">
              {{ pop.api_base_url || (pop.mgmt_ip ? `http://${pop.mgmt_ip}` : '-') }}
            </el-descriptions-item>
            <el-descriptions-item label="最近同步">
              {{ pop.last_sync_at ? formatTime(pop.last_sync_at) : '未同步' }}
            </el-descriptions-item>
          </el-descriptions>
        </el-card>

        <el-card shadow="never" class="panel-card">
          <template #header>SSH 连接</template>
          <el-form :model="ssh" label-width="72px" size="default">
            <el-form-item label="主机">
              <el-input v-model="ssh.host" placeholder="管理 IP / 主机" :disabled="connected" />
            </el-form-item>
            <el-form-item label="端口">
              <el-input-number v-model="ssh.port" :min="1" :max="65535" :controls="false" style="width:100%" :disabled="connected" />
            </el-form-item>
            <el-form-item label="用户名">
              <el-input v-model="ssh.username" :disabled="connected" />
            </el-form-item>
            <el-form-item label="密码">
              <el-input v-model="ssh.password" type="password" show-password :disabled="connected" />
            </el-form-item>
            <el-form-item>
              <el-button v-if="!connected" type="primary" :icon="Connection" :loading="connecting" @click="connect">连接</el-button>
              <el-button v-else type="danger" :icon="SwitchButton" @click="disconnect">断开</el-button>
              <el-tag :type="connected ? 'success' : 'info'" size="small" style="margin-left:8px">
                {{ connected ? '已连接' : '未连接' }}
              </el-tag>
            </el-form-item>
          </el-form>
        </el-card>

        <el-card shadow="never" class="panel-card">
          <template #header>部署包 / 数据同步</template>
          <div class="deploy-panel">
            <el-upload
              ref="uploadRef"
              :auto-upload="false"
              :show-file-list="true"
              :limit="1"
              accept=".gz,.tgz,.tar,application/gzip,application/x-gzip,application/x-tar,application/x-gtar,application/x-compressed-tar"
              :on-change="onFileChange"
              :on-remove="() => (selectedFile = null)"
            >
              <el-button :icon="UploadFilled">选择部署包(.tar.gz)</el-button>
            </el-upload>

            <el-input v-model="deployArgs" placeholder="如 --offline --ssl-ip" style="margin-top:12px">
              <template #prepend>INSTALL.sh</template>
            </el-input>

            <el-progress
              v-if="uploading || uploadPct > 0"
              :percentage="uploadPct"
              :stroke-width="10"
              style="margin-top:12px"
            />

            <div class="deploy-btns">
              <el-button
                type="primary"
                :icon="Upload"
                :loading="uploading"
                :disabled="!selectedFile"
                @click="uploadAndDeploy"
              >上传并部署</el-button>
              <el-button type="success" :icon="Refresh" :loading="syncing" @click="doSync">同步系统数据</el-button>
              <el-button type="warning" :icon="CircleCheck" @click="markDeployed">标记部署完成</el-button>
            </div>
          </div>
        </el-card>

        <el-card shadow="never" class="panel-card">
          <template #header>配置下发到节点</template>
          <div class="deploy-panel">
            <el-checkbox-group v-model="pushSections" class="push-sections">
              <el-checkbox label="customers">客户信息</el-checkbox>
              <el-checkbox label="static_routes">静态路由</el-checkbox>
              <el-checkbox label="policy_rules">策略路由</el-checkbox>
              <el-checkbox label="edge_tunnels">边缘隧道</el-checkbox>
              <el-checkbox label="bandwidth">带宽限速</el-checkbox>
            </el-checkbox-group>
            <div class="deploy-btns">
              <el-button type="primary" :icon="View" :loading="pushing" @click="doPush(true)">预览下发计划</el-button>
              <el-button type="danger" :icon="Promotion" :loading="pushing"
                :disabled="!pushPreviewed" @click="confirmPush">确认下发到节点</el-button>
            </div>
            <el-alert v-if="!pushPreviewed" type="info" :closable="false" show-icon
              title="先预览（dry-run），确认计划无误后再正式下发" style="margin-top:8px" />
          </div>
        </el-card>

        <el-card v-if="pushResult" shadow="never" class="panel-card sync-result">
          <template #header>
            <span>下发{{ pushResult.dry_run ? '预览' : '结果' }}</span>
            <el-tag :type="pushResult.ok ? 'success' : 'danger'" size="small" style="margin-left:8px">
              {{ pushResult.dry_run ? '计划' : (pushResult.ok ? '成功' : '部分失败') }}
            </el-tag>
          </template>
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item v-for="(v, k) in pushResult.summary" :key="k" :label="sectionLabel(k)">
              <template v-if="typeof v === 'object'">
                <el-tag size="small" type="primary" v-if="v.would_push != null">将下发 {{ v.would_push }}</el-tag>
                <el-tag size="small" type="success" v-if="v.would_create != null" style="margin-left:4px">将新建 {{ v.would_create }}</el-tag>
                <el-tag size="small" type="warning" v-if="v.would_update != null" style="margin-left:4px">将更新 {{ v.would_update }}</el-tag>
                <el-tag size="small" type="success" v-if="v.pushed != null">已下发 {{ v.pushed }}</el-tag>
                <el-tag size="small" type="success" v-if="v.created" style="margin-left:4px">新建 {{ v.created }}</el-tag>
                <el-tag size="small" type="warning" v-if="v.updated" style="margin-left:4px">更新 {{ v.updated }}</el-tag>
                <el-tag size="small" type="warning" v-if="v.patched" style="margin-left:4px">纠正 {{ v.patched }}</el-tag>
                <el-tag size="small" type="warning" v-if="v.applied != null" style="margin-left:4px">生效 {{ v.applied }}</el-tag>
                <el-tag size="small" type="success" v-if="v.bandwidth_pushed" style="margin-left:4px">带宽 {{ v.bandwidth_pushed }}</el-tag>
                <el-tag size="small" type="info" v-if="v.skipped" style="margin-left:4px">忽略 {{ v.skipped }}</el-tag>
                <el-tag size="small" type="info" v-if="v.socks5_skipped" style="margin-left:4px">Socks5跳过 {{ v.socks5_skipped }}</el-tag>
                <div v-if="v.skipped_reasons && Object.keys(v.skipped_reasons).length" class="sync-note">
                  忽略原因：<span v-for="(cnt, rk) in v.skipped_reasons" :key="rk">{{ rk }}×{{ cnt }} </span>
                </div>
                <div v-if="v.note" class="sync-note">{{ v.note }}</div>
                <div v-if="v.plan && v.plan.length" class="push-plan">
                  <div v-for="(item, i) in v.plan" :key="i" class="push-plan-item">
                    {{ formatPlanItem(item) }}
                  </div>
                </div>
              </template>
              <span v-else>{{ v }}</span>
            </el-descriptions-item>
          </el-descriptions>
          <div v-if="pushResult.errors && Object.keys(pushResult.errors).length" class="sync-errors">
            <el-alert v-for="(msg, k) in pushResult.errors" :key="k"
              :title="`${sectionLabel(k)}：${msg}`" type="error" :closable="false" style="margin-bottom:4px" />
          </div>
        </el-card>

        <el-card v-if="syncResult" shadow="never" class="panel-card sync-result">
          <template #header>
            <span>同步结果</span>
            <el-tag :type="syncResult.ok ? 'success' : 'danger'" size="small" style="margin-left:8px">
              {{ syncResult.ok ? '成功' : '部分失败' }}
            </el-tag>
          </template>
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item v-for="(v, k) in syncResult.summary" :key="k" :label="sectionLabel(k)">
              <template v-if="typeof v === 'object'">
                <el-tag size="small" type="success" v-if="v.created != null">新增 {{ v.created }}</el-tag>
                <el-tag size="small" type="warning" v-if="v.updated != null" style="margin-left:4px">更新 {{ v.updated }}</el-tag>
                <el-tag size="small" type="danger" v-if="v.deleted" style="margin-left:4px">清理 {{ v.deleted }}</el-tag>
                <el-tag size="small" v-if="v.tables != null" style="margin-left:4px">路由表 {{ v.tables }}</el-tag>
                <el-tag size="small" v-if="v.blocks_created != null" style="margin-left:4px">
                  地址块 +{{ v.blocks_created }}/{{ (v.blocks_updated || 0) }}
                </el-tag>
                <el-tag size="small" type="info" v-if="v.total != null" style="margin-left:4px">共 {{ v.total }}</el-tag>
                <el-tag size="small" type="info" v-if="v.fetched != null" style="margin-left:4px">拉取 {{ v.fetched }}</el-tag>
                <el-tag size="small" type="primary" v-if="v.allocated_mbps" style="margin-left:4px">{{ v.allocated_mbps }} Mbps</el-tag>
                <el-tag size="small" type="info" v-if="v.skipped" style="margin-left:4px">忽略 {{ v.skipped }}</el-tag>
                <div v-if="v.note" class="sync-note">{{ v.note }}</div>
              </template>
              <span v-else>{{ v }}</span>
            </el-descriptions-item>
          </el-descriptions>
          <div v-if="syncResult.errors && Object.keys(syncResult.errors).length" class="sync-errors">
            <el-alert
              v-for="(msg, k) in syncResult.errors"
              :key="k"
              :title="`${sectionLabel(k)}：${msg}`"
              type="error"
              :closable="false"
              style="margin-bottom:4px"
            />
          </div>
        </el-card>
      </el-col>

      <!-- 右侧：WebSSH 终端 -->
      <el-col :xs="24" :lg="16">
        <el-card shadow="never" class="terminal-card">
          <template #header>
            <div class="terminal-header">
              <span>WebSSH 终端</span>
              <el-text type="info" size="small">连接后可手动操作，或通过左侧「上传并部署」自动执行 INSTALL.sh</el-text>
            </div>
          </template>
          <div ref="termEl" class="terminal-box"></div>
        </el-card>
      </el-col>
    </el-row>
  </template>

  <el-empty v-else-if="!pageLoading" description="节点不存在或加载失败">
    <el-button type="primary" @click="goBack">返回列表</el-button>
  </el-empty>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ArrowLeft, Connection, SwitchButton, UploadFilled, Upload, Refresh, CircleCheck,
  View, Promotion
} from '@element-plus/icons-vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import dayjs from 'dayjs'
import { ElMessageBox } from 'element-plus'
import {
  getPopPointDetail, uploadPopPackage, syncPopFromNode, setPopDeployStatus,
  popSSHWebSocketURL, pushPopToNode
} from '@/api/resource'
import { wsAuthProtocols } from '@/api/wsAuth'
import { isArchiveFilename } from '@/utils/archiveUpload'

const route = useRoute()
const router = useRouter()

const popId = computed(() => Number(route.params.id))
const pop = ref(null)
const pageLoading = ref(false)

const termEl = ref(null)
const uploadRef = ref(null)
const connected = ref(false)
const connecting = ref(false)
const uploading = ref(false)
const syncing = ref(false)
const uploadPct = ref(0)
const selectedFile = ref(null)
const deployArgs = ref('')
const syncResult = ref(null)
const uploadedPackage = ref(null)
const remoteDir = ref('/tmp/pop_deploy')

// 配置下发
const pushing = ref(false)
const pushSections = ref(['static_routes', 'policy_rules'])
const pushResult = ref(null)
const pushPreviewed = ref(false)

const ssh = reactive({ host: '', port: 22, username: 'root', password: '' })

let term = null
let fitAddon = null
let ws = null
let resizeObs = null

function goBack() {
  router.push('/resource/pop')
}

function formatTime(t) {
  return dayjs(t).format('YYYY-MM-DD HH:mm:ss')
}

function deployTagType(s) {
  return { deployed: 'success', deploying: 'warning', failed: 'danger', undeployed: 'info' }[s] || 'info'
}

function sectionLabel(k) {
  return {
    interfaces: '网络接口', static_routes: '静态路由', policy_rules: '策略路由',
    customers: '客户信息', edge_tunnels: '边缘隧道',
    ip_addresses: 'IP 地址', bandwidth: '带宽分配', route_tables: '路由表',
    vms: 'PVE 虚机'
  }[k] || k
}

function resetDeployForm() {
  syncResult.value = null
  uploadPct.value = 0
  selectedFile.value = null
  uploadedPackage.value = null
  if (uploadRef.value) uploadRef.value.clearFiles()
}

function applyPopToSsh(data) {
  ssh.host = data.mgmt_ip || ''
  ssh.port = data.ssh_port || 22
  ssh.username = data.mgmt_username || 'root'
  ssh.password = ''
}

async function loadPop() {
  if (!popId.value) return
  pageLoading.value = true
  try {
    const data = await getPopPointDetail(popId.value)
    pop.value = data
    applyPopToSsh(data)
    resetDeployForm()
  } catch (e) {
    pop.value = null
    ElMessage.error('加载节点失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    pageLoading.value = false
  }
}

function initTerminal() {
  if (term || !termEl.value) return
  term = new Terminal({
    cursorBlink: true,
    fontSize: 14,
    fontFamily: 'Menlo, Monaco, Consolas, "Courier New", monospace',
    theme: { background: '#1e1e2e', foreground: '#cdd6f4' }
  })
  fitAddon = new FitAddon()
  term.loadAddon(fitAddon)
  term.open(termEl.value)
  fitAddon.fit()
  term.writeln('\x1b[36m欢迎使用 WebSSH —— 填写左侧 SSH 信息并点击「连接」\x1b[0m')

  term.onData((d) => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'input', data: d }))
    }
  })
  term.onResize(({ cols, rows }) => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'resize', cols, rows }))
    }
  })
  resizeObs = new ResizeObserver(() => {
    try { fitAddon?.fit() } catch (e) { /* ignore */ }
  })
  resizeObs.observe(termEl.value)
}

function connect() {
  if (!pop.value) return
  if (!ssh.host) { ElMessage.warning('请填写主机地址'); return }
  connecting.value = true
  ws = new WebSocket(popSSHWebSocketURL(pop.value.id), wsAuthProtocols())

  ws.onopen = () => {
    ws.send(JSON.stringify({
      type: 'open', host: ssh.host, port: ssh.port,
      username: ssh.username, password: ssh.password
    }))
  }
  ws.onmessage = (ev) => {
    let msg
    try { msg = JSON.parse(ev.data) } catch { return }
    if (msg.type === 'data') {
      term?.write(msg.data)
    } else if (msg.type === 'status') {
      connecting.value = false
      const color = msg.level === 'error' ? '31' : msg.level === 'success' ? '32' : '36'
      term?.writeln(`\x1b[${color}m[${msg.level}] ${msg.msg}\x1b[0m`)
      if (msg.level === 'success' && msg.msg.includes('SSH 连接成功')) {
        connected.value = true
        nextTick(() => { try { fitAddon?.fit() } catch (e) { /* ignore */ } })
      }
      if (msg.level === 'error') ElMessage.error(msg.msg)
    } else if (msg.type === 'closed') {
      connected.value = false
      term?.writeln('\x1b[33m[会话已关闭]\x1b[0m')
    }
  }
  ws.onclose = () => {
    connected.value = false
    connecting.value = false
    term?.writeln('\x1b[33m[WebSocket 已断开]\x1b[0m')
  }
  ws.onerror = () => {
    connecting.value = false
    ElMessage.error('WebSocket 连接错误，请检查后端是否以 ASGI 启动')
  }
}

function disconnect() {
  if (ws) { ws.close(); ws = null }
  connected.value = false
}

function onFileChange(file) {
  const raw = file.raw || null
  if (raw && !isArchiveFilename(raw.name)) {
    ElMessage.error('请选择 .tar.gz / .tgz / .tar 部署包')
    selectedFile.value = null
    return
  }
  selectedFile.value = raw
}

async function uploadAndDeploy() {
  if (!selectedFile.value) { ElMessage.warning('请先选择部署包'); return }
  if (!connected.value) { ElMessage.warning('请先建立 SSH 连接'); return }
  uploading.value = true
  uploadPct.value = 0
  try {
    const res = await uploadPopPackage(pop.value.id, selectedFile.value, (p) => { uploadPct.value = p })
    uploadedPackage.value = res.package
    remoteDir.value = res.remote_dir || '/tmp/pop_deploy'
    ElMessage.success('部署包上传成功，开始在终端执行部署')
    await loadPop()
    ws.send(JSON.stringify({
      type: 'deploy',
      package: uploadedPackage.value,
      remote_dir: remoteDir.value,
      args: deployArgs.value
    }))
  } catch (e) {
    ElMessage.error('上传失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    uploading.value = false
  }
}

async function doSync() {
  syncing.value = true
  syncResult.value = null
  try {
    const res = await syncPopFromNode(pop.value.id)
    syncResult.value = res
    if (res.ok) ElMessage.success('节点数据同步完成')
    else ElMessage.warning('同步完成，但部分区块失败，详见结果')
    await loadPop()
  } catch (e) {
    ElMessage.error('同步失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    syncing.value = false
  }
}

function formatPlanItem(item) {
  if (item.dest) return `${item.dest} → ${item.via} (${item.dev}) [${item.action}]`
  if (item.priority != null) return `prio ${item.priority} ${item.family} ${item.from}→${item.to} ${item.action} [${item.state}]`
  if (item.ifname) return `${item.protocol} ${item.ifname} ${item.name || ''} [${item.state}]`
  if (item.vm_id) return `${item.vps} net${item.net_index} ${item.mbps}Mbps`
  return JSON.stringify(item)
}

async function doPush(dryRun) {
  if (!pushSections.value.length) { ElMessage.warning('请至少选择一个下发区块'); return }
  pushing.value = true
  try {
    const res = await pushPopToNode(pop.value.id, pushSections.value, dryRun)
    pushResult.value = res
    if (dryRun) {
      pushPreviewed.value = true
      ElMessage.success('已生成下发计划，请确认后正式下发')
    } else {
      pushPreviewed.value = false
      if (res.ok) ElMessage.success('配置已下发到节点')
      else ElMessage.warning('下发完成，但部分区块失败，详见结果')
      await loadPop()
    }
  } catch (e) {
    ElMessage.error('下发失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    pushing.value = false
  }
}

async function confirmPush() {
  await ElMessageBox.confirm(
    '将把所选配置真正写入节点并即时生效，确定继续？',
    '确认下发', { type: 'warning' },
  )
  doPush(false)
}

// 顶部「待下发」横幅一键下发：全量区块先 dry-run 预览，确认后正式下发
async function quickDeploy() {
  pushSections.value = ['customers', 'static_routes', 'policy_rules', 'edge_tunnels', 'bandwidth']
  await doPush(true)
  if (!pushResult.value || !pushPreviewed.value) return
  try {
    await ElMessageBox.confirm(
      '已生成全量下发计划（见下方预览），确认正式写入节点并即时生效？',
      '确认一键下发', { type: 'warning', confirmButtonText: '确认下发', cancelButtonText: '取消' },
    )
  } catch { return }
  await doPush(false)
}

async function markDeployed() {
  try {
    await setPopDeployStatus(pop.value.id, { status: 'deployed' })
    ElMessage.success('已标记为部署完成')
    await loadPop()
  } catch (e) {
    ElMessage.error('操作失败：' + (e?.response?.data?.detail || e.message))
  }
}

onMounted(async () => {
  await loadPop()
  nextTick(() => initTerminal())
})

onBeforeUnmount(() => {
  disconnect()
  if (resizeObs) { resizeObs.disconnect(); resizeObs = null }
  if (term) { term.dispose(); term = null }
})
</script>

<style scoped>
.page-container { min-height: 100%; }
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
  gap: 12px;
  flex-wrap: wrap;
}
.page-header-left {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.page-title { font-size: 18px; font-weight: 700; color: #1a1a2e; margin: 0; }
.pop-name { color: #606266; font-size: 14px; }
.page-header-right { display: flex; align-items: center; gap: 8px; }
.deploy-layout { margin-top: 4px; }
.panel-card { margin-bottom: 12px; }
.panel-card :deep(.el-card__header) { font-weight: 600; }
.deploy-panel { display: flex; flex-direction: column; }
.deploy-btns {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 12px;
}
.deploy-btns .el-button { width: 100%; margin-left: 0; }
.terminal-card { min-height: calc(100vh - 180px); }
.terminal-card :deep(.el-card__body) { padding: 8px; }
.terminal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.terminal-box {
  height: calc(100vh - 240px);
  min-height: 480px;
  background: #1e1e2e;
  border-radius: 6px;
  padding: 6px;
  overflow: hidden;
}
.sync-note { color: #909399; font-size: 11px; margin-top: 4px; }
.sync-errors { margin-top: 8px; }
.push-sections { display: flex; flex-wrap: wrap; gap: 4px 16px; }
.push-plan { margin-top: 6px; max-height: 160px; overflow: auto; }
.push-plan-item { font-size: 11px; color: #606266; font-family: Menlo, Monaco, monospace; line-height: 1.6; }
</style>
