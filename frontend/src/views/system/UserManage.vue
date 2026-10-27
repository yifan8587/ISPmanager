<template>
  <div class="page-container">
    <div class="page-header">
      <h2 class="page-title">系统用户管理</h2>
      <el-button type="primary" :icon="Plus" @click="openCreateDialog">新建用户</el-button>
    </div>

    <!-- 搜索 -->
    <el-card shadow="never" class="search-card">
      <el-form :model="searchForm" inline>
        <el-form-item label="关键词">
          <el-input v-model="searchForm.search" placeholder="用户名/邮箱" clearable style="width:180px" @keyup.enter="loadData" />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="searchForm.is_active" clearable placeholder="全部" style="width:100px">
            <el-option label="启用" value="true" />
            <el-option label="禁用" value="false" />
          </el-select>
        </el-form-item>
        <el-form-item label="账号类型">
          <el-select v-model="searchForm.account_type" clearable placeholder="全部" style="width:120px">
            <el-option v-for="t in accountTypeOptions" :key="t.value" :label="t.label" :value="t.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="searchForm.role" clearable placeholder="全部" style="width:120px">
            <el-option v-for="r in roleOptions" :key="r.value" :label="r.label" :value="r.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="绑定供应商">
          <el-select v-model="searchForm.supplier_id" clearable filterable placeholder="全部" style="width:180px">
            <el-option v-for="s in suppliers" :key="s.id" :label="s.short_name || s.name" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="绑定渠道">
          <el-select v-model="searchForm.channel_id" clearable filterable placeholder="全部" style="width:180px">
            <el-option v-for="c in channelCustomers" :key="c.id" :label="`${c.customer_no || ''} ${c.name}`" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="loadData">搜索</el-button>
          <el-button @click="resetSearch">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 表格 -->
    <el-card shadow="never" style="margin-top:12px;">
      <el-table :data="tableData" v-loading="loading" stripe border>
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column label="用户" min-width="150">
          <template #default="{ row }">
            <div style="display:flex;align-items:center;gap:10px;">
              <el-avatar :size="32" :style="{ background: getAvatarColor(row.username) }">
                {{ row.username.charAt(0).toUpperCase() }}
              </el-avatar>
              <div>
                <div style="font-weight:600;font-size:14px;">{{ row.username }}</div>
                <div style="color:#909399;font-size:12px;">{{ row.email || '-' }}</div>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="姓名" width="100">
          <template #default="{ row }">{{ row.first_name || row.last_name ? `${row.first_name}${row.last_name}` : '-' }}</template>
        </el-table-column>
        <el-table-column label="权限概要" min-width="220">
          <template #default="{ row }">
            <div class="perm-summary">
              <el-tag v-for="tag in summarizePermissions(row.permissions)" :key="tag" size="small" type="info">
                {{ tag }}
              </el-tag>
              <el-text v-if="!summarizePermissions(row.permissions).length" type="info" size="small">
                {{ row.is_superuser ? '全部权限' : '—' }}
              </el-text>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="role" label="角色" width="140">
          <template #default="{ row }">
            <el-tooltip :content="getRoleDescription(row.role)" placement="top" :disabled="!getRoleDescription(row.role)">
              <el-tag
                :type="roleTagMap[row.role]"
                size="small"
                class="role-tag-link"
                @click="openRolePermissionConfig(row.role)"
              >
                {{ row.role_label || getRoleLabel(row.role) }}
              </el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="account_type" label="账号类型" width="110">
          <template #default="{ row }">
            <el-tag :type="row.account_type === 'customer' ? 'success' : 'primary'" size="small">
              {{ getAccountTypeLabel(row.account_type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="customer_name" label="归属客户" width="120" show-overflow-tooltip>
          <template #default="{ row }">{{ row.customer_name || '-' }}</template>
        </el-table-column>
        <el-table-column prop="supplier_name" label="绑定供应商" width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ row.supplier_name || '-' }}</template>
        </el-table-column>
        <el-table-column prop="channel_name" label="绑定渠道" width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ row.channel_name || '-' }}</template>
        </el-table-column>
        <el-table-column label="信誉值" width="150">
          <template #default="{ row }">
            <template v-if="row.account_type === 'customer' && row.reputation_mode && row.reputation_mode !== 'none'">
              <div>{{ reputationModeLabel(row.reputation_mode) }}</div>
              <div class="muted">{{ row.reputation_balance }} / {{ row.reputation_quota }}</div>
            </template>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="department" label="部门" width="100">
          <template #default="{ row }">{{ row.department || '-' }}</template>
        </el-table-column>
        <el-table-column prop="phone" label="手机号" width="130">
          <template #default="{ row }">{{ row.phone || '-' }}</template>
        </el-table-column>
        <el-table-column prop="is_active" label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'danger'" size="small">{{ row.is_active ? '启用' : '禁用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="token_count" label="API Token" width="90" align="center">
          <template #default="{ row }">
            <el-badge :value="row.token_count" :max="99" :hidden="!row.token_count">
              <el-button link size="small" type="primary" @click="viewTokens(row)">
                <el-icon><Key /></el-icon>
              </el-button>
            </el-badge>
          </template>
        </el-table-column>
        <el-table-column prop="last_login" label="最后登录" width="160">
          <template #default="{ row }">{{ formatDate(row.last_login) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="280" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEditDialog(row)">编辑</el-button>
            <el-button link type="primary" size="small" @click="viewPermissions(row)">权限</el-button>
            <el-button link type="warning" size="small" @click="openResetPwdDialog(row)">重置密码</el-button>
            <el-dropdown size="small" @command="(cmd) => handleCommand(cmd, row)">
              <el-button link type="primary" size="small">
                更多<el-icon class="el-icon--right"><ArrowDown /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item v-if="row.is_active" command="disable" style="color:#E6A23C">禁用账号</el-dropdown-item>
                  <el-dropdown-item v-else command="enable" style="color:#67C23A">启用账号</el-dropdown-item>
                  <el-dropdown-item command="tokens">管理 Token</el-dropdown-item>
                  <el-dropdown-item command="delete" style="color:#f56c6c">删除用户</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total"
        layout="total, sizes, prev, pager, next" class="pagination" @change="loadData" />
    </el-card>

    <!-- 创建/编辑用户对话框 -->
    <el-dialog v-model="userDialogVisible" :title="editUser ? '编辑用户' : '新建用户'" width="720px" destroy-on-close>
      <el-form ref="userFormRef" :model="userForm" :rules="userRules" label-width="90px">
        <el-divider content-position="left">账号信息</el-divider>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="用户名" prop="username">
              <el-input v-model="userForm.username" :disabled="!!editUser" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="邮箱">
              <el-input v-model="userForm.email" />
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="!editUser">
            <el-form-item label="密码" prop="password">
              <el-input v-model="userForm.password" type="password" show-password />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="账号类型" prop="account_type">
              <el-select v-model="userForm.account_type" style="width:100%" @change="onAccountTypeChange">
                <el-option v-for="t in accountTypeOptions" :key="t.value" :label="t.label" :value="t.value" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="角色" prop="role">
              <div class="role-field">
                <el-select
                  v-model="userForm.role"
                  style="width:100%"
                  placeholder="选择权限管理中的角色"
                  filterable
                  :disabled="userForm.account_type === 'customer'"
                >
                  <el-option
                    v-for="r in availableRoleDetails"
                    :key="r.value"
                    :label="r.label"
                    :value="r.value"
                  >
                    <div class="role-option">
                      <div class="role-option-title">
                        {{ r.label }}
                        <el-tag size="small" type="info" effect="plain" style="margin-left:6px">{{ r.value }}</el-tag>
                      </div>
                      <div class="role-option-desc">{{ r.description || '—' }}</div>
                      <div class="role-option-meta">有效权限 {{ r.permissionCount }} 项 · 来自权限管理</div>
                    </div>
                  </el-option>
                </el-select>
                <el-button
                  link
                  type="primary"
                  :disabled="!userForm.role"
                  @click="openRolePermissionConfig(userForm.role)"
                >
                  配置该角色权限
                </el-button>
              </div>
              <div v-if="selectedRoleMeta?.description" class="role-hint">
                {{ selectedRoleMeta.description }}
              </div>
              <el-alert
                v-if="userForm.account_type === 'customer'"
                class="role-alert"
                type="warning"
                :closable="false"
                show-icon
                title="归属客户账号默认绑定「客户用户」角色（含服务管理与变更）；也可改为「只读用户」。权限随权限管理中该角色授权同步，并自动限制在客户安全域内。"
              />
              <el-alert
                v-else
                class="role-alert"
                type="info"
                :closable="false"
                show-icon
                title="角色列表与权限完全同步自「系统设置 → 权限管理 → 角色权限」。更换角色即切换对应授权。"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="userForm.account_type === 'customer'">
            <el-form-item label="归属客户" prop="customer_id">
              <el-select v-model="userForm.customer_id" filterable clearable placeholder="选择客户" style="width:100%">
                <el-option v-for="c in customers" :key="c.id" :label="c.name" :value="c.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="userForm.account_type === 'system' && userForm.role === 'supplier'">
            <el-form-item label="绑定供应商" prop="supplier_id">
              <el-select v-model="userForm.supplier_id" filterable placeholder="选择要关联的供应商" style="width:100%">
                <el-option v-for="s in suppliers" :key="s.id" :label="`${s.supplier_no || ''} ${s.name}`" :value="s.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="24" v-if="userForm.role === 'supplier'">
            <el-alert
              type="warning"
              :closable="false"
              show-icon
              title="供应商账号必须绑定对应供应商。登录后仅能查看、修改、上传本供应商的产品、POP、IP/隧道，并查看本供应商账单。"
            />
          </el-col>
          <el-col :span="12" v-if="userForm.account_type === 'system' && userForm.role === 'channel'">
            <el-form-item label="绑定渠道" prop="channel_id">
              <el-select v-model="userForm.channel_id" filterable placeholder="选择要关联的渠道客户" style="width:100%">
                <el-option v-for="c in channelCustomers" :key="c.id" :label="`${c.customer_no || ''} ${c.name}`" :value="c.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="24" v-if="userForm.role === 'channel'">
            <el-alert
              type="warning"
              :closable="false"
              show-icon
              title="渠道账号必须绑定渠道客户。登录后可为本渠道及名下客户下单、新增客户与登录账号，仅能查看该渠道客户消费、推广佣金与结算；不含产品管理、资源管理权限。"
            />
          </el-col>
          <template v-if="userForm.account_type === 'customer'">
            <el-col :span="24">
              <el-divider content-position="left">客户信誉值（写入归属客户）</el-divider>
            </el-col>
            <el-col :span="12">
              <el-form-item label="信誉值模式">
                <el-select v-model="userForm.reputation_mode" style="width:100%">
                  <el-option v-for="m in reputationModeOptions" :key="m.value" :label="m.label" :value="m.value" />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="信誉值额度">
                <el-input-number
                  v-model="userForm.reputation_quota"
                  :min="0"
                  :precision="2"
                  :disabled="userForm.reputation_mode === 'none'"
                  controls-position="right"
                  style="width:100%"
                />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="当前余额">
                <el-input-number
                  v-model="userForm.reputation_balance"
                  :min="0"
                  :precision="2"
                  :disabled="userForm.reputation_mode === 'none'"
                  controls-position="right"
                  style="width:100%"
                />
              </el-form-item>
            </el-col>
          </template>
          <el-col :span="12">
            <el-form-item label="姓名">
              <el-input v-model="userForm.first_name" placeholder="名" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="姓氏">
              <el-input v-model="userForm.last_name" placeholder="姓" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-divider content-position="left">扩展信息</el-divider>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="手机号">
              <el-input v-model="userForm.phone" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="部门">
              <el-input v-model="userForm.department" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="职位">
              <el-input v-model="userForm.position" />
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="editUser">
            <el-form-item label="账号状态">
              <el-switch v-model="userForm.is_active" active-text="启用" inactive-text="禁用" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="是否管理员">
              <el-switch v-model="userForm.is_staff" active-text="是" inactive-text="否" :disabled="userForm.account_type === 'customer'" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="超级管理员">
              <el-switch v-model="userForm.is_superuser" active-text="是" inactive-text="否" :disabled="userForm.account_type === 'customer'" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="备注">
          <el-input v-model="userForm.remark" type="textarea" :rows="2" />
        </el-form-item>
        <el-divider content-position="left">角色权限预览（与权限管理同步）</el-divider>
        <el-alert
          type="info"
          :closable="false"
          show-icon
          style="margin-bottom:12px"
          :title="permissionPreviewHint"
        />
        <div class="role-preview-actions">
          <el-button size="small" @click="loadRoleCatalog" :loading="roleCatalogLoading">刷新角色权限</el-button>
          <el-button
            size="small"
            type="primary"
            plain
            :disabled="!userForm.role"
            @click="openRolePermissionConfig(userForm.role)"
          >
            前往权限管理编辑「{{ selectedRoleMeta?.label || userForm.role }}」
          </el-button>
        </div>
        <PermissionPreview
          :role-catalog="roleCatalog"
          :role="userForm.role"
          :permission-groups="permissionGroups"
          :permissions="previewPermissions"
        />
      </el-form>
      <template #footer>
        <el-button @click="userDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSaveUser">确认</el-button>
      </template>
    </el-dialog>

    <!-- 重置密码对话框 -->
    <el-dialog v-model="resetPwdVisible" :title="`重置密码 - ${selectedUser?.username}`" width="420px" destroy-on-close>
      <el-form ref="resetPwdFormRef" :model="resetPwdForm" :rules="resetPwdRules" label-width="90px">
        <el-form-item label="新密码" prop="new_password">
          <el-input v-model="resetPwdForm.new_password" type="password" show-password />
        </el-form-item>
        <el-form-item label="确认密码" prop="confirm_password">
          <el-input v-model="resetPwdForm.confirm_password" type="password" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="resetPwdVisible = false">取消</el-button>
        <el-button type="warning" :loading="saving" @click="handleResetPassword">重置密码</el-button>
      </template>
    </el-dialog>

  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search, Key, ArrowDown } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import { getUsers, createUser, updateUser, deleteUser, resetPassword, toggleActive, getRoleCatalog } from '@/api/accounts'
