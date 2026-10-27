<template>
  <el-container class="main-layout">
    <!-- 侧边栏 -->
    <el-aside :width="isCollapsed ? '64px' : '220px'" class="sidebar">
      <div class="sidebar-logo">
        <el-icon size="24" color="#409EFF"><Monitor /></el-icon>
        <span v-if="!isCollapsed" class="logo-text">ISP Manager</span>
      </div>
      <el-scrollbar>
        <el-menu
          :default-active="activeMenu"
          :collapse="isCollapsed"
          :collapse-transition="false"
          background-color="transparent"
          text-color="rgba(255,255,255,0.7)"
          active-text-color="#fff"
          router
          class="sidebar-menu"
        >
          <el-menu-item index="/dashboard">
            <el-icon><DataBoard /></el-icon>
            <template #title>数据概览</template>
          </el-menu-item>

          <el-sub-menu v-if="showCustomerMenu" index="/customer">
            <template #title>
              <el-icon><UserFilled /></el-icon>
              <span>客户管理</span>
            </template>
            <el-menu-item v-if="canPage('customer', 'list')" index="/customer/list">
              <el-icon><User /></el-icon>
              <template #title>客户列表</template>
            </el-menu-item>
            <el-menu-item v-if="canPage('customer', 'channel')" index="/customer/channels">
              <el-icon><Money /></el-icon>
              <template #title>渠道推广/佣金</template>
            </el-menu-item>
            <el-menu-item v-if="canPage('customer', 'contract')" index="/customer/contracts">
              <el-icon><Document /></el-icon>
              <template #title>合同管理</template>
            </el-menu-item>
            <el-menu-item v-if="canPage('billing', 'list')" index="/customer/billings">
              <el-icon><Money /></el-icon>
              <template #title>账单管理</template>
            </el-menu-item>
          </el-sub-menu>

          <el-sub-menu v-if="showResourceMenu" index="/resource">
            <template #title>
              <el-icon><Monitor /></el-icon>
              <span>资源管理</span>
            </template>
            <el-menu-item v-if="canPage('resource', 'pop')" index="/resource/pop">
              <el-icon><Location /></el-icon>
              <template #title>POP节点</template>
            </el-menu-item>
            <el-menu-item v-if="canPage('resource', 'ip')" index="/resource/ip">
              <el-icon><Connection /></el-icon>
              <template #title>IP管理</template>
            </el-menu-item>
            <el-menu-item v-if="canPage('resource', 'ip')" index="/resource/tunnel-ip">
              <el-icon><Share /></el-icon>
              <template #title>隧道IP段</template>
            </el-menu-item>
            <el-menu-item v-if="canPage('resource', 'vps')" index="/resource/vps">
              <el-icon><Cpu /></el-icon>
              <template #title>VPS管理</template>
            </el-menu-item>
            <el-menu-item v-if="canPage('resource', 'edge')" index="/resource/edge">
              <el-icon><Share /></el-icon>
              <template #title>边缘加速</template>
            </el-menu-item>
            <el-menu-item v-if="canPage('resource', 'bandwidth')" index="/resource/bandwidth">
              <el-icon><Histogram /></el-icon>
              <template #title>带宽管理</template>
            </el-menu-item>
          </el-sub-menu>

          <el-sub-menu v-if="showOperationMenu" index="/operation">
            <template #title>
              <el-icon><Odometer /></el-icon>
              <span>运维管理</span>
            </template>
            <el-sub-menu v-if="showMonitoringMenu" index="/operation/monitoring">
              <template #title>
                <el-icon><Monitor /></el-icon>
                <span>监控管理</span>
              </template>
              <el-menu-item v-if="canPage('operation', 'monitor_hosts')" index="/operation/monitoring/hosts">
                <el-icon><Connection /></el-icon>
                <template #title>监控主机</template>
              </el-menu-item>
              <el-menu-item v-if="canPage('operation', 'pop_quality')" index="/operation/monitoring/pop-quality">
                <el-icon><TrendCharts /></el-icon>
                <template #title>POP品质监控</template>
              </el-menu-item>
              <el-menu-item v-if="canPage('operation', 'traffic')" index="/operation/monitoring/traffic">
                <el-icon><TrendCharts /></el-icon>
                <template #title>流量计费</template>
              </el-menu-item>
              <el-menu-item v-if="canManagePageAction('operation', 'traffic_profile')" index="/operation/monitoring/profiles">
                <el-icon><SetUp /></el-icon>
                <template #title>计费规则</template>
              </el-menu-item>
              <el-menu-item v-if="canManagePageAction('operation', 'zabbix')" index="/operation/monitoring/zabbix">
                <el-icon><DataAnalysis /></el-icon>
                <template #title>Zabbix 服务器</template>
              </el-menu-item>
            </el-sub-menu>
            <el-sub-menu v-if="showToolsMenu" index="/operation/tools">
              <template #title>
                <el-icon><Tools /></el-icon>
                <span>运维工具</span>
              </template>
              <el-menu-item v-if="canManagePageAction('operation', 'webssh')" index="/operation/tools/webssh">
                <el-icon><Monitor /></el-icon>
                <template #title>WebSSH</template>
              </el-menu-item>
              <el-menu-item v-if="canManagePageAction('operation', 'template')" index="/operation/tools/templates">
                <el-icon><DocumentCopy /></el-icon>
                <template #title>命令模版</template>
              </el-menu-item>
            </el-sub-menu>
          </el-sub-menu>

          <el-sub-menu v-if="showServiceMenu" index="/service">
            <template #title>
              <el-icon><Setting /></el-icon>
              <span>服务管理</span>
            </template>
            <el-menu-item v-if="canPage('service', 'overview')" index="/service/overview">
              <el-icon><DataAnalysis /></el-icon>
              <template #title>服务全景</template>
            </el-menu-item>
            <el-menu-item v-if="canPage('service', 'order')" index="/service/orders">
              <el-icon><List /></el-icon>
              <template #title>服务工单</template>
            </el-menu-item>
            <el-menu-item v-if="canPage('service', 'inquiry')" index="/service/inquiries">
              <el-icon><ChatDotRound /></el-icon>
              <template #title>官网咨询</template>
            </el-menu-item>
            <el-menu-item v-if="canPage('service', 'ticket')" index="/service/tickets">
              <el-icon><Ticket /></el-icon>
              <template #title>技术工单</template>
            </el-menu-item>
            <el-menu-item v-if="canPage('service', 'selfservice')" index="/service/self-service">
              <el-icon><RefreshRight /></el-icon>
              <template #title>自助任务</template>
            </el-menu-item>
            <el-menu-item v-if="canPage('service', 'instance')" index="/service/instances">
              <el-icon><Grid /></el-icon>
              <template #title>服务实例</template>
            </el-menu-item>
            <el-menu-item v-if="canPage('service', 'change')" index="/service/changes">
              <el-icon><EditPen /></el-icon>
              <template #title>变更记录</template>
            </el-menu-item>
          </el-sub-menu>

          <el-sub-menu v-if="showProductMenu" index="/product">
            <template #title>
              <el-icon><Goods /></el-icon>
              <span>产品管理</span>
            </template>
            <el-menu-item v-if="canPage('product', 'product')" index="/product/products">
              <el-icon><Sell /></el-icon>
              <template #title>产品列表</template>
            </el-menu-item>
            <el-menu-item v-if="canPage('product', 'template') && authStore.user?.role !== 'supplier'" index="/product/templates">
              <el-icon><Files /></el-icon>
              <template #title>产品模版</template>
            </el-menu-item>
            <el-menu-item v-if="canPage('product', 'product')" index="/product/quick-countries">
              <el-icon><Location /></el-icon>
              <template #title>快速查找国家</template>
            </el-menu-item>
          </el-sub-menu>

          <el-sub-menu v-if="showSupplierMenu" index="/supplier">
            <template #title>
              <el-icon><OfficeBuilding /></el-icon>
              <span>供应商管理</span>
            </template>
            <el-menu-item v-if="canPage('supplier', 'list')" index="/supplier/list">
              <el-icon><Shop /></el-icon>
              <template #title>供应商列表</template>
            </el-menu-item>
            <el-menu-item v-if="canPage('supplier', 'resource')" index="/supplier/resources">
              <el-icon><Box /></el-icon>
              <template #title>供应商资源</template>
            </el-menu-item>
            <el-menu-item v-if="canPage('supplier', 'billing')" index="/supplier/bills">
              <el-icon><Money /></el-icon>
              <template #title>账单结算</template>
            </el-menu-item>
          </el-sub-menu>

          <el-sub-menu v-if="showSystemMenu" index="/system">
            <template #title>
              <el-icon><Tools /></el-icon>
              <span>系统设置</span>
            </template>
            <el-menu-item v-if="canPage('system', 'settings')" index="/system/settings">
              <el-icon><Setting /></el-icon>
              <template #title>参数配置</template>
            </el-menu-item>
            <el-menu-item v-if="canPage('system', 'notify')" index="/system/notifications">
              <el-icon><Bell /></el-icon>
              <template #title>通知/IM对接</template>
            </el-menu-item>
            <el-menu-item v-if="canPage('system', 'log')" index="/system/logs">
              <el-icon><Notebook /></el-icon>
              <template #title>操作日志</template>
            </el-menu-item>
            <el-menu-item v-if="canPage('system', 'maintenance')" index="/system/maintenance">
              <el-icon><Upload /></el-icon>
              <template #title>系统维护</template>
            </el-menu-item>
            <el-menu-item v-if="canPage('system', 'os_template')" index="/system/os-templates">
              <el-icon><Files /></el-icon>
              <template #title>系统模版</template>
            </el-menu-item>
            <el-menu-item v-if="canManagePageAction('accounts', 'role')" index="/system/roles">
              <el-icon><Lock /></el-icon>
              <template #title>权限管理</template>
            </el-menu-item>
            <el-menu-item v-if="canManagePageAction('accounts', 'user')" index="/system/users">
              <el-icon><UserFilled /></el-icon>
              <template #title>用户管理</template>
            </el-menu-item>
          </el-sub-menu>
        </el-menu>
      </el-scrollbar>

      <div v-if="systemVersionDisplay && !isCollapsed" class="sidebar-version">
        {{ systemVersionDisplay }}
      </div>
      <div class="sidebar-collapse" @click="isCollapsed = !isCollapsed">
        <el-icon>
          <component :is="isCollapsed ? 'DArrowRight' : 'DArrowLeft'" />
        </el-icon>
      </div>
    </el-aside>

    <el-container class="main-container">
      <!-- 顶部导航 -->
      <el-header class="header">
        <div class="header-left">
          <el-breadcrumb separator="/">
            <el-breadcrumb-item
              v-for="item in breadcrumbs"
              :key="item.path"
              :to="item.path"
            >
              {{ item.title }}
            </el-breadcrumb-item>
          </el-breadcrumb>
        </div>
        <div class="header-right">
          <el-tooltip content="刷新页面" placement="bottom">
            <el-button text circle @click="refreshPage">
              <el-icon><Refresh /></el-icon>
            </el-button>
          </el-tooltip>
          <el-divider direction="vertical" />
          <el-dropdown @command="handleCommand">
            <div class="user-info">
              <el-avatar :size="32" :style="{ background: 'linear-gradient(135deg,#409EFF,#007bff)' }">
                {{ userInitial }}
              </el-avatar>
              <span class="username">{{ authStore.user?.username }}</span>
              <el-icon><ArrowDown /></el-icon>
            </div>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="profile">
                  <el-icon><User /></el-icon> 个人信息
                </el-dropdown-item>
                <el-dropdown-item command="password">
                  <el-icon><Lock /></el-icon> 修改密码
                </el-dropdown-item>
                <el-dropdown-item divided command="logout" style="color: #f56c6c;">
                  <el-icon><SwitchButton /></el-icon> 退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <!-- 内容区域 -->
      <el-main class="main-content">
        <router-view v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </el-main>
    </el-container>
  </el-container>

  <!-- 修改密码对话框 -->
  <el-dialog v-model="pwdDialogVisible" title="修改密码" width="400px">
    <el-form ref="pwdFormRef" :model="pwdForm" :rules="pwdRules" label-width="80px">
      <el-form-item label="旧密码" prop="old_password">
        <el-input v-model="pwdForm.old_password" type="password" show-password />
      </el-form-item>
      <el-form-item label="新密码" prop="new_password">
        <el-input v-model="pwdForm.new_password" type="password" show-password />
      </el-form-item>
      <el-form-item label="确认密码" prop="confirm_password">
        <el-input v-model="pwdForm.confirm_password" type="password" show-password />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="pwdDialogVisible = false">取消</el-button>
      <el-button type="primary" @click="handleChangePwd">确认修改</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { usePermission } from '@/composables/usePermission'
