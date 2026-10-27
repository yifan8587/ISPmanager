<template>
  <div class="base-data">
    <div class="bd-intro">
      <el-alert
        title="系统数据库基础设置"
        type="info"
        :closable="false"
        description="维护地理位置（大区 / 国家 / 城市 / 可用区）与运营商等系统基础字典数据，POP 点、产品、IP、VPS、边缘加速等资源均可引用。"
        show-icon
      />
      <div class="bd-excel">
        <ExcelImportExport
          export-url="/api/resource/base-data/export/"
          template-url="/api/resource/base-data/import-template/"
          import-url="/api/resource/base-data/import/"
          filename="基础数据.xlsx"
          @imported="reloadAll"
        />
        <span class="bd-excel-hint">整本导出 5 张表；导入按 大区→国家→城市→可用区→运营商 关联写入。</span>
      </div>
    </div>

    <el-tabs v-model="innerTab" @tab-change="onTabChange">
      <!-- ── 大区 ── -->
      <el-tab-pane label="大区" name="region">
        <div class="bd-toolbar">
          <el-input v-model="search.region" placeholder="搜索名称/代码" clearable style="width:220px" @input="() => loadRegions()" />
          <el-button v-if="canManage" type="primary" :icon="Plus" @click="openDialog('region')">新增大区</el-button>
        </div>
        <el-table :data="regions" v-loading="loading.region" border size="small">
          <el-table-column prop="code" label="区域代码" width="150" />
          <el-table-column prop="code_label" label="区域" width="120" />
          <el-table-column prop="name" label="名称" min-width="140" />
          <el-table-column prop="name_en" label="英文名称" min-width="140" />
          <el-table-column v-if="canManage" label="操作" width="140" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="openDialog('region', row)">编辑</el-button>
              <el-button link type="danger" size="small" @click="remove('region', row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- ── 国家 ── -->
      <el-tab-pane label="国家" name="country">
        <div class="bd-toolbar">
          <el-select v-model="filter.countryRegion" placeholder="按大区筛选" clearable style="width:180px" @change="() => loadCountries()">
            <el-option v-for="r in regions" :key="r.id" :label="r.name" :value="r.id" />
          </el-select>
          <el-input v-model="search.country" placeholder="搜索名称" clearable style="width:200px" @input="() => loadCountries()" />
          <el-button v-if="canManage" type="primary" :icon="Plus" @click="openDialog('country')">新增国家</el-button>
        </div>
        <el-table :data="countries" v-loading="loading.country" border size="small">
          <el-table-column prop="region_name" label="所属大区" width="120" />
          <el-table-column prop="name" label="国家名称" min-width="160" />
          <el-table-column prop="name_en" label="英文名称" min-width="160" />
          <el-table-column v-if="canManage" label="操作" width="140" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="openDialog('country', row)">编辑</el-button>
              <el-button link type="danger" size="small" @click="remove('country', row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- ── 城市 ── -->
      <el-tab-pane label="城市" name="city">
        <div class="bd-toolbar">
          <el-select v-model="filter.cityRegion" placeholder="按大区筛选" clearable style="width:160px" @change="() => loadCities()">
            <el-option v-for="r in regions" :key="r.id" :label="r.name" :value="r.id" />
          </el-select>
          <el-select v-model="filter.cityCountry" placeholder="按国家筛选" clearable filterable style="width:180px" @change="() => loadCities()">
            <el-option v-for="c in countries" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
          <el-input v-model="search.city" placeholder="搜索名称" clearable style="width:180px" @input="() => loadCities()" />
          <el-button v-if="canManage" type="primary" :icon="Plus" @click="openDialog('city')">新增城市</el-button>
        </div>
        <el-table :data="cities" v-loading="loading.city" border size="small">
          <el-table-column prop="region_name" label="大区" width="90" />
          <el-table-column prop="country_name" label="国家" width="120" />
          <el-table-column prop="name" label="城市名称" min-width="130" />
          <el-table-column prop="name_en" label="英文名称" min-width="130" />
          <el-table-column prop="timezone" label="时区" width="150" />
          <el-table-column label="经纬度" width="160">
            <template #default="{ row }">
              <span v-if="row.latitude != null || row.longitude != null">{{ row.latitude ?? '-' }}, {{ row.longitude ?? '-' }}</span>
              <span v-else>-</span>
            </template>
          </el-table-column>
          <el-table-column v-if="canManage" label="操作" width="140" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="openDialog('city', row)">编辑</el-button>
              <el-button link type="danger" size="small" @click="remove('city', row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- ── 可用区 ── -->
      <el-tab-pane label="可用区" name="zone">
        <div class="bd-toolbar">
          <el-select v-model="filter.zoneCity" placeholder="按城市筛选" clearable filterable style="width:220px" @change="() => loadZones()">
            <el-option v-for="c in cities" :key="c.id" :label="`${c.country_name || ''} / ${c.name}`" :value="c.id" />
          </el-select>
          <el-input v-model="search.zone" placeholder="搜索名称/代码" clearable style="width:200px" @input="() => loadZones()" />
          <el-button v-if="canManage" type="primary" :icon="Plus" @click="openDialog('zone')">新增可用区</el-button>
        </div>
        <el-table :data="zones" v-loading="loading.zone" border size="small">
          <el-table-column prop="region_name" label="大区" width="90" />
          <el-table-column prop="country_name" label="国家" width="120" />
          <el-table-column prop="city_name" label="城市" width="130" />
          <el-table-column prop="name" label="可用区" min-width="140" />
          <el-table-column prop="code" label="代码" width="120" />
          <el-table-column prop="name_en" label="英文名称" min-width="130" />
          <el-table-column prop="remark" label="备注" min-width="160" show-overflow-tooltip />
          <el-table-column v-if="canManage" label="操作" width="140" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="openDialog('zone', row)">编辑</el-button>
              <el-button link type="danger" size="small" @click="remove('zone', row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- ── 运营商 ── -->
      <el-tab-pane label="运营商" name="carrier">
        <div class="bd-toolbar">
          <el-input v-model="search.carrier" placeholder="搜索名称/代码" clearable style="width:220px" @input="() => loadCarriers()" />
          <el-button v-if="canManage" type="primary" :icon="Plus" @click="openDialog('carrier')">新增运营商</el-button>
        </div>
        <el-table :data="carriers" v-loading="loading.carrier" border size="small">
          <el-table-column prop="code" label="代码" width="120" />
          <el-table-column prop="name" label="名称" min-width="160" />
          <el-table-column label="覆盖大区" min-width="180">
            <template #default="{ row }">
              <el-tag v-for="n in row.region_names" :key="n" size="small" style="margin-right:4px">{{ n }}</el-tag>
              <span v-if="!row.region_names?.length">-</span>
            </template>
          </el-table-column>
          <el-table-column prop="remark" label="备注" min-width="160" show-overflow-tooltip />
          <el-table-column v-if="canManage" label="操作" width="140" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="openDialog('carrier', row)">编辑</el-button>
              <el-button link type="danger" size="small" @click="remove('carrier', row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <!-- ── 通用新增/编辑弹窗 ── -->
    <el-dialog v-model="dialog.visible" :title="dialogTitle" width="520px" @closed="resetForm">
      <el-form :model="form" label-width="96px" @submit.prevent>
        <!-- 大区 -->
        <template v-if="dialog.type === 'region'">
          <el-form-item label="区域代码" required>
            <el-select v-model="form.code" placeholder="选择区域代码" style="width:100%" @change="onRegionCode">
              <el-option v-for="o in REGION_CODES" :key="o.value" :label="`${o.label} (${o.value})`" :value="o.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="名称" required>
            <el-input v-model="form.name" placeholder="如：亚洲" />
          </el-form-item>
          <el-form-item label="英文名称">
            <el-input v-model="form.name_en" placeholder="如：Asia" />
          </el-form-item>
        </template>

        <!-- 国家 -->
        <template v-else-if="dialog.type === 'country'">
          <el-form-item label="所属大区" required>
            <el-select v-model="form.region" placeholder="选择大区" style="width:100%">
              <el-option v-for="r in regions" :key="r.id" :label="r.name" :value="r.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="国家名称" required>
            <el-input v-model="form.name" placeholder="如：日本" />
          </el-form-item>
          <el-form-item label="英文名称">
            <el-input v-model="form.name_en" placeholder="如：Japan" />
          </el-form-item>
        </template>

        <!-- 城市 -->
        <template v-else-if="dialog.type === 'city'">
          <el-form-item label="所属国家" required>
            <el-select v-model="form.country" placeholder="选择国家" filterable style="width:100%">
              <el-option v-for="c in countries" :key="c.id" :label="`${c.name}（${c.region_name}）`" :value="c.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="城市名称" required>
            <el-input v-model="form.name" placeholder="如：东京" />
          </el-form-item>
          <el-form-item label="英文名称">
            <el-input v-model="form.name_en" placeholder="如：Tokyo" />
          </el-form-item>
          <el-form-item label="时区">
            <el-input v-model="form.timezone" placeholder="如：Asia/Tokyo" />
          </el-form-item>
          <el-form-item label="纬度">
            <el-input-number v-model="form.latitude" :precision="6" :step="0.000001" :controls="false" style="width:100%" placeholder="如：35.6895" />
          </el-form-item>
          <el-form-item label="经度">
            <el-input-number v-model="form.longitude" :precision="6" :step="0.000001" :controls="false" style="width:100%" placeholder="如：139.6917" />
          </el-form-item>
        </template>

        <!-- 可用区 -->
        <template v-else-if="dialog.type === 'zone'">
          <el-form-item label="所属城市" required>
            <el-select v-model="form.city" placeholder="选择城市" filterable style="width:100%">
              <el-option v-for="c in cities" :key="c.id" :label="`${c.region_name} / ${c.country_name} / ${c.name}`" :value="c.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="可用区名称" required>
            <el-input v-model="form.name" placeholder="如：东京 A 区" />
          </el-form-item>
          <el-form-item label="代码">
            <el-input v-model="form.code" placeholder="可选，如 tokyo-a" />
          </el-form-item>
          <el-form-item label="英文名称">
            <el-input v-model="form.name_en" placeholder="如：Tokyo AZ-A" />
          </el-form-item>
          <el-form-item label="备注">
            <el-input v-model="form.remark" type="textarea" :rows="2" />
          </el-form-item>
        </template>

        <!-- 运营商 -->
        <template v-else-if="dialog.type === 'carrier'">
          <el-form-item label="运营商代码" required>
            <el-input v-model="form.code" placeholder="如：NTT" />
          </el-form-item>
          <el-form-item label="名称" required>
            <el-input v-model="form.name" placeholder="如：NTT Communications" />
          </el-form-item>
          <el-form-item label="覆盖大区">
            <el-select v-model="form.regions" multiple placeholder="选择覆盖大区" style="width:100%">
              <el-option v-for="r in regions" :key="r.id" :label="r.name" :value="r.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="备注">
            <el-input v-model="form.remark" type="textarea" :rows="2" />
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.saving" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import ExcelImportExport from '@/components/ExcelImportExport.vue'
import { useAuthStore } from '@/stores/auth'
import {
  getRegions, createRegion, updateRegion, deleteRegion,
  getCountries, createCountry, updateCountry, deleteCountry,
  getCities, createCity, updateCity, deleteCity,
  getAvailabilityZones, createAvailabilityZone, updateAvailabilityZone, deleteAvailabilityZone,
  getCarriers, createCarrier, updateCarrier, deleteCarrier,
} from '@/api/resource'

