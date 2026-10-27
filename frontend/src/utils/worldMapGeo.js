import { countryLabel, shouldSkipPathId } from './worldMapLabels'

function normalizeKey(value) {
  return String(value || '')
    .trim()
    .toLowerCase()
    .replace(/[()（）]/g, '')
    .replace(/[\s_-]+/g, '_')
}

/** SVG path id -> 中文国名 的反向索引及常见别名 */
const CN_COUNTRY_ALIASES = {
  china: 'china',
  中国: 'china',
  prc: 'china',
  usa: 'usa',
  美国: 'usa',
  united_states: 'usa',
  canada: 'canada',
  加拿大: 'canada',
  japan: 'japan',
  日本: 'japan',
  korea: 'south_korea',
  south_korea: 'south_korea',
  韩国: 'south_korea',
  north_korea: 'north_korea',
  朝鲜: 'north_korea',
  taiwan: 'taiwan',
  台湾: 'taiwan',
  uk: 'uk',
  英国: 'uk',
  united_kingdom: 'uk',
  germany: 'germany',
  德国: 'germany',
  france: 'france',
  法国: 'france',
  singapore: 'singapore',
  新加坡: 'singapore',
  malaysia: 'malaysia',
  马来西亚: 'malaysia',
  thailand: 'thailand',
  泰国: 'thailand',
  vietnam: 'vietnam',
  越南: 'vietnam',
  indonesia: 'indonesia',
  印度尼西亚: 'indonesia',
  india: 'india',
  印度: 'india',
  australia: 'australia',
  澳大利亚: 'australia',
  russia: 'russia',
  俄罗斯: 'russia',
  uae: 'uae',
  阿联酋: 'uae',
  saudi: 'saudi',
  沙特: 'saudi',
  burma: 'burma',
  myanmar: 'burma',
  缅甸: 'burma',
  hong_kong: 'china',
  香港: 'china',
  macau: 'china',
  澳门: 'china',
}

/** 国家 SVG 区域内城市锚点（相对 bbox 的比例坐标） */
const CITY_ANCHORS = {
  'china|香港': { rx: 0.78, ry: 0.86 },
  'china|hk': { rx: 0.78, ry: 0.86 },
  'china|hong_kong': { rx: 0.78, ry: 0.86 },
  'china|北京': { rx: 0.68, ry: 0.28 },
  'china|beijing': { rx: 0.68, ry: 0.28 },
  'china|上海': { rx: 0.82, ry: 0.52 },
  'china|shanghai': { rx: 0.82, ry: 0.52 },
  'china|广州': { rx: 0.72, ry: 0.72 },
  'china|guangzhou': { rx: 0.72, ry: 0.72 },
  'china|深圳': { rx: 0.76, ry: 0.78 },
  'china|shenzhen': { rx: 0.76, ry: 0.78 },
  'china|成都': { rx: 0.52, ry: 0.48 },
  'china|chengdu': { rx: 0.52, ry: 0.48 },
  'usa|纽约': { rx: 0.78, ry: 0.32 },
  'usa|new_york': { rx: 0.78, ry: 0.32 },
  'usa|洛杉矶': { rx: 0.18, ry: 0.48 },
  'usa|los_angeles': { rx: 0.18, ry: 0.48 },
  'usa|旧金山': { rx: 0.12, ry: 0.36 },
  'usa|san_francisco': { rx: 0.12, ry: 0.36 },
  'japan|东京': { rx: 0.72, ry: 0.42 },
  'japan|tokyo': { rx: 0.72, ry: 0.42 },
  'japan|大阪': { rx: 0.58, ry: 0.48 },
  'japan|osaka': { rx: 0.58, ry: 0.48 },
  'singapore|新加坡': { rx: 0.5, ry: 0.55 },
  'singapore|singapore': { rx: 0.5, ry: 0.55 },
  'uk|伦敦': { rx: 0.48, ry: 0.35 },
  'uk|london': { rx: 0.48, ry: 0.35 },
  'germany|法兰克福': { rx: 0.52, ry: 0.42 },
  'germany|frankfurt': { rx: 0.52, ry: 0.42 },
  'australia|悉尼': { rx: 0.82, ry: 0.72 },
  'australia|sydney': { rx: 0.82, ry: 0.72 },
}