import { changePassword } from '@/api/auth'
import { getSystemVersion } from '@/api/system'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const { canPage, canManagePageAction } = usePermission()
const systemVersionDisplay = ref('')

const isCollapsed = ref(false)
const pwdDialogVisible = ref(false)
const pwdFormRef = ref(null)
const pwdForm = ref({ old_password: '', new_password: '', confirm_password: '' })

const activeMenu = computed(() => route.path)

const userInitial = computed(() => {
  const name = authStore.user?.username || 'U'
  return name.charAt(0).toUpperCase()
})

const showCustomerMenu = computed(() =>
  canPage('customer', 'list') || canPage('customer', 'channel') ||
  canPage('customer', 'contract') || canPage('billing', 'list')
)
const showResourceMenu = computed(() =>
  ['pop', 'ip', 'vps', 'edge', 'bandwidth'].some((p) => canPage('resource', p))
)
const showMonitoringMenu = computed(() =>
  canPage('operation', 'monitor_hosts') ||
  canPage('operation', 'traffic') ||
  canManagePageAction('operation', 'traffic_profile') ||
  canManagePageAction('operation', 'zabbix')
)
const showToolsMenu = computed(() =>
  canManagePageAction('operation', 'webssh') || canManagePageAction('operation', 'template')
)
const showOperationMenu = computed(() => showMonitoringMenu.value || showToolsMenu.value)
const showServiceMenu = computed(() =>
  canPage('service', 'overview') ||
  canPage('service', 'order') ||
  canPage('service', 'inquiry') ||
  canPage('service', 'ticket') ||
  canPage('service', 'selfservice') ||
  canPage('service', 'instance') ||
  canManagePageAction('service', 'plan') ||
  canPage('service', 'change')
)
const showProductMenu = computed(() =>
  ['product', 'template'].some((p) => canPage('product', p))
)
const showSupplierMenu = computed(() =>
  ['list', 'resource', 'billing'].some((p) => canPage('supplier', p))
)
const showSystemMenu = computed(() =>
  canPage('system', 'settings') ||
  canPage('system', 'notify') ||
  canPage('system', 'log') ||
  canPage('system', 'maintenance') ||
  canPage('system', 'os_template') ||
  canManagePageAction('accounts', 'role') ||
  canManagePageAction('accounts', 'user')
)

