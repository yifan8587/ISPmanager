<template>
  <div class="pop-world-map" :class="{ 'is-fullscreen': isFullscreen }" v-loading="loading">
    <div class="map-toolbar">
      <div class="legend">
        <span class="legend-group-label">节点类型</span>
        <span v-for="item in legendItems" :key="item.type" class="legend-item">
          <span class="legend-icon" v-html="getMarkerLegendSvg(item.type)"></span>
          {{ item.label }}
        </span>
      </div>
      <div class="map-stats">
        <el-text size="small" type="info">
          已标注 {{ mappedCount }} / {{ summary.total || 0 }} 个节点
        </el-text>
        <el-button link type="primary" size="small" @click="$router.push('/resource/pop')">管理 POP</el-button>
        <el-button link type="primary" size="small" @click="toggleFullscreen">
          <el-icon><FullScreen v-if="!isFullscreen" /><Close v-else /></el-icon>
          {{ isFullscreen ? '退出全屏' : '全屏查看' }}
        </el-button>
      </div>
    </div>

    <div
      ref="viewportRef"
      class="map-viewport"
      :class="{ dragging: isDragging }"
      @wheel.prevent="onWheel"
      @mousedown="onPanStart"
    >
      <div ref="svgMountRef" class="map-svg-mount"></div>

      <div class="map-controls" @mousedown.stop>
        <el-button-group size="small">
          <el-button :icon="ZoomIn" @click="zoomBy(1.25)" />
          <el-button :icon="ZoomOut" @click="zoomBy(0.8)" />
          <el-button :icon="RefreshRight" @click="resetView">重置</el-button>
        </el-button-group>
        <span class="zoom-hint">矢量缩放 · 越放大越清晰</span>
        <span class="zoom-level">{{ Math.round(currentZoom * 100) }}%</span>
      </div>
    </div>

    <el-alert
      v-if="unmapped.length && !isFullscreen"
      type="warning"
      :closable="false"
      show-icon
      style="margin-top:10px"
      :title="`${unmapped.length} 个节点缺少国家/城市信息或无法在地图上匹配区域，未显示`"
    />

    <el-card v-if="selected && !isFullscreen" shadow="never" class="pop-detail-card">
      <template #header>
        <div class="detail-header">
          <div>
            <el-tag size="small" :type="popTypeTag[selected.pop_type]">{{ selected.pop_type_label }}</el-tag>
            <strong style="margin-left:8px;">[{{ selected.code }}] {{ selected.name }}</strong>
          </div>
          <div class="detail-actions">
            <el-button link type="primary" size="small" @click="goPopDetail">详情</el-button>
            <el-button link size="small" @click="selected = null">关闭</el-button>
          </div>
        </div>
      </template>
      <el-descriptions :column="2" border size="small">
        <el-descriptions-item label="状态">
          <el-tag size="small" :type="statusTag[selected.status]">{{ selected.status_label }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="位置">
          {{ selected.city_name }} · {{ selected.country_name }}
        </el-descriptions-item>
        <el-descriptions-item label="管理 IP">{{ selected.mgmt_ip || '-' }}</el-descriptions-item>
        <el-descriptions-item label="带宽使用率">
          <el-progress
            :percentage="Math.min(selected.bw_usage_pct || 0, 100)"
            :stroke-width="10"
            :color="bwColor(selected.bw_usage_pct)"
            style="width:140px"
          />
        </el-descriptions-item>
        <el-descriptions-item label="总带宽">{{ formatMbps(selected.total_bandwidth_mbps) }}</el-descriptions-item>
        <el-descriptions-item label="已用带宽">{{ formatMbps(selected.used_bandwidth_mbps) }}</el-descriptions-item>
        <el-descriptions-item label="VPS 占用">{{ formatMbps(selected.vps_bandwidth_mbps) }}</el-descriptions-item>
        <el-descriptions-item label="边缘占用">{{ formatMbps(selected.edge_bandwidth_mbps) }}</el-descriptions-item>
        <el-descriptions-item label="可用带宽">{{ formatMbps(selected.available_bandwidth_mbps) }}</el-descriptions-item>
        <el-descriptions-item label="资源数量">
          VPS {{ selected.vps_count || 0 }} · 边缘 {{ selected.edge_count || 0 }}
        </el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-card v-if="selected && isFullscreen" shadow="never" class="pop-detail-float">
      <div class="detail-header">
        <div>
          <el-tag size="small" :type="popTypeTag[selected.pop_type]">{{ selected.pop_type_label }}</el-tag>
          <strong style="margin-left:8px;">[{{ selected.code }}] {{ selected.name }}</strong>
        </div>
        <div class="detail-actions">
          <el-button link type="primary" size="small" @click="goPopDetail">详情</el-button>
          <el-button link size="small" @click="selected = null">关闭</el-button>
        </div>
      </div>
      <el-text size="small" type="info">
        {{ selected.city_name }} · {{ selected.country_name }} · {{ formatMbps(selected.used_bandwidth_mbps) }}/{{ formatMbps(selected.total_bandwidth_mbps) }}
      </el-text>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { ZoomIn, ZoomOut, RefreshRight, FullScreen, Close } from '@element-plus/icons-vue'
import { getPopMapOverview } from '@/api/resource'
import { buildCountryLabelLayer, updateCountryLabelDisplay } from '@/utils/worldMapLabels'
import { groupMarkers, renderPopMarkers, getMarkerLegendSvg } from '@/utils/worldMapMarkers'
import { buildGeoIndex, countMappablePoints } from '@/utils/worldMapGeo'
import {
  createInitialViewBox,
  viewBoxString,
  zoomLevel,
  getSvgPoint,
  zoomViewBoxAt,
  clampViewBox,
  updateMapVisualDetail,
  resolvePreserveAspectRatio,
} from '@/utils/worldMapView'

const router = useRouter()
const loading = ref(false)
const points = ref([])
const unmapped = ref([])
const summary = ref({})
const selected = ref(null)
const isFullscreen = ref(false)

const svgMountRef = ref(null)
const svgRef = ref(null)
const viewportRef = ref(null)
const labelGroupRef = ref(null)
const geoIndexRef = ref(null)
const mappedCount = ref(0)

const viewBox = ref(createInitialViewBox())
const isDragging = ref(false)

let dragStart = null
let panStartViewBox = null
let resizeObs = null

const legendItems = [
  { type: 'pe', label: 'PE' },
  { type: 'pop', label: 'POP' },
  { type: 'vdc', label: 'VDC' },
  { type: 'other', label: '其他' },
]

const popTypeTag = { pe: 'primary', pop: 'success', vdc: 'warning', other: 'info' }
const statusTag = { online: 'success', offline: 'info', maintenance: 'warning', decommissioned: 'danger' }

const viewBoxStr = computed(() => viewBoxString(viewBox.value))
const currentZoom = computed(() => zoomLevel(viewBox.value))
const markerList = computed(() => {
  if (!geoIndexRef.value) return []
  return groupMarkers(points.value, geoIndexRef.value)
})

function formatMbps(v) {
  const n = Number(v) || 0
  if (n >= 1000) return `${(n / 1000).toFixed(n % 1000 === 0 ? 0 : 1)} Gbps`
  return `${n} Mbps`
}

function bwColor(pct) {
  if (pct >= 90) return '#F56C6C'
  if (pct >= 70) return '#E6A23C'
  return '#67C23A'
}

function updateMapFit() {
  const svg = svgRef.value
  const vp = viewportRef.value
  if (!svg || !vp) return
  svg.setAttribute(
    'preserveAspectRatio',
    resolvePreserveAspectRatio(vp.clientWidth, vp.clientHeight, isFullscreen.value),
  )
}

function refreshMapLayers() {
  const svg = svgRef.value
  if (!svg) return
  updateMapFit()
  const level = currentZoom.value
  updateMapVisualDetail(svg, level)
  updateCountryLabelDisplay(labelGroupRef.value, level)
  renderPopMarkers(svg, markerList.value, {
    activeId: selected.value?.id,
    zoomLevel: level,
    onSelect: (marker) => { selected.value = { ...marker } },
  })
  mappedCount.value = geoIndexRef.value
    ? countMappablePoints(points.value, geoIndexRef.value)
    : 0
}

function resetView() {
  viewBox.value = createInitialViewBox()
  refreshMapLayers()
}

function zoomAt(factor, clientX, clientY) {
  const svg = svgRef.value
  if (!svg) return
  const anchor = getSvgPoint(svg, clientX, clientY)
  viewBox.value = zoomViewBoxAt(viewBox.value, factor, anchor.x, anchor.y)
  refreshMapLayers()
}

function zoomBy(factor) {
  const vp = viewportRef.value
  if (!vp) return
  const rect = vp.getBoundingClientRect()
  zoomAt(factor, rect.left + rect.width / 2, rect.top + rect.height / 2)
}

function onWheel(e) {
  zoomAt(e.deltaY > 0 ? 0.9 : 1.1, e.clientX, e.clientY)
}

function onPanStart(e) {
  if (e.button !== 0 || e.target.closest('.map-controls')) return
  const svg = svgRef.value
  if (!svg) return
  isDragging.value = true
  dragStart = getSvgPoint(svg, e.clientX, e.clientY)
  panStartViewBox = { ...viewBox.value }
  window.addEventListener('mousemove', onPanMove)
  window.addEventListener('mouseup', onPanEnd)
}

function onPanMove(e) {
  if (!isDragging.value || !dragStart || !panStartViewBox) return
  const svg = svgRef.value
  if (!svg) return
  const current = getSvgPoint(svg, e.clientX, e.clientY)
  viewBox.value = clampViewBox({
    ...panStartViewBox,
    x: panStartViewBox.x + (dragStart.x - current.x),
    y: panStartViewBox.y + (dragStart.y - current.y),
  })
  refreshMapLayers()
}

function onPanEnd() {
  isDragging.value = false
  dragStart = null
  panStartViewBox = null
  window.removeEventListener('mousemove', onPanMove)
  window.removeEventListener('mouseup', onPanEnd)
}

function goPopDetail() {
  if (!selected.value?.id) return
  router.push({ path: '/resource/pop', query: { highlight: selected.value.id } })
}

function onDocClick(e) {
  if (!viewportRef.value?.contains(e.target) && !e.target.closest('.pop-detail-card') && !e.target.closest('.pop-detail-float')) {
    selected.value = null
  }
}

function onKeydown(e) {
  if (e.key === 'Escape' && isFullscreen.value) {
    isFullscreen.value = false
  }
}

function toggleFullscreen() {
  isFullscreen.value = !isFullscreen.value
  nextTick(() => {
    updateMapFit()
    refreshMapLayers()
  })
}

async function loadSvgContent() {
  await nextTick()
  const mount = svgMountRef.value
  if (!mount) return

  const res = await fetch(`${import.meta.env.BASE_URL}world-map.svg`, { cache: 'no-cache' })
  if (!res.ok) {
    console.error('Failed to load world-map.svg:', res.status)
    return
  }

  const text = await res.text()
  mount.replaceChildren()
  mount.insertAdjacentHTML('beforeend', text)

  const svg = mount.querySelector('svg')
  if (!svg) {
    console.error('world-map.svg does not contain an SVG root element')
    return
  }

  svgRef.value = svg
  svg.classList.add('map-svg')
  svg.setAttribute('id', 'world-map-interactive')
  svg.setAttribute('viewBox', viewBoxString(viewBox.value))
  svg.removeAttribute('width')
  svg.removeAttribute('height')

  labelGroupRef.value = buildCountryLabelLayer(svg)
  geoIndexRef.value = buildGeoIndex(svg)
  updateMapFit()
  refreshMapLayers()
}

async function load() {
  loading.value = true
  try {
    const res = await getPopMapOverview()
    points.value = res.points || []
    unmapped.value = res.unmapped || []
    summary.value = res.summary || {}
  } finally {
    loading.value = false
  }
}

watch(viewBoxStr, (val) => {
  svgRef.value?.setAttribute('viewBox', val)
})

watch([points, selected], () => refreshMapLayers(), { deep: true })
watch(isFullscreen, () => nextTick(refreshMapLayers))

onMounted(async () => {
  document.addEventListener('click', onDocClick)
  document.addEventListener('keydown', onKeydown)
  await loadSvgContent()
  await load()
  resizeObs = new ResizeObserver(() => refreshMapLayers())
  if (viewportRef.value) resizeObs.observe(viewportRef.value)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onKeydown)
  window.removeEventListener('mousemove', onPanMove)
  window.removeEventListener('mouseup', onPanEnd)
  resizeObs?.disconnect()
})

