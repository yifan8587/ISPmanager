<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <h2 class="page-title">权限管理</h2>
        <p class="page-subtitle">
          权限按三级组织：模块 → 页面 → 配置项；原子动作只有「查看(只读)」和「管理(增删改执行)」。
        </p>
      </div>
      <div class="header-actions">
        <el-button v-if="authStore.user?.is_superuser" @click="handleResetDefaults">恢复默认</el-button>
        <el-button type="primary" @click="$router.push('/system/users')">用户账号管理</el-button>
      </div>
    </div>

    <div class="action-legend" v-if="resolvedActionMeta.length">
      <div
        v-for="meta in resolvedActionMeta"
        :key="meta.key"
        class="legend-item"
        :class="`legend-${meta.key}`"
      >
        <el-tag size="small" :type="meta.key === 'view' ? 'info' : 'warning'" effect="plain">
          {{ meta.label || (meta.key === 'view' ? '查看' : '管理') }}
        </el-tag>
        <span class="legend-desc">{{ meta.description }}</span>
      </div>
    </div>

    <el-row :gutter="12" class="stat-row">
      <el-col :xs="12" :sm="6">
        <div class="stat-card">
          <span class="stat-num">{{ permissionGroups.length }}</span>
          <span class="stat-lbl">功能模块</span>
        </div>
      </el-col>
      <el-col :xs="12" :sm="6">
        <div class="stat-card">
          <span class="stat-num">{{ totalPageCount }}</span>
          <span class="stat-lbl">管理页面</span>
        </div>
      </el-col>
      <el-col :xs="12" :sm="6">
        <div class="stat-card">
          <span class="stat-num">{{ totalPermissionCount }}</span>
          <span class="stat-lbl">原子权限项</span>
        </div>
      </el-col>
      <el-col :xs="12" :sm="6">
        <div class="stat-card">
          <span class="stat-num">{{ roles.length }}</span>
          <span class="stat-lbl">系统角色</span>
        </div>
      </el-col>
    </el-row>

    <el-card shadow="never" v-loading="loading">
      <el-tabs v-model="activeTab">
        <!-- 权限目录 -->
        <el-tab-pane label="权限目录" name="catalog">
          <div class="tab-toolbar">
            <div class="toolbar-left">
              <el-input
                v-model="catalogSearch"
                clearable
                size="small"
                placeholder="搜索模块 / 页面 / 权限码 / 名称"
                style="width: 280px"
              />
              <el-switch
                v-model="catalogEditMode"
                size="small"
                inline-prompt
                active-text="编辑"
                inactive-text="只读"
              />
              <el-text type="info" size="small">
                {{ catalogEditMode ? '编辑模式：可修改名称并保存' : '只读模式：浏览权限目录' }}
              </el-text>
            </div>
            <el-button type="primary" plain size="small" @click="openAddPageDialog">新增页面</el-button>
          </div>

          <el-empty v-if="!filteredGroups.length" description="没有匹配的权限目录" />

          <el-collapse v-model="catalogCollapse" accordion>
            <el-collapse-item
              v-for="group in filteredGroups"
              :key="group.key"
              :name="group.key"
            >
              <template #title>
                <div class="collapse-title">
                  <strong>{{ group.label }}</strong>
                  <el-text size="small" type="info">{{ group.description }}</el-text>
                  <el-tag size="small" type="info" effect="plain" class="count-tag">
                    {{ (group.pages || []).length }} 页
                  </el-tag>
                </div>
              </template>

              <div v-if="catalogEditMode" class="group-edit-row">
                <el-input
                  v-model="groupEditForms[group.key].label"
                  size="small"
                  placeholder="模块名称"
                  style="width: 180px"
                />
                <el-input
                  v-model="groupEditForms[group.key].description"
                  size="small"
                  placeholder="模块说明"
                  style="flex: 1"
                />
                <el-button
                  size="small"
                  type="primary"
                  :loading="savingGroup === group.key"
                  @click="saveGroup(group)"
                >
                  保存模块
                </el-button>
              </div>
              <div v-else class="group-readonly-meta">
                <span class="meta-label">模块说明</span>
                <span>{{ group.description || '—' }}</span>
              </div>

              <div class="page-list">
                <div
                  v-for="row in flattenPages(group)"
                  :key="row.formKey"
                  class="page-row"
                >
                  <div class="page-row-main">
                    <div class="page-row-head">
                      <template v-if="catalogEditMode">
                        <el-input
                          v-model="pageEditForms[row.formKey].label"
                          size="small"
                          style="width: 180px"
                        />
                        <el-input
                          v-model="pageEditForms[row.formKey].route"
                          size="small"
                          placeholder="路由"
                          style="width: 220px"
                        />
                      </template>
                      <template v-else>
                        <strong class="page-label">{{ row.pageLabel }}</strong>
                        <code v-if="row.route" class="route-code">{{ row.route }}</code>
                      </template>
                    </div>
                    <p v-if="row.description" class="page-desc">{{ row.description }}</p>

                    <div class="perm-chips">
                      <div v-if="row.view" class="perm-chip chip-view">
                        <code class="perm-code">{{ row.view.code }}</code>
                        <template v-if="catalogEditMode">
                          <el-input
                            v-model="permEditForms[row.view.code].label"
                            size="small"
                            style="width: 160px"
                          />
                        </template>
                        <span v-else class="perm-label">{{ row.view.label }}</span>
                      </div>
                      <div v-if="row.manage" class="perm-chip chip-manage">
                        <code class="perm-code">{{ row.manage.code }}</code>
                        <template v-if="catalogEditMode">
                          <el-input
                            v-model="permEditForms[row.manage.code].label"
                            size="small"
                            style="width: 160px"
                          />
                        </template>
                        <span v-else class="perm-label">{{ row.manage.label }}</span>
                      </div>
                    </div>
                  </div>
                  <div v-if="catalogEditMode" class="page-row-actions">
                    <el-button
                      link
                      type="primary"
                      size="small"
                      :loading="savingPage === row.formKey"
                      @click="savePage(group.key, row)"
                    >
                      保存
                    </el-button>
                  </div>
                </div>
              </div>

              <div v-if="group.key === 'system'" class="setting-section">
                <h4>系统配置项权限</h4>
                <el-text type="info" size="small" class="setting-hint">
                  按分类展示配置项；展开可查看各配置键的查看/管理权限码
                </el-text>

                <el-table
                  :data="settingCategoryRows(group)"
                  border
                  stripe
                  size="small"
                  class="setting-table"
                  row-key="key"
                >
                  <el-table-column type="expand">
                    <template #default="{ row }">
                      <div class="setting-keys">
                        <div
                          v-for="s in row.settings"
                          :key="s.key"
                          class="setting-key-row"
                        >
                          <div class="setting-key-meta">
                            <strong>{{ s.settingLabel }}</strong>
                            <code class="perm-code">{{ s.key }}</code>
                          </div>
                          <div class="perm-chips compact">
                            <div v-if="s.viewCode" class="perm-chip chip-view">
                              <code class="perm-code">{{ s.viewCode }}</code>
                              <template v-if="catalogEditMode">
                                <el-input
                                  v-model="permEditForms[s.viewCode].label"
                                  size="small"
                                  style="width: 140px"
                                />
                              </template>
                              <span v-else class="perm-label">{{ s.viewLabel }}</span>
                            </div>
                            <div v-if="s.manageCode" class="perm-chip chip-manage">
                              <code class="perm-code">{{ s.manageCode }}</code>
                              <template v-if="catalogEditMode">
                                <el-input
                                  v-model="permEditForms[s.manageCode].label"
                                  size="small"
                                  style="width: 140px"
                                />
                              </template>
                              <span v-else class="perm-label">{{ s.manageLabel }}</span>
                            </div>
                            <el-button
                              v-if="catalogEditMode"
                              link
                              type="primary"
                              size="small"
                              @click="saveSettingPerms(s)"
                            >
                              保存
                            </el-button>
                          </div>
                        </div>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column prop="label" label="分类" min-width="120" />
                  <el-table-column label="配置项数" width="100" align="center">
                    <template #default="{ row }">{{ row.settings.length }}</template>
                  </el-table-column>
                  <el-table-column label="分类查看" min-width="200">
                    <template #default="{ row }">
                      <template v-if="row.viewCode">
                        <code class="perm-code">{{ row.viewCode }}</code>
                        <el-input
                          v-if="catalogEditMode"
                          v-model="permEditForms[row.viewCode].label"
                          size="small"
                          style="margin-top: 4px"
                        />
                        <div v-else class="perm-label muted">{{ row.viewLabel }}</div>
                      </template>
                    </template>
                  </el-table-column>
                  <el-table-column label="分类管理" min-width="200">
                    <template #default="{ row }">
                      <template v-if="row.manageCode">
                        <code class="perm-code">{{ row.manageCode }}</code>
                        <el-input
                          v-if="catalogEditMode"
                          v-model="permEditForms[row.manageCode].label"
                          size="small"
                          style="margin-top: 4px"
                        />
                        <div v-else class="perm-label muted">{{ row.manageLabel }}</div>
                      </template>
                    </template>
                  </el-table-column>
                  <el-table-column v-if="catalogEditMode" label="" width="80">
                    <template #default="{ row }">
                      <el-button link type="primary" size="small" @click="saveCategoryPerms(row)">
                        保存
                      </el-button>
                    </template>
                  </el-table-column>
                </el-table>
              </div>
            </el-collapse-item>
          </el-collapse>
        </el-tab-pane>

        <!-- 角色权限 -->
        <el-tab-pane label="角色权限" name="matrix">
          <div class="matrix-toolbar">
            <el-select
              v-model="matrixRole"
              placeholder="选择角色"
              style="width: 280px"
              filterable
              @change="loadRoleMatrix"
            >
              <el-option
                v-for="r in roles"
                :key="r.value"
                :label="r.label"
                :value="r.value"
              >
                <div class="role-option">
                  <span class="role-option-label">{{ r.label }}</span>
                  <span v-if="r.description" class="role-option-desc">{{ r.description }}</span>
                </div>
              </el-option>
            </el-select>
            <el-button
              type="primary"
              :disabled="!matrixRole"
              :loading="savingMatrix"
              @click="saveRoleMatrix"
            >
              保存角色权限
            </el-button>
            <el-button :loading="loading" @click="loadCatalog">刷新角色</el-button>
          </div>

          <div class="matrix-legend">
            <span>查看 = 只读</span>
            <span class="dot">·</span>
            <span>管理 = 写操作</span>
            <span class="dot">·</span>
            <span>勾选模块级会覆盖下属页面</span>
          </div>

          <el-alert
            v-if="!loading && !roles.length"
            type="error"
            :closable="false"
            show-icon
            title="未能加载角色目录。请确认当前账号具备「权限管理」查看权限后点击刷新。"
            style="margin-bottom: 12px"
          />
          <el-empty v-else-if="!matrixRole" description="请先选择角色，配置其对应权限" />
          <template v-else>
            <div v-for="group in permissionGroups" :key="group.key" class="matrix-group">
              <div class="matrix-group-head">
                <div class="matrix-group-title">
                  <strong>{{ group.label }}</strong>
                  <el-text v-if="group.description" size="small" type="info">
                    {{ group.description }}
                  </el-text>
                </div>
                <div class="matrix-group-checks">
                  <el-checkbox
                    v-if="groupModulePerms(group)?.view"
                    :model-value="isPermChecked(groupModulePerms(group).view?.code)"
                    @change="(v) => togglePerm(groupModulePerms(group).view?.code, v)"
                  >
                    整模块查看
                  </el-checkbox>
                  <el-checkbox
                    v-if="groupModulePerms(group)?.manage"
                    :model-value="isPermChecked(groupModulePerms(group).manage?.code)"
                    @change="(v) => togglePerm(groupModulePerms(group).manage?.code, v)"
                  >
                    整模块管理
                  </el-checkbox>
                </div>
              </div>

              <el-table :data="matrixPageRows(group)" border stripe size="small">
                <el-table-column prop="label" label="页面" min-width="140" />
                <el-table-column prop="description" label="说明" min-width="160" show-overflow-tooltip>
                  <template #default="{ row }">{{ row.description || '—' }}</template>
                </el-table-column>
                <el-table-column label="查看" width="72" align="center">
                  <template #default="{ row }">
                    <el-checkbox
                      v-if="row.viewCode"
                      :model-value="isPermChecked(row.viewCode)"
                      @change="(v) => togglePerm(row.viewCode, v)"
                    />
                  </template>
                </el-table-column>
                <el-table-column label="管理" width="72" align="center">
                  <template #default="{ row }">
                    <el-checkbox
                      v-if="row.manageCode"
                      :model-value="isPermChecked(row.manageCode)"
                      @change="(v) => togglePerm(row.manageCode, v)"
                    />
                  </template>
                </el-table-column>
                <el-table-column label="权限码" min-width="220">
                  <template #default="{ row }">
                    <div class="code-stack">
                      <code v-if="row.viewCode" class="perm-code">{{ row.viewCode }}</code>
                      <code v-if="row.manageCode" class="perm-code">{{ row.manageCode }}</code>
                    </div>
                  </template>
                </el-table-column>
              </el-table>

              <div
                v-if="group.key === 'system' && settingsPageFor(group)"
                class="matrix-settings"
              >
                <div class="matrix-settings-head">
                  <strong>参数配置 · 分类权限</strong>
                  <el-button
                    link
                    type="primary"
                    size="small"
                    @click="settingsExpanded = !settingsExpanded"
                  >
                    {{ settingsExpanded ? '收起配置项' : '展开配置项' }}
                  </el-button>
                </div>
                <el-table
                  :data="matrixCategoryRows(group)"
                  border
                  stripe
                  size="small"
                  row-key="key"
                >
                  <el-table-column v-if="settingsExpanded" type="expand">
                    <template #default="{ row }">
                      <el-table :data="row.settings" size="small" border class="nested-setting-table">
                        <el-table-column prop="label" label="配置项" min-width="140" />
                        <el-table-column label="查看" width="72" align="center">
                          <template #default="{ row: s }">
                            <el-checkbox
                              v-if="s.viewCode"
                              :model-value="isPermChecked(s.viewCode)"
                              @change="(v) => togglePerm(s.viewCode, v)"
                            />
                          </template>
                        </el-table-column>
                        <el-table-column label="管理" width="72" align="center">
                          <template #default="{ row: s }">
                            <el-checkbox
                              v-if="s.manageCode"
                              :model-value="isPermChecked(s.manageCode)"
                              @change="(v) => togglePerm(s.manageCode, v)"
                            />
                          </template>
                        </el-table-column>
                        <el-table-column label="权限码" min-width="240">
                          <template #default="{ row: s }">
                            <div class="code-stack">
                              <code v-if="s.viewCode" class="perm-code">{{ s.viewCode }}</code>
                              <code v-if="s.manageCode" class="perm-code">{{ s.manageCode }}</code>
                            </div>
                          </template>
                        </el-table-column>
                      </el-table>
                    </template>
                  </el-table-column>
                  <el-table-column prop="label" label="分类" min-width="140" />
                  <el-table-column label="说明" min-width="160">
                    <template #default="{ row }">
                      {{ row.settings?.length ? `${row.settings.length} 个配置项` : '—' }}
                    </template>
                  </el-table-column>
                  <el-table-column label="查看" width="72" align="center">
                    <template #default="{ row }">
                      <el-checkbox
                        v-if="row.viewCode"
                        :model-value="isPermChecked(row.viewCode)"
                        @change="(v) => togglePerm(row.viewCode, v)"
                      />
                    </template>
                  </el-table-column>
                  <el-table-column label="管理" width="72" align="center">
                    <template #default="{ row }">
                      <el-checkbox
                        v-if="row.manageCode"
                        :model-value="isPermChecked(row.manageCode)"
                        @change="(v) => togglePerm(row.manageCode, v)"
                      />
                    </template>
                  </el-table-column>
                  <el-table-column label="权限码" min-width="220">
                    <template #default="{ row }">
                      <div class="code-stack">
                        <code v-if="row.viewCode" class="perm-code">{{ row.viewCode }}</code>
                        <code v-if="row.manageCode" class="perm-code">{{ row.manageCode }}</code>
                      </div>
                    </template>
                  </el-table-column>
                </el-table>
              </div>
            </div>
          </template>
        </el-tab-pane>

        <!-- 我的权限 -->
        <el-tab-pane label="我的权限" name="mine">
          <el-descriptions :column="2" border size="small" class="mine-desc">
            <el-descriptions-item label="当前用户">
              {{ authStore.user?.username }}
            </el-descriptions-item>
            <el-descriptions-item label="角色">
              <el-tag size="small">
                {{ authStore.user?.role_label || authStore.user?.role }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="超级管理员">
              {{ authStore.user?.is_superuser ? '是' : '否' }}
            </el-descriptions-item>
            <el-descriptions-item label="有效权限数">
              {{ myPermissions.length }}
            </el-descriptions-item>
          </el-descriptions>

          <div class="action-legend compact" v-if="resolvedActionMeta.length">
            <div
              v-for="meta in resolvedActionMeta"
              :key="meta.key"
              class="legend-item"
              :class="`legend-${meta.key}`"
            >
              <el-tag size="small" :type="meta.key === 'view' ? 'info' : 'warning'" effect="plain">
                {{ meta.label || (meta.key === 'view' ? '查看' : '管理') }}
              </el-tag>
              <span class="legend-desc">{{ meta.description }}</span>
            </div>
          </div>

          <PermissionPreview
            :role-catalog="roles"
            :role="authStore.user?.role"
            :permission-groups="permissionGroups"
            :permissions="myPermissions"
          />
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <el-dialog v-model="addPageVisible" title="新增管理页面" width="480px">
      <el-form label-width="90px">
        <el-form-item label="所属模块">
          <el-select v-model="addPageForm.group_key" style="width: 100%">
            <el-option
              v-for="g in permissionGroups"
              :key="g.key"
              :label="g.label"
              :value="g.key"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="页面标识">
          <el-input v-model="addPageForm.key" placeholder="如 report_list" />
        </el-form-item>
        <el-form-item label="页面名称">
          <el-input v-model="addPageForm.label" placeholder="如 报表列表" />
        </el-form-item>
        <el-form-item label="路由">
          <el-input v-model="addPageForm.route" placeholder="/custom/report" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addPageVisible = false">取消</el-button>
        <el-button type="primary" :loading="addingPage" @click="submitAddPage">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getRoleCatalog,
  getMyPermissions,
  getRolePermissions,
  updateRolePermissions,
  resetPermissionDefaults,
  updatePermissionGroup,
  updatePermissionPage,
  createPermissionPage,
  updatePermissionDefinition,
} from '@/api/accounts'
import { useAuthStore } from '@/stores/auth'
import PermissionPreview from '@/components/PermissionPreview.vue'
import { actionFromPermission } from '@/constants/permissions'

