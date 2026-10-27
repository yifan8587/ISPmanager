<template>
  <div v-loading="pageLoading" class="graphs-page">
    <!-- 顶栏 -->
    <div class="gp-header">
      <div class="gp-header-left">
        <el-button :icon="ArrowLeft" @click="goBack">返回</el-button>
        <div class="gp-title-block">
          <h2 class="gp-title">{{ host?.visible_name || host?.hostname || '监控主机' }}</h2>
          <div class="gp-meta">
            <el-tag size="small" :type="host?.host_kind === 'pop' ? 'warning' : 'info'">
              {{ host?.host_kind_label || '主机' }}
            </el-tag>
            <el-text v-if="host?.monitored_ip" size="small" type="info">{{ host.monitored_ip }}</el-text>
            <el-text v-if="host?.zabbix_hostid" size="small" type="info">Host #{{ host.zabbix_hostid }}</el-text>
            <el-tag size="small" :type="syncTag[host?.sync_status]">{{ host?.sync_status_label }}</el-tag>
          </div>
        </div>
      </div>
      <div class="gp-header-actions">
        <el-button type="success" :loading="syncing" :icon="Refresh" @click="doSync">同步</el-button>
      </div>
    </div>

    <el-tabs v-model="activeTab" class="gp-tabs">
      <!-- ── 监控图形 ── -->
      <el-tab-pane name="graphs">
        <template #label>监控图形 <el-badge :value="graphs.length" :max="99" type="primary" /></template>

        <MonitoringTimeRange v-model="timeQuery" class="gp-time" @change="onTimeChange" />

        <div class="gp-toolbar">
          <div class="gp-toolbar-group">
            <span class="gp-tg-label">布局</span>
            <el-radio-group v-model="gridCols" size="small">
              <el-radio-button value="2">一行两个</el-radio-button>
              <el-radio-button value="1">一行一个</el-radio-button>
            </el-radio-group>
          </div>
          <div class="gp-toolbar-group">
            <span class="gp-tg-label">分类</span>
            <el-select v-model="filterKind" clearable placeholder="全部分类" size="small" style="width:140px;">
              <el-option v-for="k in kindOptions" :key="k.value" :label="k.label" :value="k.value" />
            </el-select>
          </div>
        </div>

        <template v-if="filteredGroups.length">
          <section v-for="grp in filteredGroups" :key="grp.kind" class="gp-section">
            <div class="gp-section-title">
              {{ grp.label }}
              <el-tag size="small" effect="plain">{{ grp.graphs.length }}</el-tag>
            </div>
            <el-row :gutter="16">
              <el-col
                v-for="g in grp.graphs"
                :key="g.id"
                :xs="24"
                :span="gridCols === '1' ? 24 : 12"
                class="gp-chart-col"
              >
                <MonitoringChart
                  :graph-id="g.id"
                  :title="g.effective_title || g.name"
                  :time-query="timeQuery"
                  :show-period="false"
                  :height="gridCols === '1' ? 320 : 260"
                  :chart-key="`${g.id}-${timeKey}`"
                />
              </el-col>
            </el-row>
          </section>
        </template>
        <el-empty v-else description="暂无图形，请先同步监控主机" :image-size="80" />
      </el-tab-pane>

      <!-- ── 监控项 ── -->
      <el-tab-pane name="items">
        <template #label>监控项 <el-badge :value="host?.items?.length" :max="99" /></template>
        <el-table :data="host?.items || []" border stripe size="small" empty-text="暂无监控项">
          <el-table-column prop="name" label="名称" min-width="200" show-overflow-tooltip />
          <el-table-column prop="item_key" label="Key" min-width="220" show-overflow-tooltip />
          <el-table-column prop="interface_name" label="接口" width="120" />
          <el-table-column prop="traffic_direction_label" label="方向" width="90">
            <template #default="{ row }">
              <el-tag v-if="row.traffic_direction === 'in'" size="small" type="primary">入站</el-tag>
              <el-tag v-else-if="row.traffic_direction === 'out'" size="small" type="success">出站</el-tag>
              <span v-else>{{ row.traffic_direction_label || '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="最新值" width="140">
            <template #default="{ row }">{{ row.last_value ?? '-' }} {{ row.units }}</template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft, Refresh } from '@element-plus/icons-vue'
import MonitoringChart from '@/components/MonitoringChart.vue'
import MonitoringTimeRange from '@/components/MonitoringTimeRange.vue'
import { getMonitoringHost, syncMonitoringHost } from '@/api/operation'

const route = useRoute()
const router = useRouter()
const hostId = computed(() => route.params.id)

const pageLoading = ref(false)
const syncing = ref(false)
const host = ref(null)
const activeTab = ref('graphs')
const timeQuery = ref({ period: '24h' })
const gridCols = ref('2')
const filterKind = ref('')

const syncTag = { synced: 'success', error: 'danger', syncing: 'warning', pending: 'info', disabled: 'info' }

const kindMap = {
  traffic: '接口流量',
  billing: '流量计费',
  system: '系统负载',
  icmp: '可用性',
  custom: '其他',
}

const kindOptions = Object.entries(kindMap).map(([value, label]) => ({ value, label }))

const graphs = computed(() => host.value?.graphs || [])

const groupedGraphs = computed(() => {
  const map = new Map()
  for (const g of graphs.value) {
    const kind = g.graph_kind || 'custom'
    if (!map.has(kind)) map.set(kind, [])
    map.get(kind).push(g)
  }
  const order = ['traffic', 'billing', 'system', 'icmp', 'custom']
  return order
    .filter(k => map.has(k))
    .map(k => ({ kind: k, label: kindMap[k] || k, graphs: map.get(k) }))
})

const filteredGroups = computed(() => {
  if (!filterKind.value) return groupedGraphs.value
  return groupedGraphs.value.filter(g => g.kind === filterKind.value)
})

const timeKey = computed(() =>
  timeQuery.value?.time_from
    ? `${timeQuery.value.time_from}_${timeQuery.value.time_till}`
    : timeQuery.value?.period || '24h')

function goBack() {
  router.push('/operation/monitoring/hosts')
}

function onTimeChange() { /* chart-key 变化触发子组件 reload */ }

async function loadHost() {
  pageLoading.value = true
  try {
    host.value = await getMonitoringHost(hostId.value)
  } catch (e) {
    ElMessage.error('加载失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    pageLoading.value = false
  }
}

async function doSync() {
  syncing.value = true
  try {
    const push = host.value?.host_kind === 'pop'
    await syncMonitoringHost(hostId.value, { push })
    ElMessage.success('同步完成')
    await loadHost()
  } catch (e) {
    ElMessage.error('同步失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    syncing.value = false
  }
}

onMounted(loadHost)
</script>

<style scoped>
.graphs-page {
  background: #fff;
  border-radius: 8px;
  padding: 16px 20px 24px;
  min-height: calc(100vh - 120px);
}
.gp-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 16px;
  padding-bottom: 14px;
  border-bottom: 1px solid #ebeef5;
}
.gp-header-left { display: flex; align-items: flex-start; gap: 12px; }
.gp-title-block { display: flex; flex-direction: column; gap: 6px; }
.gp-title { margin: 0; font-size: 20px; font-weight: 700; color: #1a1a2e; }
.gp-meta { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.gp-time { margin-bottom: 14px; }
.gp-toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 20px;
  margin-bottom: 18px;
  padding: 10px 14px;
  background: #fafafa;
  border-radius: 8px;
  border: 1px solid #f0f0f0;
}
.gp-toolbar-group { display: flex; align-items: center; gap: 8px; }
.gp-tg-label { font-size: 12px; color: #909399; white-space: nowrap; }
.gp-section { margin-bottom: 24px; }
.gp-section-title {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 12px;
  padding-left: 10px;
  border-left: 3px solid #409EFF;
  display: flex;
  align-items: center;
  gap: 8px;
}
.gp-chart-col { margin-bottom: 16px; }
</style>