const REGION_CODES = [
  { value: 'apac', label: '亚洲', nameEn: 'Asia' },
  { value: 'europe', label: '欧洲', nameEn: 'Europe' },
  { value: 'africa', label: '非洲', nameEn: 'Africa' },
  { value: 'americas', label: '北美洲', nameEn: 'North America' },
  { value: 'south_america', label: '南美洲', nameEn: 'South America' },
  { value: 'oceania', label: '大洋洲', nameEn: 'Oceania' },
]
const REGION_NAME_PRESETS = new Set([
  ...REGION_CODES.map((o) => o.label),
  '亚太', '亚州', '美洲', '北美州',
])
const REGION_NAME_EN_PRESETS = new Set([
  ...REGION_CODES.map((o) => o.nameEn),
  'Asia Pacific', 'Asia-Pacific', 'Americas', 'America',
])

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('resource.manage') || authStore.user?.is_superuser)

const innerTab = ref('region')
const regions = ref([])
const countries = ref([])
const cities = ref([])
const zones = ref([])
const carriers = ref([])
const loading = reactive({ region: false, country: false, city: false, zone: false, carrier: false })
const search = reactive({ region: '', country: '', city: '', zone: '', carrier: '' })
const filter = reactive({ countryRegion: null, cityRegion: null, cityCountry: null, zoneCity: null })