function hashStr(value) {
  let hash = 0
  for (let i = 0; i < value.length; i += 1) {
    hash = (hash * 31 + value.charCodeAt(i)) >>> 0
  }
  return hash
}

export function buildGeoIndex(svg) {
  const byId = new Map()
  const byLabel = new Map()

  if (!svg) return { byId, byLabel }

  svg.querySelectorAll('path.land-path[id]').forEach((path) => {
    const id = path.id
    if (shouldSkipPathId(id)) return

    let bbox
    try {
      bbox = path.getBBox()
    } catch {
      return
    }
    if (bbox.width < 1 || bbox.height < 1) return

    const labels = new Set([
      normalizeKey(id),
      normalizeKey(countryLabel(id)),
      normalizeKey(id.replace(/_/g, ' ')),
    ])

    const meta = {
      id,
      path,
      bbox,
      labels,
      cx: bbox.x + bbox.width / 2,
      cy: bbox.y + bbox.height / 2,
    }

    byId.set(id, meta)
    labels.forEach((label) => {
      if (label) byLabel.set(label, id)
    })
  })

  return { byId, byLabel }
}

export function resolveCountryPathId(countryName, countryNameEn, geoIndex) {
  const candidates = [countryName, countryNameEn]
    .filter(Boolean)
    .flatMap((name) => [normalizeKey(name), normalizeKey(name).replace(/_/g, '')])

  for (const key of candidates) {
    if (CN_COUNTRY_ALIASES[key]) return CN_COUNTRY_ALIASES[key]
    if (geoIndex.byLabel.has(key)) return geoIndex.byLabel.get(key)
  }

  for (const key of candidates) {
    for (const [label, pathId] of geoIndex.byLabel.entries()) {
      if (label.includes(key) || key.includes(label)) return pathId
    }
  }

  return null
}

function resolveCityAnchor(countryPathId, cityName, cityNameEn) {
  const cityKeys = [cityName, cityNameEn]
    .filter(Boolean)
    .map((name) => `${countryPathId}|${normalizeKey(name)}`)

  for (const key of cityKeys) {
    if (CITY_ANCHORS[key]) return CITY_ANCHORS[key]
  }

  const hashKey = cityKeys[0] || `${countryPathId}|unknown`
  const hash = hashStr(hashKey)
  return {
    rx: 0.22 + ((hash % 1000) / 1000) * 0.56,
    ry: 0.22 + (((hash >> 10) % 1000) / 1000) * 0.56,
  }
}

export function computePopPosition(pop, geoIndex, slotIndex = 0, slotTotal = 1) {
  const countryPathId = resolveCountryPathId(pop.country_name, pop.country_name_en, geoIndex)
  if (!countryPathId) return null

  const meta = geoIndex.byId.get(countryPathId)
  if (!meta) return null

  const anchor = resolveCityAnchor(countryPathId, pop.city_name, pop.city_name_en)
  let x = meta.bbox.x + anchor.rx * meta.bbox.width
  let y = meta.bbox.y + anchor.ry * meta.bbox.height

  if (slotTotal > 1) {
    const angle = (2 * Math.PI * slotIndex) / slotTotal
    const radius = Math.min(meta.bbox.width, meta.bbox.height) * 0.07
    x += Math.cos(angle) * radius
    y += Math.sin(angle) * radius
  }

  return {
    x,
    y,
    country_path_id: countryPathId,
  }
}

export function groupMarkers(points, geoIndex) {
  const groups = new Map()

  for (const p of points) {
    const key = `${p.country_name || ''}|${p.city_name || ''}|${p.country_name_en || ''}|${p.city_name_en || ''}`
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(p)
  }

  const markers = []
  for (const group of groups.values()) {
    group.forEach((p, idx) => {
      const pos = computePopPosition(p, geoIndex, idx, group.length)
      if (!pos) return
      markers.push({
        ...p,
        x: pos.x,
        y: pos.y,
        country_path_id: pos.country_path_id,
        stackCount: group.length,
        label: `[${p.code}] ${p.name}`,
      })
    })
  }
  return markers
}

export function countMappablePoints(points, geoIndex) {
  let mapped = 0
  for (const p of points) {
    if (computePopPosition(p, geoIndex)) mapped += 1
  }
  return mapped
}