const FALLBACK_ACTION_META = [
  { key: 'view', label: '查看', description: '只读浏览' },
  { key: 'manage', label: '管理', description: '新增/修改/删除/执行' },
]

const authStore = useAuthStore()
const route = useRoute()
const router = useRouter()
const loading = ref(false)
const activeTab = ref('catalog')
const roles = ref([])
const permissionGroups = ref([])
const actionMeta = ref([])
const myPermissions = ref(authStore.user?.permissions || [])

const catalogSearch = ref('')
const catalogEditMode = ref(false)
const catalogCollapse = ref('')
const settingsExpanded = ref(false)

const groupEditForms = reactive({})
const pageEditForms = reactive({})
const permEditForms = reactive({})
const savingGroup = ref('')
const savingPage = ref('')

const matrixRole = ref('')
const matrixChecked = ref(new Set())
const savingMatrix = ref(false)

const addPageVisible = ref(false)
const addingPage = ref(false)
const addPageForm = reactive({ group_key: '', key: '', label: '', route: '' })

const resolvedActionMeta = computed(() => {
  const list = actionMeta.value
  if (Array.isArray(list) && list.length) {
    return list.map((m) => ({
      key: m.key,
      label: m.label || (m.key === 'view' ? '查看' : m.key === 'manage' ? '管理' : m.key),
      description:
        m.description
        || (m.key === 'view' ? '只读浏览' : m.key === 'manage' ? '新增/修改/删除/执行' : ''),
    }))
  }
  return FALLBACK_ACTION_META
})

