<template>
  <el-pagination
    :current-page="modelValue"
    :total="total"
    :page-size="pageSize"
    layout="total, prev, pager, next"
    class="pagination"
    @update:current-page="$emit('update:modelValue', $event)"
    @current-change="$emit('change', $event)"
  />
</template>

<script setup>
defineProps({
  modelValue: { type: Number, default: 1 },
  total: { type: Number, default: 0 },
  pageSize: { type: Number, default: 20 },
})
defineEmits(['update:modelValue', 'change'])
</script>

<style scoped>
.pagination { margin-top: 16px; justify-content: flex-end; }
</style>
