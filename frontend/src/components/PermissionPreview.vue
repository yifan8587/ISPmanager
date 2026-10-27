<template>
  <div class="permission-preview">
    <div v-if="roleMeta?.description" class="role-desc">{{ roleMeta.description }}</div>
    <div class="action-hint">
      <el-tag size="small" type="info" effect="plain">查看</el-tag>
      <span>只读浏览</span>
      <el-tag size="small" type="warning" effect="plain">管理</el-tag>
      <span>新增 / 修改 / 删除 / 执行</span>
    </div>
    <el-empty v-if="!groups.length" description="该角色暂无可用权限" :image-size="48" />
    <div v-else class="group-list">
      <div v-for="group in groups" :key="group.key" class="perm-group">
        <div class="group-head">
          <span class="group-title">{{ group.label }}</span>
          <el-text size="small" type="info">{{ group.description }}</el-text>
        </div>

        <div v-if="group.permissions?.length" class="module-tags">
          <el-tag
            v-for="perm in group.permissions"
            :key="perm.code"
            size="small"
            :type="permActionType(perm.action || actionFromPermission(perm.code))"
            :title="perm.code"
          >
            {{ formatPermLabel(perm) }}
          </el-tag>
        </div>

        <div v-for="page in group.pages || []" :key="page.key" class="page-block">
          <div class="page-head">
            <strong>{{ page.label }}</strong>
            <el-text v-if="page.route" size="small" type="info">{{ page.route }}</el-text>
          </div>
          <div v-if="page.description" class="page-desc">{{ page.description }}</div>
          <div class="perm-tags">
            <el-tag
              v-for="perm in page.permissions"
              :key="perm.code"
              size="small"
              :type="permActionType(perm.action || actionFromPermission(perm.code))"
              :title="perm.code"
            >
              {{ formatActionChip(perm) }}
            </el-tag>
          </div>

          <div v-for="cat in page.categories || []" :key="cat.key" class="cat-block">
            <div class="cat-head">{{ cat.label }}</div>
            <div class="perm-tags" v-if="cat.permissions?.length">
              <el-tag
                v-for="perm in cat.permissions"
                :key="perm.code"
                size="small"
                :type="permActionType(perm.action || actionFromPermission(perm.code))"
                :title="perm.code"
              >
                {{ formatActionChip(perm) }}
              </el-tag>
            </div>
            <div class="setting-list">
              <div v-for="setting in visibleSettings(cat)" :key="setting.key" class="setting-row">
                <span class="setting-name">{{ setting.label }}</span>
                <el-tag
                  v-for="perm in setting.permissions"
                  :key="perm.code"
                  size="small"
                  :type="permActionType(perm.action || actionFromPermission(perm.code))"
                  :title="perm.code"
                >
                  {{ formatActionChip(perm) }}
                </el-tag>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { actionFromPermission, userHasPermission } from '@/constants/permissions'
import { useAuthStore } from '@/stores/auth'

const props = defineProps({
  roleCatalog: { type: Array, default: () => [] },
  role: { type: String, default: '' },
  permissionGroups: { type: Array, default: () => [] },
  permissions: { type: Array, default: () => [] },
})

const authStore = useAuthStore()
const roleMeta = computed(() => props.roleCatalog.find((r) => r.value === props.role))

const effectivePermissions = computed(() => {
  if (props.permissions.length) return props.permissions
  return roleMeta.value?.permissions || authStore.user?.permissions || []
})

function filterGroupsByPermissions(groups, codes) {
  if (!groups?.length || !codes?.length) return []
  const user = { permissions: codes, is_superuser: false }
  return groups
    .map((g) => ({
      ...g,
      permissions: (g.permissions || []).filter((p) => userHasPermission(user, p.code)),
      pages: (g.pages || []).map((page) => ({
        ...page,
        permissions: (page.permissions || []).filter((p) => userHasPermission(user, p.code)),
        categories: (page.categories || []).map((cat) => ({
          ...cat,
          permissions: (cat.permissions || []).filter((p) => userHasPermission(user, p.code)),
          settings: (cat.settings || []).map((setting) => ({
            ...setting,
            permissions: (setting.permissions || []).filter((p) => userHasPermission(user, p.code)),
          })).filter((s) => s.permissions.length),
        })).filter((c) => c.permissions.length || c.settings.length),
      })).filter((p) => p.permissions.length || p.categories?.length),
    }))
    .filter((g) => g.permissions.length || g.pages.length)
}

const groups = computed(() => {
  // 显式传入 permissions 时优先按授权码过滤，保证与权限管理角色矩阵一致
  if (props.permissions.length && props.permissionGroups.length) {
    return filterGroupsByPermissions(props.permissionGroups, props.permissions)
  }
  if (roleMeta.value?.permission_groups?.length) {
    return roleMeta.value.permission_groups
  }
  return filterGroupsByPermissions(props.permissionGroups, effectivePermissions.value)
})

function visibleSettings(cat) {
  return (cat.settings || []).filter((s) => s.permissions?.length)
}

function permActionType(action) {
  return action === 'manage' ? 'warning' : 'info'
}

function formatActionChip(perm) {
  const action = perm.action || actionFromPermission(perm.code)
  return action === 'manage' ? '管理' : '查看'
}

function formatPermLabel(perm) {
  return perm.label || formatActionChip(perm)
}
</script>

<style scoped>
.permission-preview { width: 100%; }
.role-desc {
  margin-bottom: 10px;
  padding: 8px 12px;
  background: #f5f7fa;
  border-radius: 6px;
  font-size: 13px;
  color: #606266;
}
.action-hint {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
  font-size: 12px;
  color: #909399;
}
.group-list { display: flex; flex-direction: column; gap: 10px; }
.perm-group {
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 10px 12px;
  background: #fafafa;
}
.group-head { margin-bottom: 8px; }
.group-title { font-weight: 600; font-size: 13px; margin-right: 8px; }
.module-tags, .perm-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
.page-block {
  margin-top: 8px;
  padding: 8px 10px;
  background: #fff;
  border: 1px dashed #e4e7ed;
  border-radius: 6px;
}
.page-head { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; font-size: 13px; }
.page-desc { font-size: 12px; color: #909399; margin-bottom: 6px; }
.cat-block { margin-top: 8px; padding-left: 8px; border-left: 2px solid #e4e7ed; }
.cat-head { font-size: 12px; color: #606266; margin-bottom: 6px; font-weight: 600; }
.setting-list { display: flex; flex-direction: column; gap: 4px; }
.setting-row { display: flex; align-items: center; gap: 6px; font-size: 12px; }
.setting-name { min-width: 120px; color: #303133; }
</style>