import { getCustomers } from '@/api/customer'
import { getSuppliersBrief } from '@/api/supplier'
import { useAuthStore } from '@/stores/auth'
import PermissionPreview from '@/components/PermissionPreview.vue'
import { MODULE_PERMISSIONS, userHasPermission, ROLE_LABELS } from '@/constants/permissions'

const authStore = useAuthStore()
const router = useRouter()

const loading = ref(false)
const saving = ref(false)
const userDialogVisible = ref(false)
const resetPwdVisible = ref(false)
const userFormRef = ref(null)
const resetPwdFormRef = ref(null)
const editUser = ref(null)
const selectedUser = ref(null)
const tableData = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

const customers = ref([])
const suppliers = ref([])
const channelCustomers = ref([])
const roleCatalog = ref([])
const permissionGroups = ref([])
const roleCatalogLoading = ref(false)

const searchForm = reactive({ search: '', is_active: '', account_type: '', role: '', supplier_id: null, channel_id: null })

const userForm = reactive({
  username: '', email: '', password: '', role: 'operator',
  account_type: 'system', customer_id: null, supplier_id: null, channel_id: null,
  first_name: '', last_name: '', phone: '', department: '', position: '',
  is_staff: false, is_superuser: false, is_active: true, remark: '',
  reputation_mode: 'none', reputation_quota: 0, reputation_balance: 0,
})