const totalPageCount = computed(() =>
  permissionGroups.value.reduce((n, g) => n + (g.pages?.length || 0), 0)
)

const totalPermissionCount = computed(() => {
  let n = 0
  for (const g of permissionGroups.value) {
    n += g.permissions?.length || 0
    for (const p of g.pages || []) {
      n += p.permissions?.length || 0
      for (const c of p.categories || []) {
        n += c.permissions?.length || 0
        for (const s of c.settings || []) n += s.permissions?.length || 0
      }
    }
  }
  return n
})

const filteredGroups = computed(() => {
  const q = catalogSearch.value.trim().toLowerCase()
  if (!q) return permissionGroups.value
  return permissionGroups.value
    .map((g) => {
      const groupHit =
        (g.key || '').toLowerCase().includes(q)
        || (g.label || '').toLowerCase().includes(q)
        || (g.description || '').toLowerCase().includes(q)
      const pages = (g.pages || []).filter((page) => {
        if (groupHit) return true
        const pageHit =
          (page.key || '').toLowerCase().includes(q)
          || (page.label || '').toLowerCase().includes(q)
          || (page.route || '').toLowerCase().includes(q)
          || (page.description || '').toLowerCase().includes(q)
        const permHit = (page.permissions || []).some(
          (p) =>
            (p.code || '').toLowerCase().includes(q)
            || (p.label || '').toLowerCase().includes(q)
        )
        let settingHit = false
        for (const cat of page.categories || []) {
          if (
            (cat.key || '').toLowerCase().includes(q)
            || (cat.label || '').toLowerCase().includes(q)
            || (cat.permissions || []).some(
              (p) =>
                (p.code || '').toLowerCase().includes(q)
                || (p.label || '').toLowerCase().includes(q)
            )
          ) {
            settingHit = true
            break
          }
          for (const s of cat.settings || []) {
            if (
              (s.key || '').toLowerCase().includes(q)
              || (s.label || '').toLowerCase().includes(q)
              || (s.permissions || []).some(
                (p) =>
                  (p.code || '').toLowerCase().includes(q)
                  || (p.label || '').toLowerCase().includes(q)
              )
            ) {
              settingHit = true
              break
            }
          }
          if (settingHit) break
        }
        return pageHit || permHit || settingHit
      })
      if (!groupHit && !pages.length) return null
      return groupHit ? g : { ...g, pages }
    })
    .filter(Boolean)
})

