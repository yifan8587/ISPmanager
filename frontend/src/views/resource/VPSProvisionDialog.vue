<template>
  <el-dialog
    :model-value="modelValue"
    title="节点下发创建虚机"
    width="780px"
    destroy-on-close
    @update:model-value="v => $emit('update:modelValue', v)"
    @open="onOpen"
  >
    <el-form :model="form" label-width="110px" v-loading="resLoading" :element-loading-text="resLoadingText">
      <el-divider content-position="left">目标节点</el-divider>
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="POP 节点" required>
            <el-select v-model="form.pop_point" filterable placeholder="选择 POP 节点"
              style="width:100%" @change="loadResources">
              <el-option v-for="p in popPoints" :key="p.id"
                :label="`${p.name}（${p.city_info?.name || p.code || ''}）`" :value="p.id" />
            </el-select>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="PVE 主机" required>
            <el-select v-model="form.host_id" placeholder="选择 PVE 主机" style="width:100%"
              :disabled="!hosts.length" @change="onHostChange">
              <el-option v-for="h in hosts" :key="h.id"
                :label="`${h.name}（${h.host}）`" :value="h.id" />
            </el-select>
          </el-form-item>
        </el-col>
      </el-row>
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="PVE 节点" required>
            <el-select v-model="form.node" placeholder="选择 / 输入节点名" style="width:100%"
              filterable allow-create default-first-option :disabled="!form.host_id"
              @change="onNodeChange">
              <el-option v-for="n in nodeOptions" :key="n.node"
                :label="nodeOptionLabel(n)"
                :value="n.node" />
            </el-select>
            <div v-if="selectedHost?.node_error" class="hint">
              实时读取节点失败：{{ selectedHost.node_error }}（已按 default_node / 已有虚机兜底，可手动输入节点名）
            </div>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="创建方式">
            <el-radio-group v-model="form.mode">
              <el-radio-button label="template">Cloud 镜像 / 模板</el-radio-button>
              <el-radio-button label="blank">空白虚机</el-radio-button>
            </el-radio-group>
          </el-form-item>
        </el-col>
      </el-row>

      <template v-if="form.mode === 'template'">
        <el-form-item label="选择模板" required>
          <el-select v-model="form.template_id" filterable placeholder="选择 Cloud 镜像或模板虚机"
            style="width:100%" :disabled="!filteredTemplates.length" @change="onTemplateChange">
            <el-option v-for="t in filteredTemplates" :key="t.id"
              :label="`${t.name} · ${tplTypeLabel(t.source_type)}${t.node ? ' @' + t.node : ''}`"
              :value="t.id" />
          </el-select>
          <div v-if="!filteredTemplates.length && form.host_id" class="hint">
            该主机暂无可用 Cloud 镜像，请先在节点「PVE 模板库」下载发行版 .qcow2/.img。
          </div>
          <div v-else-if="selectedTemplate?.source_type === 'cloud'" class="hint info">
            将从 Cloud 镜像 import-from 建机。镜像位于节点 {{ selectedTemplate.node || form.node || '（模板未记录节点）' }}
            <span v-if="selectedTemplate.volume">，卷 {{ selectedTemplate.volume }}</span>。
            磁盘请选 local-lvm 等 images 存储。开通时写入用户/密码/IP。
          </div>
          <div v-else-if="selectedTemplate?.source_type === 'template_vm'" class="hint info">
            将克隆模板虚机{{ selectedTemplate.node ? `（节点 ${selectedTemplate.node}）` : '' }}。
          </div>
        </el-form-item>
      </template>

      <el-divider content-position="left">虚机规格</el-divider>
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="虚机名称" required>
            <el-input v-model="form.name" placeholder="如 tokyo-web-01（仅字母数字与连字符）" />
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="VMID" required>
            <el-input-number v-model="form.vmid" :min="100" :max="999999999"
              controls-position="right" style="width:100%" placeholder="自动取节点最大 VMID+1" />
            <div class="hint info" v-if="nextVmid">
              已自动取节点现有最大 VMID + 1 = {{ nextVmid }}（如需可手动调整）
            </div>
          </el-form-item>
        </el-col>
      </el-row>
      <el-row :gutter="16">
        <el-col :span="8">
          <el-form-item label="vCPU">
            <el-input-number v-model="form.cores" :min="1" :max="128" style="width:100%" />
          </el-form-item>
        </el-col>
        <el-col :span="8">
          <el-form-item label="内存(GB)">
            <el-input-number v-model="memoryGb" :min="1" :step="1" style="width:100%" />
          </el-form-item>
        </el-col>
        <el-col :span="8">
          <el-form-item label="磁盘(GB)">
            <el-input-number v-model="form.disk_gb" :min="1" style="width:100%" />
          </el-form-item>
        </el-col>
      </el-row>
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="磁盘存储">
            <el-select v-model="form.storage" filterable clearable
              :placeholder="form.mode === 'template' ? '留空用模板默认' : '选择存储'" style="width:100%">
              <el-option v-for="s in imageStorages" :key="s.storage"
                :label="`${s.storage}（${s.type || ''}）`" :value="s.storage" />
            </el-select>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="网桥">
            <el-input v-model="form.bridge" placeholder="vmbr0" />
          </el-form-item>
        </el-col>
      </el-row>
      <el-row v-if="form.mode === 'blank'" :gutter="16">
        <el-col :span="24">
          <el-form-item label="安装 ISO">
            <el-input v-model="form.iso_volume" clearable
              placeholder="可选，volid 形如 local:iso/ubuntu-22.04.iso；留空挂空光驱" />
          </el-form-item>
        </el-col>
      </el-row>

      <el-collapse v-model="activePanels" style="margin-top:4px">
        <el-collapse-item title="Cloud-init / 初始化（可选）" name="ci">
          <el-row :gutter="16">
            <el-col :span="12">
              <el-form-item label="用户名">
                <el-input v-model="form.ci_user" placeholder="如 ubuntu" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="密码">
                <el-input v-model="form.ci_password" type="password" show-password />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item label="IP 配置">
            <el-input v-model="form.ci_ipconfig"
              placeholder="如 ip=10.0.0.5/24,gw=10.0.0.1（留空则 DHCP）" />
          </el-form-item>
          <el-row :gutter="16">
            <el-col :span="12">
              <el-form-item label="DNS">
                <el-input v-model="form.ci_nameserver" placeholder="223.5.5.5 1.1.1.1" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="SSH 公钥">
                <el-input v-model="form.ci_sshkeys" type="textarea" :rows="1" placeholder="ssh-ed25519 ..." />
              </el-form-item>
            </el-col>
          </el-row>
        </el-collapse-item>
      </el-collapse>

      <el-form-item label="创建后启动">
        <el-switch v-model="form.start_after_create" />
      </el-form-item>
    </el-form>

    <el-alert v-if="jobProgress" type="warning" :closable="false" show-icon style="margin-top:4px">
      {{ jobProgress }}
    </el-alert>
    <el-alert v-if="preview" type="info" :closable="false" show-icon style="margin-top:4px">
      <template #title>下发预览（dry-run）</template>
      <pre class="preview-json">{{ JSON.stringify(preview.payload || preview, null, 2) }}</pre>
    </el-alert>

    <template #footer>
      <el-button @click="$emit('update:modelValue', false)">取消</el-button>
      <el-button :loading="busy" @click="submit(true)">预览计划</el-button>
      <el-button type="primary" :loading="busy" @click="submit(false)">确认创建</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, reactive, computed } from 'vue'
