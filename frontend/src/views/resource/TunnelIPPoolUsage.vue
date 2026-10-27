<template>
  <div v-loading="usageLoading" class="tunnel-usage-page">
    <div class="page-header">
      <el-button :icon="ArrowLeft" @click="goBack">返回</el-button>
      <template v-if="usage">
        <span class="mono ph-pool">{{ usage.pool }}</span>
        <span class="ph-cidr">{{ usage.cidr }}</span>
        <span class="dt-meta">
          共 {{ usage.total_blocks }} 块 / 已用 {{ usage.used_blocks }} / 空闲 {{ usage.free_blocks }}
        </span>
      </template>
      <el-button size="small" :icon="Refresh" :loading="usageLoading" class="ph-refresh" @click="load">刷新</el-button>
    </div>

    <div v-if="usage">
      <el-alert v-if="usage.next_free" type="success" :closable="false" show-icon style="margin-bottom:12px">
        下一段可用：本端 <b>{{ usage.next_free.local_cidr }}</b>，对端 <b>{{ usage.next_free.peer_cidr }}</b>
      </el-alert>
      <el-alert v-else type="warning" :closable="false" show-icon style="margin-bottom:12px">
        该地址段已无可用 p2p 块
      </el-alert>

      <div class="sub-title">已占用块（{{ (usage.used || []).length }}）</div>
      <el-table :data="usage.used" border size="small" empty-text="尚无占用">
        <el-table-column prop="subnet" label="子网块" width="150" />
        <el-table-column prop="local_cidr" label="本端" width="150" />
        <el-table-column prop="peer_cidr" label="对端" width="150" />
        <el-table-column label="接口" min-width="140">
          <template #default="{ row }">
            <div v-for="e in row.entries" :key="`if-${e.address}`" class="entry-line">
              <span class="mono">{{ e.interface_code || row.interface_code || '-' }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="客户" min-width="180">
          <template #default="{ row }">
            <div v-for="e in row.entries" :key="`cu-${e.address}`" class="entry-line">
              <template v-if="e.customer_name || e.customer_no">
                <span>{{ e.customer_name || '-' }}</span>
                <span v-if="e.customer_no" class="muted">{{ e.customer_no }}</span>
              </template>
              <span v-else class="muted">{{ row.customer_name || '未关联客户' }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="记录状态 / 归属">
          <template #default="{ row }">
            <div v-for="e in row.entries" :key="e.address" class="entry-line">
              <span class="mono">{{ e.address }}</span>
              <el-tag size="small" :type="(stateMeta[e.status] || {}).type || 'info'">
                {{ (stateMeta[e.status] || {}).label || e.status }}
              </el-tag>
              <span v-if="e.edge" class="muted">{{ e.edge_name ? `${e.edge} ${e.edge_name}` : e.edge }}</span>
              <span v-if="e.vps" class="muted">vps: {{ e.vps }}</span>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Refresh, ArrowLeft } from '@element-plus/icons-vue'
import { getTunnelIPPoolUsage } from '@/api/resource'

const route = useRoute()
const router = useRouter()
const poolId = computed(() => route.params.id)

const usageLoading = ref(false)
const usage = ref(null)

const stateMeta = {
  available: { label: '可用', type: 'success' },
  assigned: { label: '已分配', type: 'primary' },
  reserved: { label: '预留', type: 'warning' },
  blocked: { label: '封禁', type: 'danger' },
  system: { label: '系统保留', type: 'info' },
}

function goBack() {
  router.push('/resource/tunnel-ip')
}

async function load() {
  if (!poolId.value) return
  usageLoading.value = true
  try {
    usage.value = await getTunnelIPPoolUsage(poolId.value)
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e.message || '加载用量失败')
  } finally {
    usageLoading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.tunnel-usage-page { background:#fff; border-radius:8px; padding:16px 20px 24px; min-height:calc(100vh - 120px); }
.page-header { display:flex; align-items:center; gap:10px; margin-bottom:16px; padding-bottom:14px; border-bottom:1px solid #ebeef5; }
.ph-pool { font-size:15px; font-weight:700; color:#1a1a2e; }
.ph-cidr { font-size:14px; color:#606266; }
.dt-meta { color:#888; font-size:12px; }
.ph-refresh { margin-left:auto; }
.mono { font-family: monospace; }
.sub-title { font-weight:600; color:#4a5b6c; margin-bottom:6px; font-size:13px; }
.entry-line { display:flex; align-items:center; gap:8px; padding:2px 0; }
.muted { color:#999; font-size:12px; }
</style>