function findActionPerm(perms, action) {
  return (perms || []).find(
    (p) => p.action === action || actionFromPermission(p.code) === action
  )
}

function initEditForms(groups) {
  for (const g of groups) {
    groupEditForms[g.key] = { label: g.label, description: g.description || '' }
    for (const page of g.pages || []) {
      const formKey = `${g.key}.${page.key}`
      pageEditForms[formKey] = { label: page.label, route: page.route || '' }
      for (const perm of page.permissions || []) {
        permEditForms[perm.code] = { label: perm.label, is_active: true }
      }
      for (const cat of page.categories || []) {
        for (const perm of cat.permissions || []) {
          permEditForms[perm.code] = { label: perm.label, is_active: true }
        }
        for (const setting of cat.settings || []) {
          for (const perm of setting.permissions || []) {
            permEditForms[perm.code] = { label: perm.label, is_active: true }
          }
        }
      }
    }
    for (const perm of g.permissions || []) {
      permEditForms[perm.code] = { label: perm.label, is_active: true }
    }
  }
}

function flattenPages(group) {
  return (group.pages || []).map((page) => {
    const perms = page.permissions || []
    return {
      formKey: `${group.key}.${page.key}`,
      pageKey: page.key,
      pageLabel: page.label,
      description: page.description || '',
      route: page.route,
      view: findActionPerm(perms, 'view'),
      manage: findActionPerm(perms, 'manage'),
    }
  })
}

