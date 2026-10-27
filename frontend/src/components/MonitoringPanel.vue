<template>
  <div v-loading="loading" class="mon-panel">
    <!-- 概览条 -->
    <div class="mp-bar">
      <span class="mp-host">{{ resourceName || '监控' }}</span>
      <el-tag size="small" effect="plain">已关联图形/监控项 {{ cardCount }}</el-tag>
      <div class="mp-actions">
        <el-button size="small" type="primary" :icon="Connection" @click="openBind">关联监控图形 / 监控项</el-button>
        <el-button size="small" :icon="DataLine" :disabled="!canOpenTraffic" @click="openTrafficPage">流量截取 / 95计费</el-button>
        <el-button size="small" :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>

    <!-- 图形 / 监控项卡片（按 slot 分组） -->
    <template v-if="cardCount">
      <div v-for="slot in slotOrder" :key="slot">
        <template v-if="panels[slot]?.length">
          <div class="mp-slot-title">{{ slotLabels[slot] || slot }}</div>
          <el-row :gutter="14">
            <el-col v-for="c in panels[slot]" :key="c.binding_id" :xs="24" :md="12" style="margin-bottom:14px;">
              <div class="mp-card">
                <el-button class="mp-del" link type="danger" size="small" :icon="Delete"
                  @click="removeBinding(c)" />
                <MonitoringChart
                  :graph-id="c.graph_id || null"
                  :item-id="c.item_id || null"
                  :title="c.title"
                  :height="220"
                  :default-period="defaultPeriod"
                />
                <div v-if="c.monitoring_host_name" class="mp-card-host">来源主机：{{ c.monitoring_host_name }}</div>
              </div>
            </el-col>
          </el-row>
        </template>
      </div>
    </template>

    <el-empty
      v-else-if="!loading"
      description="尚未关联监控图形 / 监控项，点击「关联监控图形 / 监控项」从监控主机中选择对应接口的图形或监控项。"
      :image-size="70"
    />

    <!-- ── 关联监控图形 / 监控项 ── -->
    <el-dialog v-model="bindVisible" title="关联监控图形 / 监控项" width="760px" destroy-on-close append-to-body>
      <el-alert v-if="popFilterHint" type="info" :closable="false" show-icon style="margin-bottom:12px;">
        {{ popFilterHint }}
      </el-alert>
      <el-form :model="bindForm" label-width="92px" size="small">
        <el-form-item label="监控主机" required>
          <el-select v-model="bindForm.host" filterable remote :remote-method="searchHosts"
            :loading="hostLoading" placeholder="搜索并选择监控主机" style="width:100%" @change="onHostChange">
            <el-option v-for="h in hostOptions" :key="h.id" :label="hostLabel(h)" :value="h.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="展示分组">
          <el-select v-model="bindForm.panel_slot" style="width:200px;">
            <el-option v-for="(lbl, k) in slotLabels" :key="k" :label="lbl" :value="k" />
          </el-select>
        </el-form-item>
      </el-form>

      <el-tabs v-if="bindForm.host" v-model="bindTab" v-loading="hostDetailLoading">
        <el-tab-pane name="graphs">
          <template #label>监控图形 <el-badge :value="hostGraphs.length" :max="99" /></template>
          <el-select v-model="bindForm.graphIds" multiple filterable placeholder="选择要展示的图形" style="width:100%;">
            <el-option v-for="g in hostGraphs" :key="g.id" :label="g.effective_title || g.name" :value="g.id" />
          </el-select>
        </el-tab-pane>
        <el-tab-pane name="items">
          <template #label>监控项 <el-badge :value="hostItems.length" :max="99" /></template>
          <el-select v-model="bindForm.itemIds" multiple filterable placeholder="选择要展示的监控项（如接口流量）" style="width:100%;">
            <el-option v-for="it in hostItems" :key="it.id"
              :label="`${it.name}${it.interface_name ? ' · ' + it.interface_name : ''}`" :value="it.id" />
          </el-select>
        </el-tab-pane>
      </el-tabs>

      <template #footer>
        <el-button @click="bindVisible = false">取消</el-button>
        <el-button type="primary" :loading="binding" @click="doBind">关联</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, DataLine, Connection, Delete } from '@element-plus/icons-vue'
import MonitoringChart from './MonitoringChart.vue'
import {
  getResourceMonitorPanel, getMonitoringHosts, getMonitoringHost,
  createGraphBinding, deleteGraphBinding,
} from '@/api/operation'

const props = defineProps({
  targetType: { type: String, required: true }, // pop | vps | edge
  resourceId: { type: [Number, String], required: true },
  resourceName: { type: String, default: '' },
  popPointId: { type: [Number, String], default: null },
  monitoredIp: { type: String, default: '' },
  defaultPeriod: { type: String, default: '24h' },
  active: { type: Boolean, default: true },
})

const router = useRouter()
const loading = ref(false)
const panels = ref({})
const hostIds = ref([])
const loaded = ref(false)

const slotOrder = ['traffic', 'overview', 'system', 'icmp', 'custom']
const slotLabels = { traffic: '流量', overview: '概览', system: '系统负载', icmp: '可用性', custom: '自定义' }

const cardCount = computed(() =>
  Object.values(panels.value).reduce((n, arr) => n + (arr?.length || 0), 0))

const canOpenTraffic = computed(() =>
  ['vps', 'edge'].includes(props.targetType) && props.resourceId)

const popFilterHint = computed(() => {
  if (props.targetType === 'pop') return '已筛选当前 POP 节点对应的监控主机'
  if (props.popPointId) return '仅显示归属该 POP 点的监控主机，避免误选其他节点'
  return ''
})