import { gbToMb, mbToGb } from '@/utils/ram'
import { ElMessage } from 'element-plus'
import { getPopPVEResources, createPopVM, getPopPVEJob } from '@/api/resource'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  popPoints: { type: Array, default: () => [] },
  defaultPopId: { type: [Number, String], default: null },
})
const emit = defineEmits(['update:modelValue', 'created'])

const resLoading = ref(false)
const resLoadingText = ref('正在读取节点 PVE 资源…')
const busy = ref(false)
const preview = ref(null)
const jobProgress = ref('')
const activePanels = ref([])

const hosts = ref([])
const templates = ref([])
const nextVmid = ref(null)

const form = reactive({
  pop_point: null,
  host_id: null,
  node: '',
  mode: 'template',
  template_id: null,
  name: '',
  vmid: null,
  cores: 2,
  memory_mb: 2048,
  disk_gb: 40,
  storage: '',
  bridge: 'vmbr0',
  iso_volume: '',
  ci_user: 'ubuntu',
  ci_password: '',
  ci_ipconfig: '',
  ci_nameserver: '223.5.5.5 1.1.1.1',
  ci_sshkeys: '',
  start_after_create: true,
})
const memoryGb = computed({
  get: () => mbToGb(form.memory_mb),
  set: (gb) => { form.memory_mb = gbToMb(gb) },
})