function settingCategoryRows(group) {
  const settingsPage = (group.pages || []).find((p) => p.key === 'settings')
  return (settingsPage?.categories || []).map((cat) => {
    const view = findActionPerm(cat.permissions, 'view')
    const manage = findActionPerm(cat.permissions, 'manage')
    return {
      key: cat.key,
      label: cat.label,
      viewCode: view?.code,
      manageCode: manage?.code,
      viewLabel: view?.label,
      manageLabel: manage?.label,
      settings: (cat.settings || []).map((s) => {
        const sv = findActionPerm(s.permissions, 'view')
        const sm = findActionPerm(s.permissions, 'manage')
        return {
          categoryLabel: cat.label,
          settingLabel: s.label,
          key: s.key,
          viewCode: sv?.code,
          manageCode: sm?.code,
          viewLabel: sv?.label,
          manageLabel: sm?.label,
        }
      }),
    }
  })
}

function settingsPageFor(group) {
  return (group.pages || []).find((p) => p.key === 'settings')
}

function groupModulePerms(group) {
  const view = findActionPerm(group.permissions, 'view')
  const manage = findActionPerm(group.permissions, 'manage')
  return view || manage ? { view, manage } : null
}

function matrixPageRows(group) {
  return (group.pages || []).map((page) => {
    const view = findActionPerm(page.permissions, 'view')
    const manage = findActionPerm(page.permissions, 'manage')
    return {
      key: page.key,
      label: page.label,
      description: page.description || '',
      route: page.route,
      viewCode: view?.code,
      manageCode: manage?.code,
    }
  })
}

