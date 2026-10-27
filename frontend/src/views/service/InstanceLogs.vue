<template>
  <div v-loading="loading" class="instance-logs-page">
    <div class="page-header">
      <el-button :icon="ArrowLeft" @click="goBack">返回</el-button>
      <span class="ph-title">实例日志 - {{ instance?.instance_no || '' }}</span>
      <el-button size="small" :icon="Refresh" :loading="loading" class="ph-refresh" @click="load">刷新</el-button>
    </div>

    <el-timeline>
      <el-timeline-item
        v-for="log in instanceLogs"
        :key="log.id"
        :timestamp="log.created_at"
        placement="top"
        :type="log.action_type === 'error' ? 'danger' : 'primary'"
      >
        <el-card shadow="never" style="padding:8px 12px;">
          <div style="font-size:13px;"><strong>{{ log.operator_name }}</strong> · {{ log.description }}</div>
        </el-card>
      </el-timeline-item>
    </el-timeline>
    <el-empty v-if="!instanceLogs.length" description="暂无日志" />
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Refresh, ArrowLeft } from '@element-plus/icons-vue'
import { getInstance } from '@/api/service'
import request from '@/api/request'

const route = useRoute()
const router = useRouter()
const instanceId = computed(() => route.params.id)

const loading = ref(false)
const instance = ref(null)
const instanceLogs = ref([])

function goBack() {
  router.push('/service/instances')
}

async function load() {
  if (!instanceId.value) return
  loading.value = true
  try {
    instance.value = await getInstance(instanceId.value).catch(() => null)
    const res = await request.get(`/service/instances/${instanceId.value}/logs/`)
    instanceLogs.value = res?.results || (Array.isArray(res) ? res : [])
  } catch {
    instanceLogs.value = []
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.instance-logs-page { background:#fff; border-radius:8px; padding:16px 20px 24px; min-height:calc(100vh - 120px); }
.page-header { display:flex; align-items:center; gap:10px; margin-bottom:16px; padding-bottom:14px; border-bottom:1px solid #ebeef5; }
.ph-title { font-size:16px; font-weight:700; color:#1a1a2e; }
.ph-refresh { margin-left:auto; }
</style>