const reputationModeOptions = [
  { value: 'none', label: '关闭（不可信誉支付）' },
  { value: 'one_time', label: '一次性' },
  { value: 'monthly', label: '按月更新' },
]
function reputationModeLabel(mode) {
  return reputationModeOptions.find((item) => item.value === mode)?.label || '关闭'
}
watch(() => userForm.reputation_mode, (mode, prev) => {
  if (mode === 'none') {
    userForm.reputation_quota = 0
    userForm.reputation_balance = 0
    return
  }
  if (prev === 'none' && Number(userForm.reputation_balance || 0) === 0) {
    userForm.reputation_balance = Number(userForm.reputation_quota || 0)
  }
})

const resetPwdForm = reactive({ new_password: '', confirm_password: '' })

const roleOptions = computed(() => {
  if (roleCatalog.value.length) {
    return roleCatalog.value.map((r) => ({
      value: r.value,
      label: r.label,
      description: r.description,
      permissionCount: Array.isArray(r.permissions) ? r.permissions.length : 0,
    }))
  }
  return Object.entries(ROLE_LABELS).map(([value, label]) => ({
    value,
    label,
    description: '',
    permissionCount: 0,
  }))
})

const accountTypeOptions = [
  { value: 'system', label: '系统账号' },
  { value: 'customer', label: '归属客户账号' }
]