function matrixCategoryRows(group) {
  return settingCategoryRows(group).map((cat) => ({
    key: cat.key,
    label: cat.label,
    viewCode: cat.viewCode,
    manageCode: cat.manageCode,
    settings: cat.settings.map((s) => ({
      key: s.key,
      label: s.settingLabel,
      viewCode: s.viewCode,
      manageCode: s.manageCode,
    })),
  }))
}

function isPermChecked(code) {
  if (!code) return false
  return matrixChecked.value.has(code)
}

function togglePerm(code, checked) {
  if (!code) return
  const next = new Set(matrixChecked.value)
  if (checked) next.add(code)
  else next.delete(code)
  matrixChecked.value = next
}

async function saveGroup(group) {
  const form = groupEditForms[group.key]
  savingGroup.value = group.key
  try {
    await updatePermissionGroup(group.key, {
      label: form.label,
      description: form.description,
    })
    ElMessage.success('模块信息已保存')
    await loadCatalog()
  } catch (err) {
    ElMessage.error(err?.response?.data?.message || '保存失败')
  } finally {
    savingGroup.value = ''
  }
}

async function savePage(groupKey, row) {
  const form = pageEditForms[row.formKey]
  savingPage.value = row.formKey
  try {
    const [, pageKey] = row.formKey.split('.')
    await updatePermissionPage(groupKey, pageKey, {
      label: form.label,
      route: form.route,
    })
    const tasks = []
    if (row.view) {
      tasks.push(
        updatePermissionDefinition(row.view.code, {
          label: permEditForms[row.view.code].label,
        })
      )
    }
    if (row.manage) {
      tasks.push(
        updatePermissionDefinition(row.manage.code, {
          label: permEditForms[row.manage.code].label,
        })
      )
    }
    await Promise.all(tasks)
    ElMessage.success('页面权限已保存')
    await loadCatalog()
  } catch (err) {
    ElMessage.error(err?.response?.data?.message || '保存失败')
  } finally {
    savingPage.value = ''
  }
}