defineExpose({ reload: load })
</script>

<style scoped>
.pop-world-map { width: 100%; }

.pop-world-map.is-fullscreen {
  position: fixed;
  inset: 0;
  z-index: 2000;
  background: #fff;
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  overflow: hidden;
}

.map-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
  flex-wrap: wrap;
  flex-shrink: 0;
}

.legend { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.legend-group-label { font-size: 12px; color: #909399; margin-right: 4px; }
.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #606266;
}
.legend-icon {
  width: 16px;
  height: 16px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.legend-icon :deep(svg) {
  display: block;
  width: 16px;
  height: 16px;
}

.map-stats {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.map-viewport {
  position: relative;
  width: 100%;
  aspect-ratio: 950 / 620;
  background: linear-gradient(180deg, #e0f2fe 0%, #bae6fd 100%);
  border-radius: 10px;
  overflow: hidden;
  border: 1px solid #7dd3fc;
  cursor: grab;
  touch-action: none;
}
.pop-world-map.is-fullscreen .map-viewport {
  flex: 1;
  width: 100%;
  aspect-ratio: auto;
  min-height: 0;
  border-radius: 12px;
}
.map-viewport.dragging { cursor: grabbing; }

.map-svg-mount {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}
.map-svg-mount :deep(svg),
.map-svg {
  width: 100%;
  height: 100%;
  display: block;
  shape-rendering: geometricPrecision;
  text-rendering: geometricPrecision;
}

.map-controls {
  position: absolute;
  right: 10px;
  bottom: 10px;
  z-index: 5;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 10px;
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  flex-wrap: wrap;
  max-width: calc(100% - 20px);
}

.zoom-hint {
  font-size: 11px;
  color: #909399;
  white-space: nowrap;
}

.zoom-level {
  font-size: 12px;
  color: #409EFF;
  font-weight: 600;
  min-width: 42px;
  text-align: right;
}

.pop-detail-card { margin-top: 12px; }
.pop-detail-float {
  position: fixed;
  left: 24px;
  bottom: 24px;
  z-index: 2010;
  width: min(420px, calc(100% - 48px));
  background: rgba(255, 255, 255, 0.96);
}
.detail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 6px;
}
.detail-actions { display: flex; align-items: center; gap: 4px; }
</style>