const roleTagMap = {
  superadmin: 'danger', admin: 'warning', customer_manager: 'success',
  operator: 'primary', sales: '', customer: 'success', readonly: 'info', supplier: 'warning', channel: '',
}

const availableRoles = computed(() =>
  userForm.account_type === 'customer'
    ? roleOptions.value.filter(r => r.value === 'customer' || r.value === 'readonly')
    : roleOptions.value.filter(r => r.value !== 'customer')
)

const availableRoleDetails = computed(() => {
  const fromCatalog = availableRoles.value
  if (fromCatalog.length) return fromCatalog
  // 目录未加载时的静态兜底，保证角色定义始终可见
  const fallback = Object.entries(ROLE_LABELS).map(([value, label]) => ({
    value,
    label,
    description: '',
    permissionCount: 0,
  }))
  return userForm.account_type === 'customer'
    ? fallback.filter(r => r.value === 'customer' || r.value === 'readonly')
    : fallback.filter(r => r.value !== 'customer')
})

const selectedRoleMeta = computed(() =>
  roleCatalog.value.find((r) => r.value === userForm.role)
  || availableRoleDetails.value.find((r) => r.value === userForm.role)
  || null
)

/** 预览权限优先取权限管理中该角色的授权码，保证与角色权限页一致 */
const previewPermissions = computed(() => {
  if (userForm.account_type === 'customer' && editUser.value?.permissions?.length) {
    return editUser.value.permissions
  }
  return selectedRoleMeta.value?.permissions || []
})

