<template>
  <div class="resource-table-group">
    <el-tag size="small" type="primary">区域：{{ row.region_name }}</el-tag>
    <el-tag size="small" type="info">国家：{{ row.country_name }}</el-tag>
    <span>{{ row.group_count }} 条当前页记录</span>
  </div>
</template>

<script setup>
defineProps({
  row: { type: Object, required: true },
})
</script>

<style scoped>
.resource-table-group { display:flex; align-items:center; gap:8px; min-height:24px; padding:2px 0; white-space:nowrap; }
.resource-table-group span { color:#909399; font-size:12px; }
</style>