const dialog = reactive({ visible: false, type: '', saving: false, editId: null })
const form = reactive({})

const dialogTitle = computed(() => {
  const map = { region: '大区', country: '国家', city: '城市', zone: '可用区', carrier: '运营商' }
  return `${dialog.editId ? '编辑' : '新增'}${map[dialog.type] || ''}`
})

async function loadRegions() {
  loading.region = true
  try {
    const res = await getRegions({ page_size: 500, search: search.region || undefined })
    regions.value = res?.results || res || []
  } finally { loading.region = false }
}

async function loadCountries() {
  loading.country = true
  try {
    const res = await getCountries({
      page_size: 1000,
      search: search.country || undefined,
      region: filter.countryRegion || undefined,
    })
    countries.value = res?.results || res || []
  } finally { loading.country = false }
}

async function loadCities() {
  loading.city = true
  try {
    const res = await getCities({
      page_size: 1000,
      search: search.city || undefined,
      country: filter.cityCountry || undefined,
      country__region: filter.cityRegion || undefined,
    })
    cities.value = res?.results || res || []
  } finally { loading.city = false }
}

async function loadZones() {
  loading.zone = true
  try {
    const res = await getAvailabilityZones({
      page_size: 1000,
      search: search.zone || undefined,
      city: filter.zoneCity || undefined,
    })
    zones.value = res?.results || res || []
  } finally { loading.zone = false }
}

