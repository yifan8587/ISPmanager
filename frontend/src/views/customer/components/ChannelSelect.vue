<template>
  <el-select
    :model-value="modelValue"
    clearable
    filterable
    placeholder="全部渠道"
    style="width: 220px"
    @update:model-value="$emit('update:modelValue', $event)"
    @change="$emit('change', $event)"
  >
    <el-option
      v-for="channel in channels"
      :key="channel.id"
      :label="`${channel.name}${channel.customer_no ? `（${channel.customer_no}）` : ''}`"
      :value="channel.id"
    />
  </el-select>
</template>

<script setup>
defineProps({
  modelValue: { type: [Number, String], default: '' },
  channels: { type: Array, default: () => [] },
})
defineEmits(['update:modelValue', 'change'])
</script>