const TPL_TYPE = {
  cloud: 'Cloud 镜像', template_vm: '模板机', backup: '备份', vztmpl: 'LXC 模板', iso: 'ISO',
}
function tplTypeLabel(t) { return TPL_TYPE[t] || t || '-' }
function nodeOptionLabel(n) {
  if (!n?.status || n.status === 'unknown') return n.node
  if (n.status === 'template') return `${n.node}（模板所在节点）`
  return `${n.node}（${n.status}）`
}

const selectedHost = computed(() => hosts.value.find(h => h.id === form.host_id) || null)
const selectedTemplate = computed(() => templates.value.find(x => x.id === form.template_id) || null)
const nodeOptions = computed(() => selectedHost.value?.nodes || [])
const filteredTemplates = computed(() =>
  templates.value.filter((t) => {
    if (t.source_type === 'iso') return false
    if (t.is_active === false) return false
    return !form.host_id || t.host === form.host_id
  }))
const imageStorages = computed(() => {
  const list = selectedHost.value?.storages || []
  return list.filter((s) => {
    const content = String(s.content || '')
    const okContent = content.includes('images') || content.includes('rootdir')
    const okNode = !form.node || !s.node || s.node === form.node
    return okContent && okNode
  })
})

function resetResources() {
  hosts.value = []
  templates.value = []
  nextVmid.value = null
  form.host_id = null
  form.node = ''
  form.template_id = null
  preview.value = null
}

async function onOpen() {
  preview.value = null
  if (props.defaultPopId && !form.pop_point) {
    form.pop_point = Number(props.defaultPopId)
  }
  if (form.pop_point) await loadResources()
}

