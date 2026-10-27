<template>
  <div class="time-range-bar">
    <span v-if="label" class="tr-label">{{ label }}</span>
    <el-radio-group v-model="quick" size="small" @change="onQuickChange">
      <el-radio-button v-for="q in presets" :key="q.value" :value="q.value">{{ q.label }}</el-radio-button>
    </el-radio-group>
    <el-date-picker
      v-model="customRange"
      type="datetimerange"
      unlink-panels
      range-separator="至"
      start-placeholder="开始时间"
      end-placeholder="结束时间"
      value-format="YYYY-MM-DDTHH:mm:ss"
      :shortcuts="shortcuts"
      size="small"
      style="width:380px;margin-left:12px;"
      @change="onCustomChange"
    />
    <el-text v-if="rangeHint" type="info" size="small" class="tr-hint">{{ rangeHint }}</el-text>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'

const props = defineProps({
  modelValue: { type: Object, default: () => ({ period: '24h' }) },
  label: { type: String, default: '时间范围' },
  defaultPeriod: { type: String, default: '24h' },
})

const emit = defineEmits(['update:modelValue', 'change'])

const presets = [
  { value: '1h', label: '近1小时' },
  { value: '6h', label: '近6小时' },
  { value: '24h', label: '近24小时' },
  { value: '7d', label: '近7天' },
  { value: '30d', label: '近30天' },
]

const quick = ref(props.modelValue?.period || props.defaultPeriod)
const customRange = ref(null)

function toLocalStr(d) {
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

function rangeForPeriod(period) {
  const end = new Date()
  const map = { '1h': 1, '6h': 6, '24h': 24, '7d': 24 * 7, '30d': 24 * 30 }
  const hours = map[period] || 24
  const start = new Date(end.getTime() - hours * 3600 * 1000)
  return [toLocalStr(start), toLocalStr(end)]
}

const shortcuts = presets.map((q) => ({
  text: q.label,
  value: () => {
    const [s, e] = rangeForPeriod(q.value)
    return [new Date(s), new Date(e)]
  },
}))

const rangeHint = computed(() => {
  const v = props.modelValue
  if (v?.time_from && v?.time_till) {
    return `${v.time_from.replace('T', ' ')} ~ ${v.time_till.replace('T', ' ')}`
  }
  const p = presets.find(x => x.value === v?.period)
  return p ? p.label : ''
})

function emitQuery(query) {
  emit('update:modelValue', query)
  emit('change', query)
}

function onQuickChange(val) {
  customRange.value = null
  const [time_from, time_till] = rangeForPeriod(val)
  emitQuery({ period: val, time_from, time_till })
}

function onCustomChange(val) {
  if (!val || val.length !== 2) return
  quick.value = ''
  emitQuery({ time_from: val[0], time_till: val[1] })
}

watch(() => props.modelValue, (v) => {
  if (v?.period) quick.value = v.period
  if (v?.time_from && v?.time_till) customRange.value = [v.time_from, v.time_till]
}, { deep: true })

onMounted(() => {
  if (!props.modelValue?.period && !props.modelValue?.time_from) {
    const [time_from, time_till] = rangeForPeriod(props.defaultPeriod)
    emitQuery({ period: props.defaultPeriod, time_from, time_till })
  }
})
</script>

<style scoped>
.time-range-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 10px 14px;
  background: #f5f7fa;
  border-radius: 8px;
  border: 1px solid #ebeef5;
}
.tr-label { font-size: 13px; font-weight: 600; color: #606266; margin-right: 4px; }
.tr-hint { margin-left: auto; font-size: 12px; }
</style>
