<template>
  <div v-loading="loading" class="user-perm-page">
    <div class="page-header">
      <el-button :icon="ArrowLeft" @click="goBack">返回</el-button>
      <span class="ph-title">权限详情 - {{ user?.username || '' }}</span>
    </div>

    <el-descriptions :column="2" border size="small" style="margin-bottom:16px">
      <el-descriptions-item label="用户">{{ user?.username || '—' }}</el-descriptions-item>
      <el-descriptions-item label="角色">
        <el-tag size="small">{{ user?.role_label || getRoleLabel(user?.role) }}</el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="账号类型">{{ getAccountTypeLabel(user?.account_type) }}</el-descriptions-item>
      <el-descriptions-item label="有效权限">{{ user?.permissions?.length || 0 }} 项</el-descriptions-item>
      <el-descriptions-item label="角色说明" :span="2">
        {{ roleCatalog.find(r => r.value === user?.role)?.description || '—' }}
      </el-descriptions-item>
    </el-descriptions>
    <el-alert
      type="info"
      :closable="false"
      show-icon
      style="margin-bottom:12px"
      title="以下为该用户当前有效权限。调整方式：修改用户角色，或在「权限管理 → 角色权限」中配置角色原子权限。"
    />
    <PermissionPreview
      :role-catalog="roleCatalog"
      :role="user?.role"
      :permission-groups="permissionGroups"
      :permissions="user?.permissions || []"
    />
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft } from '@element-plus/icons-vue'
import { getUser, getRoleCatalog } from '@/api/accounts'
import PermissionPreview from '@/components/PermissionPreview.vue'
import { ROLE_LABELS } from '@/constants/permissions'

const route = useRoute()
const router = useRouter()
const userId = computed(() => route.params.id)

const loading = ref(false)
const user = ref(null)
const roleCatalog = ref([])
const permissionGroups = ref([])

const accountTypeOptions = [
  { value: 'system', label: '系统账号' },
  { value: 'customer', label: '归属客户账号' },
]

function getRoleLabel(role) {
  return roleCatalog.value.find(r => r.value === role)?.label || ROLE_LABELS[role] || role
}
function getAccountTypeLabel(type) { return accountTypeOptions.find(t => t.value === type)?.label || type }

function goBack() {
  router.push('/system/users')
}

async function load() {
  if (!userId.value) return
  loading.value = true
  try {
    const cat = await getRoleCatalog().catch(() => null)
    const catData = cat?.data || cat || {}
    roleCatalog.value = catData.roles || []
    permissionGroups.value = catData.permission_groups || []
    const res = await getUser(userId.value)
    user.value = res?.data || res
  } catch (e) {
    ElMessage.error('加载失败：' + (e?.response?.data?.message || e?.response?.data?.detail || e.message))
  } finally { loading.value = false }
}

onMounted(load)
</script>

<style scoped>
.user-perm-page { background:#fff; border-radius:8px; padding:16px 20px 24px; min-height:calc(100vh - 120px); }
.page-header { display:flex; align-items:center; gap:10px; margin-bottom:16px; padding-bottom:14px; border-bottom:1px solid #ebeef5; }
.ph-title { font-size:16px; font-weight:700; color:#1a1a2e; }
</style>