const breadcrumbs = computed(() => {
  const matched = route.matched.filter(r => r.meta.title)
  return matched.map(r => ({ title: r.meta.title, path: r.path }))
})

const pwdRules = {
  old_password: [{ required: true, message: '请输入旧密码' }],
  new_password: [{ required: true, message: '请输入新密码' }, { min: 6, message: '密码至少6位' }],
  confirm_password: [
    { required: true, message: '请确认新密码' },
    {
      validator: (rule, value, callback) => {
        if (value !== pwdForm.value.new_password) {
          callback(new Error('两次密码不一致'))
        } else {
          callback()
        }
      }
    }
  ]
}

function refreshPage() {
  router.go(0)
}

onMounted(async () => {
  if (!canPage('system', 'settings') && !canPage('system', 'maintenance')) return
  try {
    const res = await getSystemVersion()
    systemVersionDisplay.value = res?.display || ''
  } catch {
    systemVersionDisplay.value = ''
  }
})

async function handleCommand(cmd) {
  if (cmd === 'logout') {
    await ElMessageBox.confirm('确定要退出登录吗？', '提示', { type: 'warning' })
    authStore.logout()
    ElMessage.success('已成功退出登录')
  } else if (cmd === 'password') {
    pwdDialogVisible.value = true
  }
}

