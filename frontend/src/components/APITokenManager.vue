<template>
  <div class="token-manager">
    <!-- 操作栏 -->
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
      <div>
        <el-tag type="info" style="margin-right:8px;">
          共 {{ tokens.length }} 个令牌
        </el-tag>
        <el-tag v-if="activeCount" type="success">{{ activeCount }} 个有效</el-tag>
      </div>
      <div style="display:flex;gap:8px;">
        <el-button type="primary" :icon="Plus" size="small" @click="openCreateDialog">生成新令牌</el-button>
        <el-button type="danger" plain size="small" @click="handleRevokeAll" v-if="tokens.length">撤销全部</el-button>
      </div>
    </div>

    <!-- 令牌列表 -->
    <div v-loading="loading">
      <div v-if="!tokens.length" class="empty-state">
        <el-icon size="48" color="#C0C4CC"><Key /></el-icon>
        <p>暂无 API 令牌</p>
        <el-button type="primary" size="small" @click="openCreateDialog">生成第一个令牌</el-button>
      </div>
      <div v-for="token in tokens" :key="token.id" class="token-card" :class="{ inactive: !token.is_valid }">
        <div class="token-header">
          <div class="token-info">
            <div class="token-name">
              <el-icon :color="token.is_valid ? '#67C23A' : '#C0C4CC'"><CircleCheckFilled v-if="token.is_valid" /><CircleCloseFilled v-else /></el-icon>
              {{ token.name }}
            </div>
            <div class="token-meta">
              <code class="token-prefix">{{ token.token_prefix }}...</code>
              <el-tag :type="token.is_valid ? 'success' : 'danger'" size="small">{{ token.is_valid ? '有效' : (token.is_expired ? '已过期' : '已禁用') }}</el-tag>
              <span class="meta-text">创建于 {{ formatDate(token.created_at) }}</span>
              <span class="meta-text" v-if="token.last_used_at">最后使用 {{ formatDate(token.last_used_at) }}</span>
              <span class="meta-text" v-if="token.expires_at">过期时间 {{ formatDate(token.expires_at) }}</span>
              <span class="meta-text" v-else>永不过期</span>
            </div>
          </div>
          <div class="token-actions">
            <el-tooltip :content="token.is_active ? '禁用' : '启用'">
              <el-switch v-model="token.is_active" size="small" @change="handleToggle(token)" />
            </el-tooltip>
            <el-button link type="danger" size="small" @click="handleDelete(token)">
              <el-icon><Delete /></el-icon>
            </el-button>
          </div>
        </div>
        <div class="token-ip" v-if="token.last_used_ip">
          最后使用 IP：<code>{{ token.last_used_ip }}</code>
        </div>
      </div>
    </div>

    <!-- 生成令牌对话框 -->
    <el-dialog v-model="createDialogVisible" title="生成新 API 令牌" width="480px" destroy-on-close append-to-body>
      <el-form ref="createFormRef" :model="createForm" :rules="createRules" label-width="90px">
        <el-form-item label="令牌名称" prop="name">
          <el-input v-model="createForm.name" placeholder="如：前端应用、CI/CD 流水线" />
        </el-form-item>
        <el-form-item label="过期时间">
          <el-date-picker
            v-model="createForm.expires_at"
            type="datetime"
            placeholder="留空表示永不过期"
            value-format="YYYY-MM-DDTHH:mm:ss"
            style="width:100%"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="handleCreate">生成令牌</el-button>
      </template>
    </el-dialog>

    <!-- 新令牌展示对话框 -->
    <el-dialog v-model="newTokenDialogVisible" title="🎉 令牌生成成功" width="560px" :close-on-click-modal="false" append-to-body>
      <el-alert type="warning" :closable="false" style="margin-bottom:16px;">
        <template #default>
          <strong>⚠️ 请立即保存！</strong> 原始令牌仅展示一次，关闭后无法再次查看。
        </template>
      </el-alert>
      <div class="new-token-display">
        <div class="token-label">您的 API 令牌：</div>
        <div class="token-value-box">
          <code class="token-value">{{ newToken }}</code>
          <el-button :icon="CopyDocument" size="small" type="primary" plain @click="copyToken">复制</el-button>
        </div>
        <div class="usage-example">
          <div class="usage-title">使用示例：</div>
          <pre class="usage-code">Authorization: Token {{ newToken }}</pre>
          <pre class="usage-code">X-API-Token: {{ newToken }}</pre>
        </div>
      </div>
      <template #footer>
        <el-button type="success" @click="newTokenDialogVisible = false; loadTokens()">
          已保存，关闭
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Delete, Key, CopyDocument } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import { getMyTokens, createToken, updateToken, deleteToken, revokeAllTokens } from '@/api/accounts'