async function saveSettingPerms(row) {
  try {
    const tasks = []
    if (row.viewCode) {
      tasks.push(
        updatePermissionDefinition(row.viewCode, {
          label: permEditForms[row.viewCode].label,
        })
      )
    }
    if (row.manageCode) {
      tasks.push(
        updatePermissionDefinition(row.manageCode, {
          label: permEditForms[row.manageCode].label,
        })
      )
    }
    await Promise.all(tasks)
    ElMessage.success('配置项权限名称已保存')
  } catch (err) {
    ElMessage.error(err?.response?.data?.message || '保存失败')
  }
}

async function saveCategoryPerms(row) {
  try {
    const tasks = []
    if (row.viewCode) {
      tasks.push(
        updatePermissionDefinition(row.viewCode, {
          label: permEditForms[row.viewCode].label,
        })
      )
    }
    if (row.manageCode) {
      tasks.push(
        updatePermissionDefinition(row.manageCode, {
          label: permEditForms[row.manageCode].label,
        })
      )
    }
    await Promise.all(tasks)
    ElMessage.success('分类权限名称已保存')
  } catch (err) {
    ElMessage.error(err?.response?.data?.message || '保存失败')
  }
}

async function loadRoleMatrix() {
  if (!matrixRole.value) return
  try {
    const res = await getRolePermissions(matrixRole.value)
    const data = res?.data || res
    matrixChecked.value = new Set(data.permissions || [])
  } catch {
    matrixChecked.value = new Set()
  }
}

async function saveRoleMatrix() {
  if (!matrixRole.value) return
  savingMatrix.value = true
  try {
    await updateRolePermissions(matrixRole.value, [...matrixChecked.value])
    ElMessage.success('角色权限已更新')
    await loadCatalog()
    await authStore.refreshPermissions()
  } catch (err) {
    ElMessage.error(err?.response?.data?.message || '保存失败')
  } finally {
    savingMatrix.value = false
  }
}

function openAddPageDialog() {
  addPageForm.group_key = permissionGroups.value[0]?.key || ''
  addPageForm.key = ''
  addPageForm.label = ''
  addPageForm.route = ''
  addPageVisible.value = true
}

async function submitAddPage() {
  if (!addPageForm.group_key || !addPageForm.key || !addPageForm.label) {
    ElMessage.warning('请填写模块、页面标识和名称')
    return
  }
  addingPage.value = true
  try {
    await createPermissionPage({ ...addPageForm })
    ElMessage.success('页面已创建')
    addPageVisible.value = false
    await loadCatalog()
  } catch (err) {
    ElMessage.error(err?.response?.data?.message || '创建失败')
  } finally {
    addingPage.value = false
  }
}

async function handleResetDefaults() {
  await ElMessageBox.confirm(
    '将恢复内置权限目录与各角色默认授权，自定义页面/权限将被清除。是否继续？',
    '恢复默认',
    { type: 'warning' }
  )
  try {
    await resetPermissionDefaults()
    ElMessage.success('已恢复默认权限配置')
    matrixRole.value = ''
    matrixChecked.value = new Set()
    await loadCatalog()
    await authStore.refreshPermissions()
  } catch (err) {
    ElMessage.error(err?.response?.data?.message || '操作失败')
  }
}

async function loadCatalog() {
  loading.value = true
  try {
    const res = await getRoleCatalog()
    const data = res?.data || res
    roles.value = data.roles || []
    permissionGroups.value = data.permission_groups || []
    actionMeta.value = data.action_meta || []
    initEditForms(permissionGroups.value)
    if (!roles.value.length) {
      ElMessage.warning('角色目录为空，请检查权限或恢复默认配置')
    }
  } catch (err) {
    roles.value = []
    ElMessage.error(err?.response?.data?.message || '加载角色目录失败')
  } finally {
    loading.value = false
  }
}

async function loadMyPermissions() {
  try {
    const res = await getMyPermissions()
    const data = res?.data || res
    myPermissions.value = data.permissions || authStore.user?.permissions || []
  } catch {
    myPermissions.value = authStore.user?.permissions || []
  }
}

function applyRouteQuery() {
  const tab = String(route.query.tab || '')
  const role = String(route.query.role || '')
  if (tab === 'matrix' || tab === 'catalog' || tab === 'mine') {
    activeTab.value = tab
  }
  if (role && roles.value.some((r) => r.value === role)) {
    activeTab.value = 'matrix'
    if (matrixRole.value !== role) {
      matrixRole.value = role
      loadRoleMatrix()
    }
  }
}

function syncMatrixQuery() {
  if (activeTab.value !== 'matrix') return
  const nextTab = 'matrix'
  const nextRole = matrixRole.value || undefined
  if (route.query.tab === nextTab && (route.query.role || undefined) === nextRole) return
  const nextQuery = { ...route.query, tab: nextTab }
  if (nextRole) nextQuery.role = nextRole
  else delete nextQuery.role
  router.replace({ query: nextQuery }).catch(() => {})
}

