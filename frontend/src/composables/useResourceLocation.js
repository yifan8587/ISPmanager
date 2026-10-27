import { computed, reactive, ref } from 'vue'
import { getAvailabilityZones, getCities, getCountries, getPopPoints, getRegions } from '@/api/resource'

function asList(value) {
  return value?.results || value || []
}

function sortByName(items) {
  return [...items].sort((left, right) =>
    String(left.name || '').localeCompare(String(right.name || ''), 'zh-CN'),
  )
}

/**
 * 资源页通用的位置筛选与区域→POP导航数据。
 *
 * 所有资源仍以 POP 作为实际归属，区域/国家/城市仅用于缩小 POP 范围；
 * 因此同一筛选对象可直接传给所有资源 API。
 */
export function useResourceLocation() {
  const regions = ref([])
  const countries = ref([])
  const cities = ref([])
  const zones = ref([])
  const popPoints = ref([])
  const location = reactive({
    region: '',
    country: '',
    city: '',
    availability_zone: '',
    pop_point: '',
  })
  const loadingLocation = ref(false)

  const filteredCountries = computed(() => {
    if (!location.region) return countries.value
    return countries.value.filter((item) => String(item.region) === String(location.region))
  })

  const filteredCities = computed(() => {
    if (location.country) {
      return cities.value.filter((item) => String(item.country) === String(location.country))
    }
    if (location.region) {
      const countryIds = new Set(
        countries.value
          .filter((item) => String(item.region) === String(location.region))
          .map((item) => String(item.id)),
      )
      return cities.value.filter((item) => countryIds.has(String(item.country)))
    }
    return cities.value
  })

  const filteredZones = computed(() => {
    if (location.city) {
      return zones.value.filter((item) => String(item.city) === String(location.city))
    }
    const cityIds = new Set(filteredCities.value.map((item) => String(item.id)))
    if (location.region || location.country) {
      return zones.value.filter((item) => cityIds.has(String(item.city)))
    }
    return zones.value
  })

  const filteredPopPoints = computed(() => {
    if (location.availability_zone) {
      return popPoints.value.filter((item) => String(item.availability_zone) === String(location.availability_zone))
    }
    if (location.city) {
      return popPoints.value.filter((item) => String(item.city) === String(location.city))
    }
    const cityIds = new Set(filteredCities.value.map((item) => String(item.id)))
    if (location.region || location.country) {
      return popPoints.value.filter((item) => cityIds.has(String(item.city)))
    }
    return popPoints.value
  })

  function cityOf(pop) {
    return cities.value.find((item) => String(item.id) === String(pop?.city)) || pop?.city_info || {}
  }

  function countryOf(pop) {
    const city = cityOf(pop)
    return countries.value.find((item) => String(item.id) === String(city.country)) || {}
  }

  function regionOf(pop) {
    const country = countryOf(pop)
    return regions.value.find((item) => String(item.id) === String(country.region)) || {}
  }

  function popLabel(pop, withLocation = true) {
    if (!pop) return ''
    if (!withLocation) return pop.name || pop.code || String(pop.id)
    const city = cityOf(pop)
    const country = countryOf(pop)
    const region = regionOf(pop)
    const locationText = [
      region.name || pop?.city_info?.region_name,
      country.name || pop?.city_info?.country_name,
      city.name,
      pop?.zone_name || pop?.availability_zone_name,
    ].filter(Boolean).join(' / ')
    return [locationText, pop.name || pop.code].filter(Boolean).join(' · ')
  }

  function onRegionChange() {
    location.country = ''
    location.city = ''
    location.availability_zone = ''
    location.pop_point = ''
  }

  function onCountryChange() {
    location.city = ''
    location.availability_zone = ''
    location.pop_point = ''
  }

  function onCityChange() {
    location.availability_zone = ''
    location.pop_point = ''
  }

  function selectPop(pop) {
    const city = cityOf(pop)
    const country = countryOf(pop)
    const region = regionOf(pop)
    location.region = region.id || ''
    location.country = country.id || ''
    location.city = city.id || pop.city || ''
    location.availability_zone = pop.availability_zone || ''
    location.pop_point = pop.id
  }

  function resetLocation() {
    Object.assign(location, { region: '', country: '', city: '', availability_zone: '', pop_point: '' })
  }

  function locationParams() {
    return Object.fromEntries(
      Object.entries(location).filter(([, value]) => value !== '' && value !== null && value !== undefined),
    )
  }

  async function loadLocationOptions() {
    loadingLocation.value = true
    try {
      const [regionResult, countryResult, cityResult, zoneResult, popResult] = await Promise.allSettled([
        getRegions({ page_size: 500 }),
        getCountries({ page_size: 1000 }),
        getCities({ page_size: 2000 }),
        getAvailabilityZones({ page_size: 2000 }),
        getPopPoints({ page_size: 1000 }),
      ])
      regions.value = regionResult.status === 'fulfilled' ? sortByName(asList(regionResult.value)) : []
      countries.value = countryResult.status === 'fulfilled' ? sortByName(asList(countryResult.value)) : []
      cities.value = cityResult.status === 'fulfilled' ? sortByName(asList(cityResult.value)) : []
      zones.value = zoneResult.status === 'fulfilled' ? sortByName(asList(zoneResult.value)) : []
      popPoints.value = popResult.status === 'fulfilled' ? sortByName(asList(popResult.value)) : []
    } finally {
      loadingLocation.value = false
    }
  }

  return {
    location,
    regions,
    countries,
    cities,
    zones,
    popPoints,
    loadingLocation,
    filteredCountries,
    filteredCities,
    filteredZones,
    filteredPopPoints,
    popLabel,
    onRegionChange,
    onCountryChange,
    onCityChange,
    selectPop,
    resetLocation,
    locationParams,
    loadLocationOptions,
  }
}

/**
 * 在当前分页结果中插入“区域 + 国家”组合表头行。
 * 后端已按区域、国家、城市和 POP 排序，因而无需一次加载全部资源。
 */
export function useRegionGroupedRows(rows, columnCount = 1) {
  const groupedRows = computed(() => {
    const source = Array.isArray(rows?.value) ? rows.value : []
    const locationCounts = new Map()

    source.forEach((row) => {
      const regionKey = String(row.region_id ?? row.region_name ?? 'unclassified')
      const locationKey = `${regionKey}:${row.country_name || '未归类国家'}`
      locationCounts.set(locationKey, (locationCounts.get(locationKey) || 0) + 1)
    })

    const result = []
    let previousLocation = null
    source.forEach((row) => {
      const regionKey = String(row.region_id ?? row.region_name ?? 'unclassified')
      const regionName = row.region_name || '未归类区域'
      const countryName = row.country_name || '未归类国家'
      const locationKey = `${regionKey}:${countryName}`

      if (locationKey !== previousLocation) {
        result.push({
          __location_group: 'location',
          __group_key: `location:${locationKey}`,
          region_name: regionName,
          country_name: countryName,
          group_count: locationCounts.get(locationKey) || 0,
        })
        previousLocation = locationKey
      }
      result.push(row)
    })
    return result
  })

  const isLocationGroup = (row) => Boolean(row?.__location_group)
  const locationSpanMethod = ({ row, columnIndex }) => {
    if (!isLocationGroup(row)) return [1, 1]
    return columnIndex === 0 ? [1, columnCount] : [0, 0]
  }

  return { groupedRows, isLocationGroup, locationSpanMethod }
}