const props = defineProps({
  user: { type: Object, default: null }
})
const emit = defineEmits(['refresh'])

const loading = ref(false)
const creating = ref(false)
const createDialogVisible = ref(false)
const newTokenDialogVisible = ref(false)
const createFormRef = ref(null)
const tokens = ref([])
const newToken = ref('')

const createForm = ref({ name: '', expires_at: null })
const createRules = { name: [{ required: true, message: '请输入令牌名称' }] }

const activeCount = computed(() => tokens.value.filter(t => t.is_valid).length)

function formatDate(date) { return date ? dayjs(date).format('YYYY-MM-DD HH:mm') : '-' }

async function loadTokens() {
  loading.value = true
  try {
    const params = {}
    if (props.user?.id) params.user_id = props.user.id
    const res = await getMyTokens(params)
    tokens.value = res?.data || (Array.isArray(res) ? res : [])
  } catch { tokens.value = [] }
  finally { loading.value = false }
}

function openCreateDialog() {
  createForm.value = { name: '', expires_at: null }
  createDialogVisible.value = true
}

async function handleCreate() {
  const valid = await createFormRef.value.validate().catch(() => false)
  if (!valid) return
  creating.value = true
  try {
    const payload = { name: createForm.value.name }
    if (createForm.value.expires_at) payload.expires_at = createForm.value.expires_at
    if (props.user?.id) payload.user_id = props.user.id
    const res = await createToken(payload)
    const tokenData = res?.data || res
    newToken.value = tokenData.token
    createDialogVisible.value = false
    newTokenDialogVisible.value = true
    emit('refresh')
  } finally { creating.value = false }
}

async function handleToggle(token) {
  try {
    await updateToken(token.id, { is_active: token.is_active })
    ElMessage.success(token.is_active ? '已启用' : '已禁用')
  } catch {
    token.is_active = !token.is_active
  }
}

async function handleDelete(token) {
  await ElMessageBox.confirm(`确定撤销令牌 "${token.name}"？`, '提示', { type: 'warning' })
  await deleteToken(token.id)
  ElMessage.success('已撤销')
  loadTokens()
  emit('refresh')
}

async function handleRevokeAll() {
  await ElMessageBox.confirm('确定撤销所有 API 令牌？', '警告', { type: 'warning' })
  const payload = {}
  if (props.user?.id) payload.user_id = props.user.id
  await revokeAllTokens(payload)
  ElMessage.success('已撤销所有令牌')
  loadTokens()
  emit('refresh')
}

async function copyToken() {
  try {
    await navigator.clipboard.writeText(newToken.value)
    ElMessage.success('已复制到剪贴板')
  } catch {
    ElMessage.warning('请手动复制')
  }
}

onMounted(loadTokens)
</script>

<style scoped>
.token-manager { min-height: 200px; }

.empty-state {
  text-align: center;
  padding: 40px;
  color: #909399;
}

.empty-state p { margin: 12px 0; }

.token-card {
  border: 1px solid #e8e8e8;
  border-radius: 8px;
  padding: 14px 16px;
  margin-bottom: 10px;
  background: #fff;
  transition: box-shadow 0.2s;
}

.token-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
.token-card.inactive { background: #fafafa; opacity: 0.7; }

.token-header { display: flex; align-items: flex-start; justify-content: space-between; }

.token-info { flex: 1; }

.token-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
  color: #1a1a2e;
  margin-bottom: 6px;
}

.token-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.token-prefix {
  font-family: 'Courier New', monospace;
  background: #f0f2f5;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 12px;
  color: #409EFF;
}

.meta-text { font-size: 12px; color: #909399; }

.token-actions { display: flex; align-items: center; gap: 8px; margin-left: 12px; }

.token-ip { font-size: 12px; color: #909399; margin-top: 6px; }
.token-ip code { background: #f0f2f5; padding: 1px 4px; border-radius: 3px; }

.new-token-display { padding: 8px 0; }

.token-label { font-size: 13px; color: #606266; margin-bottom: 10px; }

.token-value-box {
  display: flex;
  align-items: center;
  gap: 10px;
  background: #1a1a2e;
  border-radius: 8px;
  padding: 12px 16px;
  margin-bottom: 16px;
}

.token-value {
  flex: 1;
  font-family: 'Courier New', monospace;
  font-size: 13px;
  color: #67C23A;
  word-break: break-all;
}

.usage-example { background: #f8f9fa; border-radius: 8px; padding: 12px 16px; }
.usage-title { font-size: 12px; color: #909399; margin-bottom: 8px; }
.usage-code {
  font-family: 'Courier New', monospace;
  font-size: 12px;
  color: #409EFF;
  margin: 4px 0;
  background: none;
  padding: 0;
}
</style>