async function loadResources() {
  if (!form.pop_point) return
  const popId = form.pop_point
  resetResources()
  form.pop_point = popId
  resLoading.value = true
  try {
    const res = await getPopPVEResources(form.pop_point)
    hosts.value = res?.hosts || []
    templates.value = res?.templates || []
    // 自动回填 VMID：节点现有最大 VMID + 1
    nextVmid.value = res?.next_vmid || null
    if (nextVmid.value) form.vmid = nextVmid.value
    if (hosts.value.length === 1) {
      form.host_id = hosts.value[0].id
      onHostChange()
    }
  } catch (e) {
    ElMessage.error('读取节点 PVE 资源失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    resLoading.value = false
  }
}

function onHostChange() {
  form.node = ''
  form.template_id = null
  const h = selectedHost.value
  if (h) {
    const def = h.preferred_node || h.default_node || (h.nodes?.[0]?.node || '')
    form.node = def
  }
  // 自动选择该主机下唯一模板
  const tpls = filteredTemplates.value
  if (tpls.length === 1) {
    form.template_id = tpls[0].id
    onTemplateChange()
  }
}

function onNodeChange() {
  // 节点变化时清掉跨节点不可用的存储
  if (form.storage && !imageStorages.value.some(s => s.storage === form.storage)) {
    form.storage = ''
  }
}

function sanitizeVmName(name) {
  let r = String(name || '').trim().replace(/[^A-Za-z0-9-]+/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '')
  if (!r || !/^[A-Za-z]/.test(r)) r = `vm-${r}`.replace(/^-|-$/g, '')
  r = r.slice(0, 63).replace(/-+$/, '')
  return r || 'vm'
}

function pickImageStorage() {
  const list = imageStorages.value
  if (!list.length) return ''
  const prefer = list.find((s) => {
    const type = String(s.type || '')
    const name = String(s.storage || '')
    return ['lvmthin', 'lvm', 'zfspool', 'rbd', 'cephfs'].includes(type) || name.includes('lvm')
  })
  return (prefer || list[0]).storage
}

function onTemplateChange() {
  const t = templates.value.find(x => x.id === form.template_id)
  if (!t) return
  if (t.default_cores) form.cores = t.default_cores
  if (t.default_memory_mb) form.memory_mb = t.default_memory_mb
  if (t.default_disk_gb) form.disk_gb = t.default_disk_gb
  if (t.default_bridge) form.bridge = t.default_bridge
  if (t.node) form.node = t.node
  onNodeChange()
  if (t.source_type === 'cloud' || t.source_type === 'backup' || t.source_type === 'vztmpl') {
    const currentOk = form.storage && imageStorages.value.some((s) => s.storage === form.storage)
    if (!currentOk) form.storage = pickImageStorage()
  }
  if (t.source_type === 'cloud') {
    const text = `${t.name || ''} ${t.filename || ''}`.toLowerCase()
    form.ci_user = text.includes('debian') ? 'debian' : (text.includes('alma') ? 'almalinux' : (text.includes('rocky') ? 'rocky' : 'ubuntu'))
    if (!form.ci_nameserver) form.ci_nameserver = '223.5.5.5 1.1.1.1'
    form.start_after_create = true
  }
}

function buildCloudInit() {
  const ci = {}
  if (form.ci_user) ci.user = form.ci_user
  if (form.ci_password) ci.password = form.ci_password
  if (form.ci_ipconfig) ci.ipconfig = form.ci_ipconfig
  if (form.ci_nameserver) ci.nameserver = form.ci_nameserver
  if (form.ci_sshkeys) ci.sshkeys = form.ci_sshkeys
  return Object.keys(ci).length ? ci : null
}

function validate() {
  if (!form.pop_point) { ElMessage.warning('请选择 POP 节点'); return false }
  if (!form.host_id) { ElMessage.warning('请选择 PVE 主机'); return false }
  if (!form.node) { ElMessage.warning('请选择 PVE 节点'); return false }
  if (!form.name) { ElMessage.warning('请填写虚机名称'); return false }
  const safeName = sanitizeVmName(form.name)
  if (safeName !== form.name) {
    form.name = safeName
    ElMessage.info(`名称已规范化为：${safeName}`)
  }
  if (!form.vmid || form.vmid < 100) { ElMessage.warning('请填写有效 VMID（≥100）'); return false }
  if (form.mode === 'template' && !form.template_id) { ElMessage.warning('请选择模板'); return false }
  if (form.mode === 'template' && ['cloud', 'backup'].includes(selectedTemplate.value?.source_type) && !form.storage) {
    ElMessage.warning('请选择可放虚机磁盘的存储（如 local-lvm），不要用 iso/backup 目录存储')
    return false
  }
  return true
}

async function waitForJob(popId, jobId) {
  const started = Date.now()
  const timeoutMs = 35 * 60 * 1000
  while (Date.now() - started < timeoutMs) {
    const job = await getPopPVEJob(popId, jobId)
    if (job?.status === 'succeeded') return job
    if (job?.status === 'failed') {
      throw new Error(job.error || job.detail || '节点创建任务失败')
    }
    jobProgress.value = job?.progress || '节点正在从备份/模板创建虚机…'
    await new Promise((resolve) => setTimeout(resolve, 2000))
  }
  throw new Error('等待节点创建任务超时')
}

function buildPayload(dryRun) {
  const ci = buildCloudInit()
  const base = {
    from_template: form.mode === 'template',
    dry_run: dryRun,
    host_id: form.host_id,
    node: form.node,
    vmid: form.vmid,
    cores: form.cores,
    memory_mb: form.memory_mb,
    disk_gb: form.disk_gb,
    bridge: form.bridge || 'vmbr0',
    start_after_create: form.start_after_create,
  }
  if (form.storage) base.storage = form.storage
  if (ci) base.cloud_init = ci
  if (form.mode === 'template') {
    base.template_id = form.template_id
    base.newname = sanitizeVmName(form.name)
    if (selectedTemplate.value?.source_type) {
      base.source_type = selectedTemplate.value.source_type
    }
    if (selectedTemplate.value?.node) {
      base.node = selectedTemplate.value.node
    }
    if (selectedTemplate.value?.download_url) {
      base.download_url = selectedTemplate.value.download_url
    }
  } else {
    base.name = form.name
    if (form.iso_volume) base.iso_volume = form.iso_volume
  }
  return base
}

async function submit(dryRun) {
  if (!validate()) return
  busy.value = true
  preview.value = null
  jobProgress.value = ''
  try {
    const res = await createPopVM(form.pop_point, buildPayload(dryRun))
    if (dryRun) {
      preview.value = res
      ElMessage.success('已生成下发计划，确认无误后点「确认创建」')
    } else if (res?.job_id && res?.status !== 'succeeded') {
      ElMessage.info(res.progress || '已提交节点任务，正在等待备份恢复…')
      const job = await waitForJob(form.pop_point, res.job_id)
      ElMessage.success(`虚机已创建：${job?.result?.name || form.name}（VMID ${job?.result?.vmid || form.vmid}）`)
      emit('created')
      emit('update:modelValue', false)
    } else {
      ElMessage.success('虚机创建已下发到节点')
      emit('created')
      emit('update:modelValue', false)
    }
  } catch (e) {
    const detail = e?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' ? detail : (e.message || '创建失败'))
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.hint { color: #e6a23c; font-size: 12px; margin-top: 4px; line-height: 1.4; }
.hint.info { color: #909399; }
.preview-json {
  margin: 0;
  max-height: 220px;
  overflow: auto;
  font-size: 12px;
  font-family: Menlo, Monaco, Consolas, monospace;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