watch(activeTab, (tab) => {
  if (tab === 'matrix' && matrixRole.value) syncMatrixQuery()
})

watch(matrixRole, () => {
  if (activeTab.value === 'matrix') syncMatrixQuery()
})

watch(
  () => [route.query.tab, route.query.role],
  () => {
    if (roles.value.length) applyRouteQuery()
  }
)

onMounted(async () => {
  await Promise.all([loadCatalog(), loadMyPermissions()])
  applyRouteQuery()
})
</script>

<style scoped>
.page-container {
  min-height: 100%;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 16px;
  gap: 12px;
}

.header-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.page-title {
  font-size: 18px;
  font-weight: 700;
  color: #1a1a2e;
  margin: 0 0 4px;
}

.page-subtitle {
  margin: 0;
  font-size: 13px;
  color: #909399;
  line-height: 1.5;
  max-width: 640px;
}

.action-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 20px;
  margin-bottom: 14px;
  padding: 12px 14px;
  background: #fafbfc;
  border: 1px solid #ebeef5;
  border-radius: 8px;
}

.action-legend.compact {
  margin: 0 0 16px;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.legend-desc {
  font-size: 13px;
  color: #606266;
  line-height: 1.4;
}

.stat-row {
  margin-bottom: 12px;
}

.stat-card {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.stat-num {
  font-size: 22px;
  font-weight: 700;
  color: #409eff;
}

.stat-lbl {
  font-size: 12px;
  color: #909399;
}

.tab-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
  gap: 12px;
  flex-wrap: wrap;
}

.toolbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.collapse-title {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding-right: 12px;
}

.count-tag {
  margin-left: 4px;
}

.group-edit-row {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
  align-items: center;
  flex-wrap: wrap;
}

.group-readonly-meta {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
  font-size: 13px;
  color: #606266;
}

.meta-label {
  color: #909399;
  flex-shrink: 0;
}

.page-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 8px;
}

.page-row {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  justify-content: space-between;
  padding: 12px 14px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  background: #fff;
}

.page-row-main {
  flex: 1;
  min-width: 0;
}

.page-row-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 4px;
}

.page-label {
  font-size: 14px;
  color: #303133;
}

.route-code,
.perm-code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  color: #606266;
  background: #f5f7fa;
  padding: 1px 6px;
  border-radius: 4px;
}

.page-desc {
  margin: 0 0 8px;
  font-size: 12px;
  color: #909399;
  line-height: 1.4;
}

.perm-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.perm-chips.compact {
  margin-top: 4px;
}

.perm-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border-radius: 6px;
  border: 1px solid #e4e7ed;
  background: #fafafa;
}

.chip-view {
  border-color: #d9ecff;
  background: #f0f7ff;
}

.chip-manage {
  border-color: #faecd8;
  background: #fff8f0;
}

.perm-label {
  font-size: 12px;
  color: #303133;
}

.perm-label.muted {
  margin-top: 2px;
  color: #909399;
}

.page-row-actions {
  flex-shrink: 0;
  padding-top: 2px;
}

.setting-section {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px dashed #e4e7ed;
}

.setting-section h4 {
  margin: 0 0 4px;
  font-size: 14px;
  color: #303133;
}

.setting-hint {
  display: block;
  margin-bottom: 10px;
}

.setting-table {
  margin-top: 4px;
}

.setting-keys {
  padding: 8px 12px 12px 36px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.setting-key-row {
  padding: 8px 10px;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  background: #fafbfc;
}

.setting-key-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}

.matrix-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}

.role-option {
  display: flex;
  flex-direction: column;
  line-height: 1.3;
  padding: 2px 0;
}

.role-option-label {
  font-size: 13px;
  color: #303133;
}

.role-option-desc {
  font-size: 11px;
  color: #909399;
  white-space: normal;
}

.matrix-legend {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 6px;
  margin-bottom: 16px;
  font-size: 12px;
  color: #909399;
}

.matrix-legend .dot {
  color: #c0c4cc;
}

.matrix-group {
  margin-bottom: 22px;
}

.matrix-group-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}

.matrix-group-title {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.matrix-group-checks {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.code-stack {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.matrix-settings {
  margin-top: 12px;
  padding: 12px;
  background: #fafbfc;
  border: 1px solid #ebeef5;
  border-radius: 8px;
}

.matrix-settings-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
  gap: 8px;
}

.nested-setting-table {
  margin: 4px 8px 8px 24px;
}

.mine-desc {
  margin-bottom: 16px;
}
</style>