async function handleChangePwd() {
  const valid = await pwdFormRef.value.validate().catch(() => false)
  if (!valid) return
  try {
    await changePassword(pwdForm.value)
    ElMessage.success('密码修改成功，请重新登录')
    pwdDialogVisible.value = false
    authStore.logout()
  } catch (err) {
    ElMessage.error(err?.response?.data?.message || '修改失败')
  }
}
</script>

<style scoped>
.main-layout {
  height: 100vh;
  background: #f0f2f5;
}

.sidebar {
  background: linear-gradient(180deg, #0d1b3e 0%, #1a2a5e 100%);
  transition: width 0.25s ease;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 2px 0 8px rgba(0, 0, 0, 0.2);
}

.sidebar-logo {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  flex-shrink: 0;
  padding: 0 16px;
}

.logo-text {
  color: #fff;
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 1px;
  white-space: nowrap;
}

.sidebar-menu {
  border: none;
  flex: 1;
}

:deep(.el-menu-item),
:deep(.el-sub-menu__title) {
  height: 46px;
  line-height: 46px;
  margin: 2px 8px;
  border-radius: 8px;
  color: rgba(255, 255, 255, 0.6) !important;
}

:deep(.el-menu-item:hover),
:deep(.el-sub-menu__title:hover) {
  background: rgba(64, 158, 255, 0.15) !important;
  color: rgba(255, 255, 255, 0.9) !important;
}

:deep(.el-menu-item.is-active) {
  background: linear-gradient(135deg, rgba(64, 158, 255, 0.3), rgba(64, 158, 255, 0.15)) !important;
  color: #fff !important;
}

:deep(.el-sub-menu .el-menu) {
  background: rgba(0, 0, 0, 0.2) !important;
}

:deep(.el-sub-menu .el-menu .el-menu-item) {
  padding-left: 40px !important;
  font-size: 13px;
}

:deep(.el-sub-menu .el-sub-menu .el-menu .el-menu-item) {
  padding-left: 52px !important;
  font-size: 12px;
}

.sidebar-version {
  flex-shrink: 0;
  text-align: center;
  font-size: 12px;
  letter-spacing: 0.4px;
  color: rgba(255, 255, 255, 0.38);
  padding: 8px 12px 4px;
}

.sidebar-collapse {
  height: 46px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(255, 255, 255, 0.4);
  cursor: pointer;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  flex-shrink: 0;
  transition: color 0.2s;
}

.sidebar-collapse:hover {
  color: rgba(255, 255, 255, 0.8);
  background: rgba(255, 255, 255, 0.05);
}

.main-container {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.header {
  background: #fff;
  border-bottom: 1px solid #eee;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  height: 60px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
  flex-shrink: 0;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 8px;
  transition: background 0.2s;
}

.user-info:hover {
  background: #f0f2f5;
}

.username {
  font-size: 14px;
  color: #333;
  font-weight: 500;
}

.main-content {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