const permissionPreviewHint = computed(() => {
  const name = selectedRoleMeta.value?.label || ROLE_LABELS[userForm.role] || userForm.role || '当前角色'
  if (userForm.account_type === 'customer') {
    return `预览对应该客户账号角色「${name}」的有效权限（与权限管理同步，并限制在客户安全域）。`
  }
  return `以下权限来自权限管理中「${name}」的角色授权，保存用户后立即按此角色生效。`
})

const colors = ['#409EFF', '#67C23A', '#E6A23C', '#F56C6C', '#909399', '#8B5CF6']
function getAvatarColor(str) {
  const idx = str.charCodeAt(0) % colors.length
  return colors[idx]
}

function getRoleLabel(role) {
  return (
    roleOptions.value.find(r => r.value === role)?.label
    || roleCatalog.value.find(r => r.value === role)?.label
    || ROLE_LABELS[role]
    || role
  )
}

function getRoleDescription(role) {
  return roleCatalog.value.find((r) => r.value === role)?.description || ''
}

function summarizePermissions(permissions = []) {
  const labels = []
  const user = { permissions, is_superuser: false }
  for (const [, mod] of Object.entries(MODULE_PERMISSIONS)) {
    const hasManage = userHasPermission(user, mod.manage)
    const hasView = userHasPermission(user, mod.view)
    if (hasManage) labels.push(`${mod.label}·管`)
    else if (hasView) labels.push(`${mod.label}·查`)
  }
  return labels.slice(0, 5)
}

function getAccountTypeLabel(type) { return accountTypeOptions.find(t => t.value === type)?.label || type }
function formatDate(date) { return date ? dayjs(date).format('YYYY-MM-DD HH:mm') : '从未登录' }

function viewPermissions(user) {
  router.push(`/system/users/${user.id}/permissions`)
}

function openRolePermissionConfig(role) {
  if (!role) return
  router.push({
    path: '/system/roles',
    query: { tab: 'matrix', role },
  })
}

function onAccountTypeChange(type) {
  if (type === 'customer') {
    userForm.role = 'customer'
    userForm.is_staff = false
    userForm.is_superuser = false
  } else {
    userForm.customer_id = null
    if (userForm.role === 'customer' || userForm.role === 'readonly') {
      if (!editUser.value) userForm.role = 'operator'
    }
    if (userForm.role !== 'supplier') userForm.supplier_id = null
    if (userForm.role !== 'channel') userForm.channel_id = null
  }
}

async function loadRoleCatalog() {
  roleCatalogLoading.value = true
  try {
    const res = await getRoleCatalog()
    const data = res?.data || res
    roleCatalog.value = data.roles || []
    permissionGroups.value = data.permission_groups || []
  } finally {
    roleCatalogLoading.value = false
  }
}

async function loadCustomers() {
  const res = await getCustomers({ page_size: 500 })
  customers.value = res?.results || []
}

async function loadSuppliers() {
  const res = await getSuppliersBrief()
  suppliers.value = Array.isArray(res) ? res : (res?.results || [])
}

async function loadChannelCustomers() {
  const res = await getCustomers({ customer_type: 'channel', page_size: 500 })
  channelCustomers.value = res?.results || []
}