async function loadCarriers() {
  loading.carrier = true
  try {
    const res = await getCarriers({ page_size: 500, search: search.carrier || undefined })
    carriers.value = res?.results || res || []
  } finally { loading.carrier = false }
}

function onTabChange(name) {
  if (name === 'region') loadRegions()
  else if (name === 'country') loadCountries()
  else if (name === 'city') loadCities()
  else if (name === 'zone') loadZones()
  else if (name === 'carrier') loadCarriers()
}

function onRegionCode(code) {
  const item = REGION_CODES.find((o) => o.value === code)
  if (!item) return
  if (!form.name || REGION_NAME_PRESETS.has(form.name)) form.name = item.label
  if (!form.name_en || REGION_NAME_EN_PRESETS.has(form.name_en)) form.name_en = item.nameEn
}

function openDialog(type, row = null) {
  dialog.type = type
  dialog.editId = row?.id ?? null
  Object.keys(form).forEach((k) => delete form[k])
  if (type === 'region') {
    Object.assign(form, { code: row?.code ?? '', name: row?.name ?? '', name_en: row?.name_en ?? '' })
  } else if (type === 'country') {
    Object.assign(form, { region: row?.region ?? null, name: row?.name ?? '', name_en: row?.name_en ?? '' })
  } else if (type === 'city') {
    Object.assign(form, {
      country: row?.country ?? null, name: row?.name ?? '', name_en: row?.name_en ?? '',
      timezone: row?.timezone ?? '',
      latitude: row?.latitude != null ? Number(row.latitude) : null,
      longitude: row?.longitude != null ? Number(row.longitude) : null,
    })
  } else if (type === 'zone') {
    Object.assign(form, {
      city: row?.city ?? filter.zoneCity ?? null,
      name: row?.name ?? '',
      name_en: row?.name_en ?? '',
      code: row?.code ?? '',
      remark: row?.remark ?? '',
    })
  } else if (type === 'carrier') {
    Object.assign(form, {
      code: row?.code ?? '', name: row?.name ?? '',
      regions: Array.isArray(row?.regions) ? [...row.regions] : [],
      remark: row?.remark ?? '',
    })
  }
  dialog.visible = true
}

