const SKIP_IDS = new Set([
  'ocean-bg', 'country-labels', 'region-labels', 'pop-markers', 'svg2',
  'metadata346', 'defs344', 'path1', 'path-1', 'namedview', 'metadata', 'defs', 'landmasses',
])

const CN_NAMES = {
  china: '中国', usa: '美国', canada: '加拿大', mexico: '墨西哥',
  brazil: '巴西', argentina: '阿根廷', chile: '智利', peru: '秘鲁',
  colombia: '哥伦比亚', venezuela: '委内瑞拉', ecuador: '厄瓜多尔',
  bolivia: '玻利维亚', paraguay: '巴拉圭', uruguay: '乌拉圭',
  france: '法国', germany: '德国', spain: '西班牙', portugal: '葡萄牙',
  italy: '意大利', uk: '英国', england: '英格兰', scotland: '苏格兰',
  ireland: '爱尔兰', iceland: '冰岛', norway: '挪威', sweden: '瑞典',
  finland: '芬兰', denmark: '丹麦', poland: '波兰', ukraine: '乌克兰',
  belarus: '白俄罗斯', romania: '罗马尼亚', bulgaria: '保加利亚',
  greece: '希腊', hungary: '匈牙利', austria: '奥地利',
  switzerland: '瑞士', czech: '捷克', slovakia: '斯洛伐克',
  slovenia: '斯洛文尼亚', croatia: '克罗地亚', serbia: '塞尔维亚',
  albania: '阿尔巴尼亚', macedonia: '北马其顿', montenegro: '黑山',
  bosnia: '波黑', moldova: '摩尔多瓦', estonia: '爱沙尼亚',
  latvia: '拉脱维亚', lithuania: '立陶宛', netherlands: '荷兰',
  belgium: '比利时', turkey: '土耳其', russia: '俄罗斯',
  kazakhstan: '哈萨克斯坦', uzbekistan: '乌兹别克斯坦',
  mongolia: '蒙古', japan: '日本', korea: '韩国', taiwan: '台湾',
  india: '印度', pakistan: '巴基斯坦', bangladesh: '孟加拉国',
  afghanistan: '阿富汗', iran: '伊朗', iraq: '伊拉克', israel: '以色列',
  saudi: '沙特', yemen: '也门', oman: '阿曼', uae: '阿联酋',
  qatar: '卡塔尔', kuwait: '科威特', jordan: '约旦', lebanon: '黎巴嫩',
  syria: '叙利亚', egypt: '埃及', libya: '利比亚', algeria: '阿尔及利亚',
  morocco: '摩洛哥', tunisia: '突尼斯', nigeria: '尼日利亚',
  ethiopia: '埃塞俄比亚', kenya: '肯尼亚', tanzania: '坦桑尼亚',
  south_africa: '南非', angola: '安哥拉', mozambique: '莫桑比克',
  australia: '澳大利亚', new_zealand: '新西兰', indonesia: '印度尼西亚',
  malaysia: '马来西亚', thailand: '泰国', vietnam: '越南',
  philippines: '菲律宾', burma: '缅甸', myanmar: '缅甸', cambodia: '柬埔寨',
  laos: '老挝', singapore: '新加坡', brunei: '文莱',
  greenland: '格陵兰', alaska: '阿拉斯加', cuba: '古巴',
  georgia: '格鲁吉亚', armenia: '亚美尼亚', azerbaijan: '阿塞拜疆',
  sudan: '苏丹', south_sudan: '南苏丹', congo: '刚果', drc: '刚果(金)',
  ghana: '加纳', cameroon: '喀麦隆', uganda: '乌干达', zambia: '赞比亚',
  zimbabwe: '津巴布韦', namibia: '纳米比亚', botswana: '博茨瓦纳',
  madagascar: '马达加斯加', sri_lanka: '斯里兰卡', nepal: '尼泊尔',
  bhutan: '不丹', north_korea: '朝鲜', south_korea: '韩国',
  hong_kong: '香港', macau: '澳门', papua: '巴布亚', fiji: '斐济',
}

export function countryLabel(id) {
  const key = id.toLowerCase().replace(/-/g, '_').replace(/ /g, '_')
  if (CN_NAMES[key]) return CN_NAMES[key]
  return id.replace(/_/g, ' ').replace(/-/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

export function shouldSkipPathId(id) {
  return !id || SKIP_IDS.has(id) || id.startsWith('path-effect') || id.includes('effect')
}

export function buildCountryLabelLayer(svg) {
  if (!svg) return null

  svg.querySelector('#country-labels')?.remove()

  const g = document.createElementNS('http://www.w3.org/2000/svg', 'g')
  g.id = 'country-labels'
  g.setAttribute('class', 'country-labels')
  g.setAttribute('pointer-events', 'none')

  const scope = svg
  scope.querySelectorAll('path[id].land-path').forEach((path) => {
    const id = path.id
    if (shouldSkipPathId(id)) return

    let bbox
    try {
      bbox = path.getBBox()
    } catch {
      return
    }

    const area = bbox.width * bbox.height
    if (area < 20 || bbox.width < 2 || bbox.height < 2) return

    const text = document.createElementNS('http://www.w3.org/2000/svg', 'text')
    text.setAttribute('x', String(bbox.x + bbox.width / 2))
    text.setAttribute('y', String(bbox.y + bbox.height / 2))
    text.setAttribute('text-anchor', 'middle')
    text.setAttribute('dominant-baseline', 'middle')
    text.setAttribute('class', 'country-name')
    text.dataset.country = id
    text.dataset.area = String(area)
    text.setAttribute('fill', '#1e293b')
    text.setAttribute('stroke', '#ffffff')
    text.setAttribute('stroke-width', '0.25')
    text.setAttribute('paint-order', 'stroke fill')
    text.textContent = countryLabel(id)
    g.appendChild(text)
  })

  svg.appendChild(g)
  return g
}

export function updateCountryLabelDisplay(labelGroup, zoomLevel) {
  if (!labelGroup) return

  labelGroup.querySelectorAll('text.country-name').forEach((text) => {
    const area = Number(text.dataset.area) || 0
    const minArea = zoomLevel >= 6 ? 8 : zoomLevel >= 4 ? 18 : zoomLevel >= 2.2 ? 40 : 120
    const visible = zoomLevel >= 1.6 && area >= minArea

    if (!visible) {
      text.style.opacity = '0'
      return
    }

    const fontSize = Math.max(3.8, Math.min(11, Math.pow(area, 0.18) * (0.75 + zoomLevel * 0.08)))
    text.setAttribute('font-size', String(fontSize))
    text.style.opacity = String(Math.min(0.98, 0.45 + (zoomLevel - 1.6) * 0.18))
    text.setAttribute('stroke-width', String(zoomLevel >= 4 ? 0.35 : 0.2))
  })
}
