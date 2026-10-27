export const MAP_WIDTH = 950
export const MAP_HEIGHT = 620
export const MAP_ASPECT_RATIO = MAP_WIDTH / MAP_HEIGHT
export const MIN_ZOOM = 1
export const MAX_ZOOM = 12

export function createInitialViewBox() {
  return { x: 0, y: 0, w: MAP_WIDTH, h: MAP_HEIGHT }
}

export function viewBoxString(vb) {
  return `${vb.x} ${vb.y} ${vb.w} ${vb.h}`
}

export function zoomLevel(vb) {
  return MAP_WIDTH / vb.w
}

export function getSvgPoint(svg, clientX, clientY) {
  const pt = svg.createSVGPoint()
  pt.x = clientX
  pt.y = clientY
  const ctm = svg.getScreenCTM()
  if (!ctm) return { x: 0, y: 0 }
  const p = pt.matrixTransform(ctm.inverse())
  return { x: p.x, y: p.y }
}

export function clampViewBox(vb) {
  const aspect = MAP_HEIGHT / MAP_WIDTH
  const minW = MAP_WIDTH / MAX_ZOOM
  const maxW = MAP_WIDTH / MIN_ZOOM

  let w = Math.min(maxW, Math.max(minW, vb.w))
  let h = w * aspect

  let x = vb.x
  let y = vb.y

  if (x < 0) x = 0
  if (y < 0) y = 0
  if (x + w > MAP_WIDTH) x = MAP_WIDTH - w
  if (y + h > MAP_HEIGHT) y = MAP_HEIGHT - h

  return { x, y, w, h }
}

export function zoomViewBoxAt(vb, factor, anchorX, anchorY) {
  const nextW = vb.w / factor
  const ratio = nextW / vb.w
  return clampViewBox({
    x: anchorX - (anchorX - vb.x) * ratio,
    y: anchorY - (anchorY - vb.y) * ratio,
    w: nextW,
    h: vb.h / factor,
  })
}

export function projectLatLng(lat, lng) {
  return {
    x: ((Number(lng) + 180) / 360) * MAP_WIDTH,
    y: ((90 - Number(lat)) / 180) * MAP_HEIGHT,
  }
}

/** 根据容器比例决定 SVG 铺满方式：比例一致时 meet，否则 slice 裁边铺满 */
export function resolvePreserveAspectRatio(viewportWidth, viewportHeight, preferFill = false) {
  if (!viewportWidth || !viewportHeight) return 'xMidYMid meet'
  const vpRatio = viewportWidth / viewportHeight
  if (!preferFill && Math.abs(vpRatio - MAP_ASPECT_RATIO) <= 0.03) {
    return 'xMidYMid meet'
  }
  if (preferFill || Math.abs(vpRatio - MAP_ASPECT_RATIO) > 0.03) {
    return 'xMidYMid slice'
  }
  return 'xMidYMid meet'
}

export function updateMapVisualDetail(svg, level) {
  if (!svg) return

  svg.style.shapeRendering = 'geometricPrecision'
  svg.style.textRendering = 'geometricPrecision'

  svg.querySelectorAll('path.land-path').forEach((path) => {
    path.style.strokeOpacity = level >= 4 ? '1' : level >= 2 ? '0.92' : '0.82'
  })
}
