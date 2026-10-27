const NS = 'http://www.w3.org/2000/svg'
const VIEW = 24

const TYPE_ICONS = {
  pe: {
    fill: '#409EFF',
    paths: [
      { d: 'M3 9h18a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2z' },
      { d: 'M6 6h2v3H6V6zm4-1h2v4h-2V5zm4 1h2v3h-2V6z' },
      { d: 'M6 13h3v2H6v-2zm5 0h3v2h-3v-2zm5 0h3v2h-3v-2z', fill: '#ffffff' },
    ],
    title: 'PE 路由器',
  },
  pop: {
    fill: '#67C23A',
    paths: [
      { d: 'M7 2h10a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z' },
      { d: 'M7 6h10v3H7V6zm0 5h10v3H7v-3zm0 5h10v3H7v-3z', fill: '#ffffff', opacity: 0.28 },
      { d: 'M9 7.2h1.6v1.6H9V7.2zm4.4 0H15v1.6h-1.6V7.2zm-4.4 5h1.6v1.6H9v-1.6zm4.4 0H15v1.6h-1.6v-1.6zm-4.4 5h1.6v1.6H9V17.2zm4.4 0H15v1.6h-1.6V17.2z', fill: '#ffffff' },
    ],
    title: 'POP 服务器',
  },
  vdc: {
    fill: '#E6A23C',
    paths: [
      { d: 'M4 5h16a3 3 0 0 1 3 3v3a3 3 0 0 1-3 3H4a3 3 0 0 1-3-3V8a3 3 0 0 1 3-3z' },
      { d: 'M4 13h16a3 3 0 0 1 3 3v3a3 3 0 0 1-3 3H4a3 3 0 0 1-3-3v-3a3 3 0 0 1 3-3z' },
      { d: 'M7 8.5h2v1H7v-1zm4 0h2v1h-2v-1zm4 0h2v1h-2v-1zM7 16.5h2v1H7v-1zm4 0h2v1h-2v-1zm4 0h2v1h-2v-1z', fill: '#ffffff' },
    ],
    title: 'VDC 存储',
  },
  other: {
    fill: '#909399',
    paths: [{ d: 'M12 4l8 14H4l8-14z' }],
    title: '其他',
  },
}

function markerSize(level) {
  if (level >= 6) return 6
  if (level >= 3) return 5.2
  return 4.6
}

function appendIconPaths(parent, type, { fillOverride, opacity } = {}) {
  const cfg = TYPE_ICONS[type] || TYPE_ICONS.other
  const baseFill = fillOverride || cfg.fill

  cfg.paths.forEach((item, index) => {
    const node = document.createElementNS(NS, 'path')
    node.setAttribute('d', item.d)
    node.setAttribute('fill', item.fill || (index === 0 ? baseFill : baseFill))
    if (item.opacity != null) node.setAttribute('opacity', String(item.opacity))
    if (opacity != null) node.setAttribute('opacity', String(opacity))
    parent.appendChild(node)
  })
}

function createMarkerIcon(type, size, marker) {
  const g = document.createElementNS(NS, 'g')
  g.classList.add('marker-icon')
  const scale = (size * 2.2) / VIEW
  g.setAttribute('transform', `scale(${scale}) translate(${-VIEW / 2}, ${-VIEW / 2})`)

  const fillOverride = marker.status === 'maintenance' ? '#E6A23C' : null
  const opacity = marker.status === 'offline' ? 0.5 : 1
  appendIconPaths(g, type, { fillOverride, opacity })
  return g
}

export function getMarkerLegendSvg(type, px = 16) {
  const cfg = TYPE_ICONS[type] || TYPE_ICONS.other
  const paths = cfg.paths.map((item, index) => {
    const fill = item.fill || (index === 0 ? cfg.fill : cfg.fill)
    const opacity = item.opacity != null ? ` opacity="${item.opacity}"` : ''
    return `<path d="${item.d}" fill="${fill}"${opacity}/>`
  }).join('')
  return `<svg xmlns="${NS}" viewBox="0 0 ${VIEW} ${VIEW}" width="${px}" height="${px}" aria-hidden="true">${paths}</svg>`
}

export function renderPopMarkers(svg, markers, { activeId, zoomLevel, onSelect }) {
  if (!svg) return

  svg.querySelector('#pop-markers')?.remove()

  const layer = document.createElementNS(NS, 'g')
  layer.id = 'pop-markers'
  layer.setAttribute('pointer-events', 'all')

  const size = markerSize(zoomLevel)

  markers.forEach((marker) => {
    if (marker.x == null || marker.y == null) return

    const g = document.createElementNS(NS, 'g')
    g.classList.add('pop-marker')
    g.dataset.id = String(marker.id)
    g.setAttribute('transform', `translate(${marker.x}, ${marker.y})`)
    g.style.cursor = 'pointer'

    const halo = document.createElementNS(NS, 'circle')
    halo.setAttribute('r', String(size + 2.8))
    halo.setAttribute('fill', '#ffffff')
    halo.setAttribute('stroke', '#e2e8f0')
    halo.setAttribute('stroke-width', '0.4')
    halo.setAttribute('opacity', marker.status === 'offline' ? '0.55' : '0.96')

    const core = createMarkerIcon(marker.pop_type, size, marker)
    g.appendChild(halo)
    g.appendChild(core)

    if (marker.active || marker.id === activeId) {
      const ring = document.createElementNS(NS, 'circle')
      ring.setAttribute('r', String(size + 4.5))
      ring.setAttribute('fill', 'none')
      ring.setAttribute('stroke', '#409EFF')
      ring.setAttribute('stroke-width', '1.2')
      ring.setAttribute('opacity', '0.85')
      g.insertBefore(ring, halo)
    }

    if (marker.stackCount > 1) {
      const badge = document.createElementNS(NS, 'text')
      badge.setAttribute('x', String(size + 1))
      badge.setAttribute('y', String(-size + 1))
      badge.setAttribute('font-size', '6')
      badge.setAttribute('fill', '#303133')
      badge.setAttribute('font-weight', '700')
      badge.textContent = String(marker.stackCount)
      g.appendChild(badge)
    }

    const title = document.createElementNS(NS, 'title')
    title.textContent = `${marker.label} · ${marker.city_name || ''} ${marker.country_name || ''}`
    g.appendChild(title)

    g.addEventListener('click', (e) => {
      e.stopPropagation()
      onSelect?.(marker)
    })

    layer.appendChild(g)
  })

  svg.appendChild(layer)
}

export { groupMarkers } from './worldMapGeo'