function resetForm() {
  dialog.type = ''
  dialog.editId = null
  Object.keys(form).forEach((k) => delete form[k])
}

const API = {
  region: { create: createRegion, update: updateRegion, remove: deleteRegion, reload: loadRegions },
  country: { create: createCountry, update: updateCountry, remove: deleteCountry, reload: loadCountries },
  city: { create: createCity, update: updateCity, remove: deleteCity, reload: loadCities },
  zone: { create: createAvailabilityZone, update: updateAvailabilityZone, remove: deleteAvailabilityZone, reload: loadZones },
  carrier: { create: createCarrier, update: updateCarrier, remove: deleteCarrier, reload: loadCarriers },
}

function buildPayload() {
  const t = dialog.type
  if (t === 'city') {
    return {
      country: form.country, name: form.name, name_en: form.name_en, timezone: form.timezone,
      latitude: form.latitude === '' ? null : form.latitude,
      longitude: form.longitude === '' ? null : form.longitude,
    }
  }
  return { ...form }
}

async function submit() {
  const t = dialog.type
  const payload = buildPayload()
  // 基础必填校验
  if ((t === 'region' || t === 'carrier') && !payload.code) return ElMessage.warning('请填写代码')
  if (t === 'country' && !payload.region) return ElMessage.warning('请选择所属大区')
  if (t === 'city' && !payload.country) return ElMessage.warning('请选择所属国家')
  if (t === 'zone' && !payload.city) return ElMessage.warning('请选择所属城市')
  if (!payload.name) return ElMessage.warning('请填写名称')

  dialog.saving = true
  try {
    if (dialog.editId) await API[t].update(dialog.editId, payload)
    else await API[t].create(payload)
    ElMessage.success('保存成功')
    dialog.visible = false
    await API[t].reload()
    if (t === 'region') { await loadCountries(); await loadCities() }
    if (t === 'country') { await loadCities(); await loadZones() }
    if (t === 'city') await loadZones()
  } catch (err) {
    ElMessage.error(err?.response?.data?.message || err?.response?.data?.detail || firstFieldError(err) || '保存失败')
  } finally {
    dialog.saving = false
  }
}

function firstFieldError(err) {
  const data = err?.response?.data
  if (data && typeof data === 'object') {
    const k = Object.keys(data)[0]
    const v = data[k]
    if (Array.isArray(v)) return `${k}: ${v[0]}`
    if (typeof v === 'string') return v
  }
  return ''
}

async function remove(type, row) {
  const names = { region: '大区', country: '国家', city: '城市', zone: '可用区', carrier: '运营商' }
  try {
    await ElMessageBox.confirm(`确认删除${names[type]}「${row.name}」？`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await API[type].remove(row.id)
    ElMessage.success('已删除')
    await API[type].reload()
  } catch (err) {
    ElMessage.error(err?.response?.data?.message || err?.response?.data?.detail || '删除失败')
  }
}

function reloadAll() {
  loadRegions()
  loadCountries()
  loadCities()
  loadZones()
  loadCarriers()
}

onMounted(async () => {
  await loadRegions()
  await loadCountries()
  loadCities()
  loadZones()
})

defineExpose({ reload: reloadAll })
</script>

<style scoped>
.base-data { padding-top: 4px; }
.bd-intro { margin-bottom: 14px; }
.bd-excel { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 12px; }
.bd-excel-hint { color: #909399; font-size: 12px; }
.bd-toolbar { display: flex; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; align-items: center; }
.bd-toolbar .el-button { margin-left: auto; }
</style>
