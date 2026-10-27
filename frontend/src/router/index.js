import { createRouter, createWebHistory } from 'vue-router'
import { canAccessPermission } from '@/constants/permissions'
import { expireSession, isIdleExpired } from '@/utils/idleSession'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue'),
    meta: { title: '登录', requiresAuth: false }
  },
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    meta: { requiresAuth: true },
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/Dashboard.vue'),
        meta: { title: '数据概览', icon: 'DataBoard' }
      },
      {
        path: 'customer',
        name: 'CustomerManage',
        redirect: '/customer/list',
        meta: { title: '客户管理', icon: 'UserFilled', permission: 'customer.list.view' },
        children: [
          {
            path: 'list',
            name: 'CustomerList',
            component: () => import('@/views/customer/CustomerList.vue'),
            meta: { title: '客户列表', permission: 'customer.list.view' }
          },
          {
            path: 'channels',
            name: 'ChannelCommissionManage',
            component: () => import('@/views/customer/ChannelCommissionManage.vue'),
            meta: { title: '渠道推广/佣金', permission: 'customer.channel.view' }
          },
          {
            path: ':id',
            name: 'CustomerDetail',
            component: () => import('@/views/customer/CustomerDetail.vue'),
            meta: { title: '客户详情', hidden: true, permission: 'customer.detail.view' }
          },
          {
            path: 'contracts',
            name: 'ContractList',
            component: () => import('@/views/customer/ContractList.vue'),
            meta: { title: '合同管理', permission: 'customer.contract.view' }
          },
          {
            path: 'billings',
            name: 'BillingList',
            component: () => import('@/views/customer/BillingList.vue'),
            meta: { title: '账单管理', permission: 'billing.list.view' }
          },
          {
            path: 'billings/item-detail',
            name: 'BillingItemDetail',
            component: () => import('@/views/customer/BillingItemDetail.vue'),
            meta: { title: '计费明细', hidden: true, permission: 'billing.list.view' }
          }
        ]
      },
      {
        path: 'resource',
        name: 'ResourceManage',
        redirect: '/resource/pop',
        meta: { title: '资源管理', icon: 'Monitor', permission: 'resource.pop.view' },
        children: [
          {
            path: 'pop',
            name: 'PopPointList',
            component: () => import('@/views/resource/PopPointList.vue'),
            meta: { title: 'POP节点', permission: 'resource.pop.view' }
          },
          {
            path: 'pop/:id',
            name: 'PopPointDetail',
            component: () => import('@/views/resource/PopPointDetail.vue'),
            meta: { title: '节点详情', hidden: true, permission: 'resource.pop.view' }
          },
          {
            path: 'pop/:id/deploy',
            name: 'PopPointDeploy',
            component: () => import('@/views/resource/PopPointDeploy.vue'),
            meta: { title: 'POP节点部署', hidden: true, permission: 'resource.pop_deploy.manage' }
          },
          {
            path: 'ip',
            name: 'IPManage',
            component: () => import('@/views/resource/IPManage.vue'),
            meta: { title: 'IP管理', permission: 'resource.ip.view' }
          },
          {
            path: 'ip/blocks/:id',
            name: 'IPBlockDetail',
            component: () => import('@/views/resource/IPManage.vue'),
            meta: { title: 'IP块详情', hidden: true, permission: 'resource.ip.view' }
          },
          {
            path: 'ip/addresses/:id/logs',
            name: 'IPAddressLogs',
            component: () => import('@/views/resource/IPManage.vue'),
            meta: { title: 'IP操作日志', hidden: true, permission: 'resource.ip.view' }
          },
          {
            path: 'tunnel-ip',
            name: 'TunnelIPPoolList',
            component: () => import('@/views/resource/TunnelIPPoolList.vue'),
            meta: { title: '隧道IP段', permission: 'resource.ip.view' }
          },
          {
            path: 'tunnel-ip/:id/usage',
            name: 'TunnelIPPoolUsage',
            component: () => import('@/views/resource/TunnelIPPoolUsage.vue'),
            meta: { title: '用量详情', hidden: true, permission: 'resource.ip.view' }
          },
          {
            path: 'vps',
            name: 'VPSList',
            component: () => import('@/views/resource/VPSList.vue'),
            meta: { title: 'VPS管理', permission: 'resource.vps.view' }
          },
          {
            path: 'vps/:id',
            name: 'VPSDetail',
            component: () => import('@/views/resource/VPSDetail.vue'),
            meta: { title: 'VPS详情', hidden: true, permission: 'resource.vps.view' }
          },
          {
            path: 'vps/:id/console',
            name: 'VPSVncConsole',
            component: () => import('@/views/resource/VPSVncConsole.vue'),
            meta: { title: 'VPS 控制台', hidden: true, permission: 'resource.vps_console.manage' }
          },
          {
            path: 'vps/:id/traffic-billing',
            name: 'VPSTrafficBilling',
            component: () => import('@/views/operation/ResourceTrafficBilling.vue'),
            meta: { title: 'VPS 流量计费', hidden: true, targetType: 'vps', permission: 'operation.traffic.view' }
          },
          {
            path: 'edge',
            name: 'EdgeList',
            component: () => import('@/views/resource/EdgeList.vue'),
            meta: { title: '边缘加速', permission: 'resource.edge.view' }
          },
          {
            path: 'edge/:id',
            name: 'EdgeDetail',
            component: () => import('@/views/resource/EdgeDetail.vue'),
            meta: { title: '边缘加速详情', hidden: true, permission: 'resource.edge.view' }
          },
          {
            path: 'edge/:id/traffic-billing',
            name: 'EdgeTrafficBilling',
            component: () => import('@/views/operation/ResourceTrafficBilling.vue'),
            meta: { title: '边缘加速流量计费', hidden: true, targetType: 'edge', permission: 'operation.traffic.view' }
          },
          {
            path: 'bandwidth',
            name: 'BandwidthManage',
            component: () => import('@/views/resource/BandwidthManage.vue'),
            meta: { title: '带宽管理', permission: 'resource.bandwidth.view' }
          }
        ]
      },
      {
        path: 'operation',
        name: 'OperationManage',
        redirect: '/operation/monitoring/hosts',
        meta: { title: '运维管理', icon: 'Odometer', permission: 'operation.monitor_hosts.view' },
        children: [
          {
            path: 'monitoring',
            name: 'MonitoringManage',
            redirect: '/operation/monitoring/hosts',
            meta: { title: '监控管理', permission: 'operation.monitor_hosts.view' },
            children: [
              {
                path: 'hosts',
                name: 'MonitoringHosts',
                component: () => import('@/views/operation/MonitoringHosts.vue'),
                meta: { title: '监控主机', permission: 'operation.monitor_hosts.view' }
              },
              {
                path: 'pop-quality',
                name: 'PopQualityMonitoring',
                component: () => import('@/views/operation/PopQualityMonitoring.vue'),
                meta: { title: 'POP品质监控', permission: 'operation.pop_quality.view' }
              },
              {
                path: 'hosts/:id/graphs',
                name: 'MonitoringHostGraphs',
                component: () => import('@/views/operation/MonitoringHostGraphs.vue'),
                meta: { title: '监控图形', hidden: true, permission: 'operation.monitor_graphs.view' }
              },
              {
                path: 'traffic',
                name: 'TrafficReports',
                component: () => import('@/views/operation/TrafficReports.vue'),
                meta: { title: '流量计费', permission: 'operation.traffic.view' }
              },
              {
                path: 'profiles',
                name: 'GraphProfiles',
                component: () => import('@/views/operation/GraphProfiles.vue'),
                meta: { title: '计费规则', permission: 'operation.traffic_profile.manage' }
              },
              {
                path: 'zabbix',
                name: 'ZabbixServers',
                component: () => import('@/views/operation/ZabbixServers.vue'),
                meta: { title: 'Zabbix 服务器', permission: 'operation.zabbix.manage' }
              }
            ]
          },
          {
            path: 'tools',
            name: 'OperationTools',
            redirect: '/operation/tools/webssh',
            meta: { title: '运维工具', permission: 'operation.webssh.manage' },
            children: [
              {
                path: 'webssh',
                name: 'OperationWebSSH',
                component: () => import('@/views/operation/tools/WebSSHConsole.vue'),
                meta: { title: 'WebSSH', permission: 'operation.webssh.manage' }
              },
              {
                path: 'templates',
                name: 'WebSSHCommandTemplates',
                component: () => import('@/views/operation/tools/CommandTemplates.vue'),
                meta: { title: '命令模版', permission: 'operation.template.manage' }
              }
            ]
          }
        ]
      },
      {
        path: 'service',
        name: 'ServiceManage',
        redirect: '/service/overview',
        meta: { title: '服务管理', icon: 'Setting', permission: 'service.order.view' },
        children: [
          {
            path: 'overview',
            name: 'ServiceOverview',
            component: () => import('@/views/service/ServiceOverview.vue'),
            meta: { title: '服务全景', permission: 'service.overview.view' }
          },
          {
            path: 'orders',
            name: 'OrderList',
            component: () => import('@/views/service/OrderList.vue'),
            meta: { title: '服务工单', permission: 'service.order.view' }
          },
          {
            path: 'inquiries',
            name: 'InquiryList',
            component: () => import('@/views/service/InquiryList.vue'),
            meta: { title: '官网咨询', permission: 'service.inquiry.view' }
          },
          {
            path: 'tickets',
            name: 'TicketList',
            component: () => import('@/views/service/TicketList.vue'),
            meta: { title: '技术工单', permission: 'service.ticket.view' }
          },
          {
            path: 'self-service',
            name: 'SelfServiceList',
            component: () => import('@/views/service/SelfServiceList.vue'),
            meta: { title: '自助任务', permission: 'service.selfservice.view' }
          },
          {
            path: 'instances',
            name: 'InstanceList',
            component: () => import('@/views/service/InstanceList.vue'),
            meta: { title: '服务实例', permission: 'service.instance.view' }
          },
          {
            path: 'instances/:id/logs',
            name: 'InstanceLogs',
            component: () => import('@/views/service/InstanceLogs.vue'),
            meta: { title: '实例日志', hidden: true, permission: 'service.instance.view' }
          },
          {
            path: 'changes',
            name: 'ChangeList',
            component: () => import('@/views/service/ChangeList.vue'),
            meta: { title: '变更记录', permission: 'service.change.view' }
          }
        ]
      },
      {
        path: 'product',
        name: 'ProductManage',
        redirect: '/product/products',
        meta: { title: '产品管理', icon: 'Goods', permission: 'product.product.view' },
        children: [
          {
            path: 'products',
            name: 'ProductList',
            component: () => import('@/views/product/ProductList.vue'),
            meta: { title: '产品列表', permission: 'product.product.view' }
          },
          {
            path: 'products/:id',
            name: 'ProductDetail',
            component: () => import('@/views/product/ProductDetail.vue'),
            meta: { title: '产品详情', hidden: true, permission: 'product.product.view' }
          },
          {
            path: 'templates',
            name: 'ProductTemplateList',
            component: () => import('@/views/product/TemplateList.vue'),
            meta: { title: '产品模版', permission: 'product.template.view' }
          },
          {
            path: 'quick-countries',
            name: 'QuickCountryList',
            component: () => import('@/views/product/QuickCountryList.vue'),
            meta: { title: '快速查找国家', permission: 'product.product.view' }
          }
        ]
      },
      {
        path: 'supplier',
        name: 'SupplierManage',
        redirect: () => {
          const user = JSON.parse(localStorage.getItem('user') || 'null')
          if (canAccessPermission(user, 'supplier.list.view')) return '/supplier/list'
          return '/supplier/bills'
        },
        meta: { title: '供应商管理', icon: 'OfficeBuilding' },
        children: [
          {
            path: 'list',
            name: 'SupplierList',
            component: () => import('@/views/supplier/SupplierList.vue'),
            meta: { title: '供应商列表', permission: 'supplier.list.view' }
          },
          {
            path: 'list/:id',
            name: 'SupplierDetail',
            component: () => import('@/views/supplier/SupplierDetail.vue'),
            meta: { title: '供应商详情', hidden: true, permission: 'supplier.list.view' }
          },
          {
            path: 'resources',
            name: 'SupplierResourceList',
            component: () => import('@/views/supplier/SupplierResourceList.vue'),
            meta: { title: '供应商资源', permission: 'supplier.resource.view' }
          },
          {
            path: 'bills',
            name: 'SupplierBills',
            component: () => import('@/views/supplier/SupplierBills.vue'),
            meta: { title: '账单结算', permission: 'supplier.billing.view' }
          }
        ]
      },
      {
        path: 'system',
        name: 'SystemManage',
        redirect: '/system/settings',
        meta: { title: '系统设置', icon: 'Tools', permission: 'system.settings.view' },
        children: [
          {
            path: 'settings',
            name: 'SystemSettings',
            component: () => import('@/views/system/SystemSettings.vue'),
            meta: { title: '参数配置', permission: 'system.settings.view' }
          },
          {
            path: 'roles',
            name: 'RolePermissionManage',
            component: () => import('@/views/system/RolePermissionManage.vue'),
            meta: { title: '权限管理', permission: 'accounts.role.manage' }
          },
          {
            path: 'users',
            name: 'SystemUsers',
            component: () => import('@/views/system/UserManage.vue'),
            meta: { title: '用户管理', permission: 'accounts.user.manage' }
          },
          {
            path: 'users/:id/permissions',
            name: 'UserPermissions',
            component: () => import('@/views/system/UserPermissions.vue'),
            meta: { title: '权限详情', hidden: true, permission: 'accounts.user.manage' }
          },
          {
            path: 'users/:id/tokens',
            name: 'UserTokens',
            component: () => import('@/views/system/UserTokens.vue'),
            meta: { title: 'API Token', hidden: true, permission: 'accounts.user.manage' }
          },
          {
            path: 'notifications',
            name: 'NotificationManage',
            component: () => import('@/views/system/NotificationManage.vue'),
            meta: { title: '通知/IM对接', permission: 'system.notify.view' }
          },
          {
            path: 'logs',
            name: 'AuditLogList',
            component: () => import('@/views/system/AuditLogList.vue'),
            meta: { title: '操作日志', permission: 'system.log.view' }
          },
          {
            path: 'maintenance',
            name: 'SystemMaintenance',
            component: () => import('@/views/system/SystemMaintenance.vue'),
            meta: { title: '系统维护', permission: 'system.maintenance.view' }
          },
          {
            path: 'os-templates',
            name: 'SystemOsTemplates',
            component: () => import('@/views/system/SystemOsTemplateSettings.vue'),
            meta: { title: '系统模版', permission: 'system.os_template.view' }
          }
        ]
      }
    ]
  },
  { path: '/:pathMatch(.*)*', redirect: '/' }
]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes
})

router.beforeEach((to, from, next) => {
  document.title = `${to.meta.title || 'ISPManager'} - ISP管理系统`
  const token = localStorage.getItem('access_token')
  if (token && isIdleExpired()) {
    expireSession()
    if (to.path === '/login' && String(to.query.expired || '') === '1') {
      next()
      return
    }
    next({ path: '/login', query: { expired: '1' } })
    return
  }
  if (to.meta.requiresAuth !== false && !token) {
    next('/login')
    return
  }
  if (to.path === '/login' && token) {
    next('/')
    return
  }

  const required = to.matched.map(r => r.meta.permission).filter(Boolean).pop()
  if (required) {
    const user = JSON.parse(localStorage.getItem('user') || 'null')
    if (!canAccessPermission(user, required)) {
      next('/dashboard')
      return
    }
  }
  next()
})

export default router
