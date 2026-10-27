<template>
  <div class="location-filter">
    <el-select
      :model-value="location.region"
      clearable
      filterable
      placeholder="全部区域"
      :loading="loading"
      @update:model-value="update('region', $event)"
    >
      <el-option v-for="item in regions" :key="item.id" :label="item.name" :value="item.id" />
    </el-select>
    <el-select
      :model-value="location.availability_zone"
      clearable
      filterable
      placeholder="全部可用区"
      :disabled="!zones.length"
      @update:model-value="update('availability_zone', $event)"
    >
      <el-option
        v-for="item in zones"
        :key="item.id"
        :label="[item.city_name, item.name].filter(Boolean).join(' / ')"
        :value="item.id"
      />
    </el-select>
    <el-select
      :model-value="location.pop_point"
      clearable
      filterable
      placeholder="全部 POP"
      :disabled="!popPoints.length"
      @update:model-value="update('pop_point', $event)"
    >
      <el-option
        v-for="item in popPoints"
        :key="item.id"
        :label="popLabel(item)"
        :value="item.id"
      />
    </el-select>
  </div>
</template>

<script setup>
const props = defineProps({
  location: { type: Object, required: true },
  regions: { type: Array, default: () => [] },
  zones: { type: Array, default: () => [] },
  popPoints: { type: Array, default: () => [] },
  popLabel: { type: Function, required: true },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['update', 'change'])

function update(field, value) {
  const next = { ...props.location, [field]: value || '' }
  if (field === 'region') {
    next.country = ''
    next.city = ''
    next.availability_zone = ''
    next.pop_point = ''
  }
  if (field === 'availability_zone') {
    next.pop_point = ''
  }
  emit('update', next)
  emit('change', next)
}
</script>

<style scoped>
.location-filter { display:flex; gap:8px; flex-wrap:wrap; }
.location-filter :deep(.el-select) { width:150px; }
@media (max-width: 768px) {
  .location-filter :deep(.el-select) { flex:1 1 150px; }
}
</style>
