<template>
  <div v-loading="loading" class="user-token-page">
    <div class="page-header">
      <el-button :icon="ArrowLeft" @click="goBack">返回</el-button>
      <span class="ph-title">API Token 管理 - {{ user?.username || '' }}</span>
    </div>

    <APITokenManager v-if="user" :user="user" @refresh="load" />
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft } from '@element-plus/icons-vue'
import { getUser } from '@/api/accounts'
import APITokenManager from '@/components/APITokenManager.vue'

const route = useRoute()
const router = useRouter()
const userId = computed(() => route.params.id)

const loading = ref(false)
const user = ref(null)

function goBack() {
  router.push('/system/users')
}

async function load() {
  if (!userId.value) return
  loading.value = true
  try {
    const res = await getUser(userId.value)
    user.value = res?.data || res
  } catch (e) {
    ElMessage.error('加载失败：' + (e?.response?.data?.message || e?.response?.data?.detail || e.message))
  } finally { loading.value = false }
}

onMounted(load)
</script>

<style scoped>
.user-token-page { background:#fff; border-radius:8px; padding:16px 20px 24px; min-height:calc(100vh - 120px); }
.page-header { display:flex; align-items:center; gap:10px; margin-bottom:16px; padding-bottom:14px; border-bottom:1px solid #ebeef5; }
.ph-title { font-size:16px; font-weight:700; color:#1a1a2e; }
</style>