watch(() => userForm.role, (role) => {
  if (role !== 'supplier') userForm.supplier_id = null
  if (role !== 'channel') userForm.channel_id = null
  if (role === 'channel' || role === 'supplier') {
    userForm.account_type = 'system'
    userForm.customer_id = null
    userForm.is_staff = false
    userForm.is_superuser = false
  }
})

const userRules = {
  username: [{ required: true, message: '请输入用户名' }],
  password: [{ required: true, message: '请输入密码' }, { min: 8, message: '密码至少8位' }],
  role: [{ required: true, message: '请选择角色' }],
  supplier_id: [{
    validator: (rule, value, cb) => {
      if (userForm.role === 'supplier' && !value) cb(new Error('供应商角色必须绑定供应商'))
      else cb()
    },
  }],
  channel_id: [{
    validator: (rule, value, cb) => {
      if (userForm.role === 'channel' && !value) cb(new Error('渠道角色必须绑定渠道客户'))
      else cb()
    },
  }],
}
const resetPwdRules = {
  new_password: [{ required: true, message: '请输入新密码' }, { min: 8, message: '密码至少8位' }],
  confirm_password: [{ required: true, message: '请确认密码' }, {
    validator: (rule, value, cb) => value !== resetPwdForm.new_password ? cb(new Error('两次密码不一致')) : cb()
  }]
}

async function loadData() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (searchForm.search) params.search = searchForm.search
    if (searchForm.is_active !== '') params.is_active = searchForm.is_active
    if (searchForm.account_type) params.account_type = searchForm.account_type
    if (searchForm.role) params.role = searchForm.role
    if (searchForm.supplier_id) params.supplier_id = searchForm.supplier_id
    if (searchForm.channel_id) params.channel_id = searchForm.channel_id
    const res = await getUsers(params)
    tableData.value = res?.data?.results || []
    total.value = res?.data?.count || 0
  } finally { loading.value = false }
}

function resetSearch() { Object.assign(searchForm, { search: '', is_active: '', account_type: '', role: '', supplier_id: null, channel_id: null }); loadData() }

function openCreateDialog() {
  editUser.value = null
  Object.assign(userForm, {
    username: '', email: '', password: '', role: 'operator',
    account_type: 'system', customer_id: null, supplier_id: null, channel_id: null,
    first_name: '', last_name: '', phone: '', department: '', position: '',
    is_staff: false, is_superuser: false, is_active: true, remark: '',
    reputation_mode: 'none', reputation_quota: 0, reputation_balance: 0,
  })
  loadRoleCatalog()
  userDialogVisible.value = true
}

function openEditDialog(user) {
  editUser.value = user
  Object.assign(userForm, {
    username: user.username, email: user.email || '', password: '',
    role: user.role || 'operator', account_type: user.account_type || 'system',
    customer_id: user.customer_id || null, supplier_id: user.supplier_id || null, channel_id: user.channel_id || null,
    first_name: user.first_name || '', last_name: user.last_name || '',
    phone: user.phone || '', department: user.department || '', position: user.position || '',
    is_staff: user.is_staff, is_superuser: user.is_superuser, is_active: user.is_active, remark: '',
    reputation_mode: user.reputation_mode || 'none',
    reputation_quota: Number(user.reputation_quota || 0),
    reputation_balance: Number(user.reputation_balance || 0),
  })
  loadRoleCatalog()
  userDialogVisible.value = true
}

const fieldErrorLabels = {
  username: '用户名', password: '密码', email: '邮箱',
  first_name: '姓名', last_name: '姓氏', phone: '手机号',
  department: '部门', position: '职位', role: '角色',
  supplier_id: '绑定供应商', channel_id: '绑定渠道', customer_id: '归属客户', remark: '备注',
}

function formatFieldErrors(detail) {
  if (!detail || typeof detail !== 'object') return ''
  if (typeof detail === 'string') return detail
  const parts = Object.entries(detail).map(([key, val]) => {
    const msg = Array.isArray(val) ? val[0] : (typeof val === 'string' ? val : '')
    if (!msg) return ''
    const label = fieldErrorLabels[key] || key
    return `${label}：${msg}`
  }).filter(Boolean)
  return parts.join('；')
}