function extractError(e) {
  const d = e?.response?.data
  if (!d) return e?.message || '请求失败'
  const detail = d.detail ?? d
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.join('；')
  if (typeof detail === 'object') {
    return Object.entries(detail).map(([k, v]) => {
      const val = Array.isArray(v) ? v.join('，') : v
      return k === 'non_field_errors' ? val : `${k}: ${val}`
    }).join('；') || d.message || '请求失败'
  }
  return d.message || '请求失败'
}

async function load() {
  if (!props.resourceId) return
  loading.value = true
  try {
    const res = await getResourceMonitorPanel({
      target_type: props.targetType, resource_id: props.resourceId,
    })
    panels.value = res?.panels || {}
    hostIds.value = res?.monitoring_host_ids || []
    loaded.value = true
  } catch (e) {
    ElMessage.error('加载监控数据失败：' + extractError(e))
  } finally {
    loading.value = false
  }
}

watch(() => [props.active, props.resourceId], ([active, id]) => {
  if (active && id && !loaded.value) load()
}, { immediate: true })
watch(() => props.resourceId, () => { loaded.value = false; panels.value = {} })

// ── 关联监控图形 / 监控项 ────────────────────────────────────
const bindVisible = ref(false)
const binding = ref(false)
const bindTab = ref('graphs')
const hostOptions = ref([])
const hostLoading = ref(false)
const hostDetail = ref(null)
const hostDetailLoading = ref(false)
const bindForm = reactive({ host: null, panel_slot: 'traffic', graphIds: [], itemIds: [] })

const hostGraphs = computed(() => hostDetail.value?.graphs || [])
const hostItems = computed(() => hostDetail.value?.items || [])
const fkParam = computed(() => ({ pop: 'pop_point', vps: 'vps', edge: 'edge' }[props.targetType]))

function hostLabel(h) {
  const kind = h.host_kind === 'pop' ? 'POP' : '通用'
  const pop = h.pop_point_label || h.service_pop_point_label
  const popTag = pop ? ` · ${pop}` : ''
  return `[${kind}] ${h.visible_name || h.hostname}${popTag}${h.monitored_ip ? ' · ' + h.monitored_ip : ''}`
}

function hostSearchParams(q) {
  const params = { search: q || '', page_size: 30, is_enabled: true }
  if (props.targetType === 'pop') {
    params.pop_point = props.resourceId
  } else if (props.popPointId) {
    params.pop_point = props.popPointId
  }
  return params
}

async function searchHosts(q) {
  hostLoading.value = true
  try {
    const res = await getMonitoringHosts(hostSearchParams(q))
    hostOptions.value = res?.results || res || []
  } catch { /* ignore */ } finally { hostLoading.value = false }
}

async function loadHostDetail(id) {
  if (!id) { hostDetail.value = null; return }
  hostDetailLoading.value = true
  try {
    hostDetail.value = await getMonitoringHost(id)
  } catch (e) {
    ElMessage.error('加载主机详情失败：' + extractError(e))
  } finally { hostDetailLoading.value = false }
}

function onHostChange(id) {
  bindForm.graphIds = []
  bindForm.itemIds = []
  loadHostDetail(id)
}

async function openBind() {
  bindForm.host = null
  bindForm.graphIds = []
  bindForm.itemIds = []
  bindForm.panel_slot = 'traffic'
  hostDetail.value = null
  bindTab.value = 'graphs'
  await searchHosts('')
  if (props.targetType === 'pop') {
    const h = hostOptions.value[0]
    if (h) {
      bindForm.host = h.id
      await loadHostDetail(h.id)
    }
  }
  bindVisible.value = true
}

async function doBind() {
  if (!bindForm.host) { ElMessage.warning('请选择监控主机'); return }
  if (!bindForm.graphIds.length && !bindForm.itemIds.length) {
    ElMessage.warning('请选择要关联的图形或监控项'); return
  }
  binding.value = true
  try {
    const base = {
      target_type: props.targetType,
      [fkParam.value]: props.resourceId,
      monitoring_host: bindForm.host,
      panel_slot: bindForm.panel_slot,
      is_visible: true,
    }
    const tasks = [
      ...bindForm.graphIds.map(gid => createGraphBinding({ ...base, graph: gid })),
      ...bindForm.itemIds.map(iid => createGraphBinding({ ...base, monitoring_item: iid })),
    ]
    await Promise.all(tasks)
    ElMessage.success('关联成功')
    bindVisible.value = false
    loaded.value = false
    await load()
  } catch (e) {
    ElMessage.error('关联失败：' + extractError(e))
  } finally { binding.value = false }
}

async function removeBinding(card) {
  try {
    await ElMessageBox.confirm('确定取消该图形/监控项的关联？', '提示', { type: 'warning' })
  } catch { return }
  try {
    await deleteGraphBinding(card.binding_id)
    ElMessage.success('已取消关联')
    loaded.value = false
    await load()
  } catch (e) {
    ElMessage.error('取消失败：' + extractError(e))
  }
}

function openTrafficPage() {
  const name = props.targetType === 'vps' ? 'VPSTrafficBilling' : 'EdgeTrafficBilling'
  router.push({ name, params: { id: props.resourceId } })
}

defineExpose({ load })
</script>

<style scoped>
.mon-panel { min-height: 120px; }
.mp-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
.mp-host { font-weight: 600; font-size: 14px; color: #303133; }
.mp-actions { margin-left: auto; display: flex; gap: 6px; flex-wrap: wrap; }
.mp-slot-title { font-size: 13px; font-weight: 600; color: #606266; margin: 4px 0 10px; padding-left: 8px; border-left: 3px solid #409EFF; }
.mp-card { position: relative; }
.mp-del { position: absolute; top: 4px; right: 6px; z-index: 5; }
.mp-card-host { font-size: 11px; color: #909399; margin-top: 4px; padding-left: 4px; }
</style>