function extractError(e, fallback) {
  const data = e?.response?.data
  if (!data) return e?.message || fallback
  if (typeof data === 'string') return data
  const fromDetail = formatFieldErrors(data.detail)
  if (fromDetail) return fromDetail
  if (data.message) return data.message
  const fromBody = formatFieldErrors(data)
  if (fromBody) return fromBody
  if (typeof data.detail === 'string') return data.detail
  return fallback
}

async function handleSaveUser() {
  const valid = await userFormRef.value.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    const payload = { ...userForm }
    if (payload.account_type !== 'customer') {
      payload.customer_id = null
      delete payload.reputation_mode
      delete payload.reputation_quota
      delete payload.reputation_balance
    } else if (payload.reputation_mode === 'none') {
      payload.reputation_quota = 0
      payload.reputation_balance = 0
    }
    if (payload.role !== 'supplier') payload.supplier_id = null
    if (payload.role !== 'channel') payload.channel_id = null
    if (editUser.value) {
      const editingId = editUser.value.id
      delete payload.username
      delete payload.password
      await updateUser(editingId, payload)
      ElMessage.success('用户信息已更新')
      userDialogVisible.value = false
      loadData()
      if (editingId === authStore.user?.id) {
        authStore.refreshPermissions()
      }
    } else {
      await createUser(payload)
      ElMessage.success('用户创建成功')
      userDialogVisible.value = false
      loadData()
    }
  } catch (e) {
    ElMessage.error(extractError(e, '保存失败，请检查表单'))
  } finally { saving.value = false }
}

function openResetPwdDialog(user) {
  selectedUser.value = user
  Object.assign(resetPwdForm, { new_password: '', confirm_password: '' })
  resetPwdVisible.value = true
}

async function handleResetPassword() {
  const valid = await resetPwdFormRef.value.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    await resetPassword(selectedUser.value.id, { ...resetPwdForm })
    ElMessage.success(`用户 ${selectedUser.value.username} 密码已重置`)
    resetPwdVisible.value = false
  } catch (e) {
    ElMessage.error(extractError(e, '重置密码失败'))
  } finally { saving.value = false }
}

function viewTokens(user) {
  router.push(`/system/users/${user.id}/tokens`)
}

async function handleCommand(cmd, user) {
  selectedUser.value = user
  if (cmd === 'tokens') {
    router.push(`/system/users/${user.id}/tokens`)
    return
  }
  if (cmd === 'delete') {
    try {
      await ElMessageBox.confirm(`确定删除用户 "${user.username}"？此操作不可恢复`, '危险操作', { type: 'warning', confirmButtonText: '确认删除', confirmButtonClass: 'el-button--danger' })
    } catch { return }
    try {
      await deleteUser(user.id)
      ElMessage.success(`用户 ${user.username} 已删除`)
      loadData()
    } catch (e) {
      ElMessage.error(extractError(e, '删除失败'))
    }
    return
  }
  if (cmd === 'disable' || cmd === 'enable') {
    const nextActive = cmd === 'enable'
    try {
      await toggleActive(user.id, { is_active: nextActive })
      ElMessage[nextActive ? 'success' : 'warning'](`用户 ${user.username} 已${nextActive ? '启用' : '禁用'}`)
      loadData()
    } catch (e) {
      ElMessage.error(extractError(e, '操作失败'))
    }
  }
}

onMounted(() => { loadRoleCatalog(); loadData(); loadCustomers(); loadSuppliers(); loadChannelCustomers() })
</script>

<style scoped>
.page-container { min-height: 100%; }
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.page-title { font-size: 18px; font-weight: 700; color: #1a1a2e; margin: 0; }
.search-card :deep(.el-card__body) { padding: 16px 16px 0; }
.pagination { margin-top: 16px; justify-content: flex-end; }
.perm-summary { display: flex; flex-wrap: wrap; gap: 4px; }
.role-tag-link { cursor: pointer; }
.role-field {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}
.role-field .el-select { flex: 1; }
.role-option { line-height: 1.3; padding: 4px 0; }
.role-option-title { font-size: 13px; color: #303133; display: flex; align-items: center; }
.role-option-desc {
  font-size: 12px; color: #909399; max-width: 360px;
  white-space: normal;
}
.role-option-meta {
  margin-top: 2px;
  font-size: 11px;
  color: #a0a4ab;
}
.role-hint {
  margin-top: 6px;
  font-size: 12px;
  color: #909399;
  line-height: 1.5;
}
.role-alert { margin-top: 8px; }
.muted { color: #909399; font-size: 12px; }
.role-preview-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}
</style>
