<template>
  <div v-loading="loading && !detail" class="pop-detail-page">
    <!-- ── 标题栏 ── -->
    <div class="page-header">
      <div class="drawer-header" v-if="detail">
        <div class="dh-left">
          <el-button :icon="ArrowLeft" @click="goBack" class="dh-back">返回</el-button>
          <el-tag :type="statusMap[detail.status]?.type" size="large" style="margin-right:10px;">
            {{ statusMap[detail.status]?.label }}
          </el-tag>
          <span class="dh-code">{{ detail.code }}</span>
          <span class="dh-name">{{ detail.name }}</span>
          <el-tag type="info" size="small" style="margin-left:8px;">
            {{ detail.city_info?.country_name }} · {{ detail.city_info?.name }}
          </el-tag>
        </div>
        <div class="dh-stats">
          <div class="ds-item">
            <span class="ds-num">{{ detail.vps_list?.length || 0 }}</span>
            <span class="ds-lbl">VPS</span>
          </div>
          <div class="ds-item">
            <span class="ds-num">{{ detail.edge_accelerations?.length || 0 }}</span>
            <span class="ds-lbl">边缘加速</span>
          </div>
          <div class="ds-item">
            <span class="ds-num">{{ totalIPs }}</span>
            <span class="ds-lbl">IP总数</span>
          </div>
          <div class="ds-item">
            <span class="ds-num">{{ assignedIPs }}</span>
            <span class="ds-lbl">已分配</span>
          </div>
        </div>
        <div class="dh-actions">
          <el-button size="small" :icon="Refresh" :loading="loading" @click="loadDetail">刷新</el-button>
          <el-button size="small" type="success" :icon="Connection" :loading="syncing" @click="doSync">从节点同步</el-button>
          <el-button size="small" type="warning" :icon="Upload" :loading="pushing" @click="doPush">下发到节点</el-button>
          <el-button size="small" type="danger" :loading="resumingProvisioning" @click="resumePaidOrders">
            资源调整后重试交付
          </el-button>
          <el-button size="small" type="primary" @click="goEdit">编辑节点</el-button>
        </div>
      </div>
      <div v-else class="dh-loading">
        <el-button :icon="ArrowLeft" @click="goBack">返回</el-button>
        <span v-if="loading" style="color:#606266;margin-left:12px;">
          <el-icon class="is-loading"><Loading/></el-icon> 加载中…
        </span>
      </div>
    </div>

    <!-- ── 待下发提示 ── -->
    <el-alert v-if="detail && detail.config_dirty" type="warning" :closable="false" show-icon
      style="margin-bottom:10px">
      <template #title>
        <span>存在未下发到节点的配置变更，请下发以保持前后端一致。</span>
        <el-button size="small" type="warning" :loading="pushing" style="margin-left:12px"
          @click="doPush">立即下发到节点</el-button>
      </template>
    </el-alert>

    <!-- ── 正文 ── -->
    <div v-if="loading && !detail" v-loading="true" style="height:300px;"/>

    <el-tabs v-else-if="detail" v-model="activeTab" class="detail-tabs">

      <!-- ★ Tab 1: 概览 ★ -->
      <el-tab-pane label="概览" name="overview">
        <el-row :gutter="16">
          <!-- 带宽利用率 -->
          <el-col :span="24" style="margin-bottom:16px;">
            <el-card shadow="never" class="bw-card">
              <div style="display:flex;align-items:center;margin-bottom:10px;">
                <span class="bw-title">带宽利用率</span>
                <div style="margin-left:auto;display:flex;gap:6px;">
                  <el-tag type="primary" size="small">VPS {{ detail.vps_bandwidth_mbps || 0 }} Mbps</el-tag>
                  <el-tag type="success" size="small">Edge {{ detail.edge_bandwidth_mbps || 0 }} Mbps</el-tag>
                  <el-tag type="warning" size="small">已用 {{ detail.used_bandwidth_mbps || 0 }} Mbps</el-tag>
                  <el-tag type="info" size="small">可用 {{ detail.available_bandwidth_mbps || 0 }} Mbps</el-tag>
                </div>
              </div>
              <div class="bw-row">
                <!-- 分段进度条：VPS占比 + Edge占比 -->
                <div class="bw-stack" v-if="detail.total_bandwidth_mbps > 0">
                  <div class="bw-seg vps-seg"
                    :style="{ width: vpsSegPct + '%' }"
                    :title="`VPS: ${detail.vps_bandwidth_mbps} Mbps`"/>
                  <div class="bw-seg edge-seg"
                    :style="{ width: edgeSegPct + '%' }"
                    :title="`Edge: ${detail.edge_bandwidth_mbps} Mbps`"/>
                </div>
                <el-progress v-else :percentage="0" :stroke-width="14" style="flex:1;"/>
                <div class="bw-labels">
                  <span class="bw-used">{{ detail.used_bandwidth_mbps || 0 }}</span>
                  <span class="bw-sep">/</span>
                  <span>{{ detail.total_bandwidth_mbps }} Mbps</span>
                  <el-tag size="small" :type="bwPct>=90?'danger':bwPct>=70?'warning':'success'" style="margin-left:8px;">
                    {{ bwPct }}%
                  </el-tag>
                </div>
              </div>
            </el-card>
          </el-col>

          <!-- 基础信息 -->
          <el-col :xs="24" :sm="12">
            <el-card shadow="never" header="基础信息" class="info-card">
              <el-descriptions :column="1" border size="small">
                <el-descriptions-item label="节点编号">
                  <el-tag type="primary" size="small">{{ detail.code }}</el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="状态">
                  <el-tag :type="statusMap[detail.status]?.type" size="small">
                    {{ statusMap[detail.status]?.label }}
                  </el-tag>
                  <el-button
                    size="small" link type="primary" style="margin-left:8px;"
                    @click="openStatusChange"
                  >变更</el-button>
                </el-descriptions-item>
                <el-descriptions-item label="城市">
                  {{ detail.city_info?.country_name }} · {{ detail.city_info?.name }}
                </el-descriptions-item>
                <el-descriptions-item label="节点类型">
                  <el-tag size="small" :type="popTypeMap[detail.pop_type]?.type || 'info'">
                    {{ detail.pop_type_label || popTypeMap[detail.pop_type]?.label || detail.pop_type || 'POP' }}
                  </el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="部署状态">
                  <el-tag size="small" :type="deployMap[detail.deploy_status]?.type || 'info'">
                    {{ detail.deploy_status_label || deployMap[detail.deploy_status]?.label || '未部署' }}
                  </el-tag>
                  <span v-if="detail.agent_version" style="color:#909399;margin-left:6px;font-size:12px;">
                    {{ detail.agent_version }}
                  </span>
                  <el-button size="small" link type="success" style="margin-left:8px;" @click="emitDeploy">部署 / WebSSH</el-button>
                </el-descriptions-item>
                <el-descriptions-item label="最近同步">
                  {{ detail.last_sync_at ? formatDate(detail.last_sync_at) : '未同步' }}
                </el-descriptions-item>
                <el-descriptions-item label="网络类型">{{ detail.network_type_label }}</el-descriptions-item>
                <el-descriptions-item label="ASN/企业性质">{{ detail.asn_nature_label }}</el-descriptions-item>
                <el-descriptions-item label="IP属性">{{ detail.ip_attribute_label || '原生' }}</el-descriptions-item>
                <el-descriptions-item label="IP 交付">
                  <el-tag :type="detail.ip_delivery_mode === 'nat' ? 'warning' : 'success'" size="small">
                    {{ detail.ip_delivery_mode_label || (detail.ip_delivery_mode === 'nat' ? 'NAT 交付' : '公网直连') }}
                  </el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="运营商">
                  <el-tag v-for="c in detail.carriers_info" :key="c.id" size="small" style="margin:1px;">
                    {{ c.code || c.name }}
                  </el-tag>
                  <span v-if="!detail.carriers_info?.length" style="color:#C0C4CC;">-</span>
                </el-descriptions-item>
                <el-descriptions-item label="创建时间">
                  {{ formatDate(detail.created_at) }}
                </el-descriptions-item>
                <el-descriptions-item label="备注">
                  {{ detail.remark || '-' }}
                </el-descriptions-item>
              </el-descriptions>
            </el-card>
          </el-col>

          <!-- 硬件 & 管理 -->
          <el-col :xs="24" :sm="12">
            <el-card shadow="never" header="硬件与管理" class="info-card">
              <el-descriptions :column="1" border size="small">
                <el-descriptions-item label="管理 IP">
                  <el-tag size="small" type="info">{{ detail.mgmt_ip || '-' }}</el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="管理URL">
                  <a v-if="detail.mgmt_url" :href="detail.mgmt_url" target="_blank" rel="noopener">
                    {{ detail.mgmt_url }}
                  </a>
                  <span v-else style="color:#C0C4CC;">-</span>
                </el-descriptions-item>
                <el-descriptions-item label="操作系统">
                  {{ osLabels[detail.os_type] || detail.os_type || '-' }}
                  <span v-if="detail.os_version" style="color:#909399;"> {{ detail.os_version }}</span>
                </el-descriptions-item>
                <el-descriptions-item label="CPU">
                  {{ detail.cpu_model || '-' }}
                  <span v-if="detail.cpu_cores"> · {{ detail.cpu_cores }} 核</span>
                </el-descriptions-item>
                <el-descriptions-item label="内存">
                  {{ detail.ram_gb ? detail.ram_gb + ' GB' : '-' }}
                </el-descriptions-item>
                <el-descriptions-item label="硬盘">
                  {{ detail.disk_info || '-' }}
                </el-descriptions-item>
                <el-descriptions-item label="SNMP">
                  {{ detail.snmp_community ? `${detail.snmp_version} · ${detail.snmp_community}` : '-' }}
                </el-descriptions-item>
              </el-descriptions>
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>

      <!-- ★ Tab: 监控 ★ -->
      <el-tab-pane label="监控" name="monitor">
        <MonitoringPanel
          target-type="pop"
          :resource-id="detail.id"
          :resource-name="detail.name"
          :monitored-ip="detail.mgmt_ip"
          :active="activeTab === 'monitor'"
        />
      </el-tab-pane>

      <!-- ★ Tab 2: VPS ★ -->
      <el-tab-pane name="vps">
        <template #label>
          VPS <el-badge :value="detail.vps_list?.length" :max="99" type="primary"/>
        </template>
        <el-table :data="detail.vps_list" border stripe size="small" empty-text="暂无VPS">
          <el-table-column prop="vps_no" label="编号" width="140">
            <template #default="{row}">
              <el-tag type="primary" size="small">{{ row.vps_no }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="name" label="名称" min-width="150">
            <template #default="{row}">
              {{ row.name }}
              <el-tag v-if="row.is_synced" size="small" type="success" effect="plain" style="margin-left:4px;">
                PVE{{ row.source_vmid ? ' #' + row.source_vmid : '' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{row}">
              <el-tag :type="vpsStatusMap[row.status]?.type" size="small">{{ vpsStatusMap[row.status]?.label }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="规格" width="140">
            <template #default="{row}">
              <span style="font-size:11px;">
                {{ row.vcpu }}核 · {{ (row.ram_mb/1024).toFixed(0) }}G · {{ row.disk_gb }}G
              </span>
            </template>
          </el-table-column>
          <el-table-column label="OS" width="100">
            <template #default="{row}">
              {{ osLabels[row.os_type] || row.os_type || '-' }}
            </template>
          </el-table-column>
          <el-table-column prop="mgmt_ip" label="管理IP" width="130">
            <template #default="{row}">
              <el-text type="info" size="small">{{ row.mgmt_ip || '-' }}</el-text>
            </template>
          </el-table-column>
          <el-table-column prop="customer_name" label="客户" width="100">
            <template #default="{row}">{{ row.customer_name || '-' }}</template>
          </el-table-column>
          <el-table-column label="IP列表" min-width="160">
            <template #default="{row}">
              <div style="display:flex;flex-wrap:wrap;gap:3px;">
                <el-tag
                  v-for="ip in (row.ip_list||[]).slice(0,3)" :key="ip.id"
                  size="small"
                  :type="ip.is_primary?'primary':'info'"
                >{{ ip.address }}</el-tag>
                <el-tag v-if="(row.ip_list||[]).length>3" size="small" type="info">
                  +{{ row.ip_list.length-3 }}
                </el-tag>
              </div>
              <span v-if="!(row.ip_list||[]).length" style="color:#C0C4CC;font-size:11px;">无IP</span>
            </template>
          </el-table-column>
          <el-table-column label="到期" width="105">
            <template #default="{row}">
              <span :class="isExpiring(row.expire_time)" style="font-size:11px;">
                {{ row.expire_time ? formatDate(row.expire_time) : '无期限' }}
              </span>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- ★ Tab 3: 边缘加速 ★ -->
      <el-tab-pane name="edge">
        <template #label>
          边缘加速 <el-badge :value="detail.edge_accelerations?.length" :max="99" type="success"/>
        </template>
        <el-table :data="detail.edge_accelerations" border stripe size="small" empty-text="暂无边缘加速节点">
          <el-table-column prop="edge_no" label="编号" width="145">
            <template #default="{row}">
              <el-tag type="success" size="small">{{ row.edge_no }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="name" label="名称" min-width="130"/>
          <el-table-column label="协议" width="100">
            <template #default="{row}">
              <el-tag size="small">{{ protocolMap[row.protocol] || row.protocol }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{row}">
              <el-tag :type="edgeStatusMap[row.status]?.type" size="small">
                {{ edgeStatusMap[row.status]?.label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="bandwidth_mbps" label="带宽(Mbps)" width="105" align="right"/>
          <el-table-column prop="customer_name" label="客户" width="100">
            <template #default="{row}">{{ row.customer_name || '-' }}</template>
          </el-table-column>
          <el-table-column label="IP列表" min-width="160">
            <template #default="{row}">
              <div style="display:flex;flex-wrap:wrap;gap:3px;">
                <el-tag v-for="ip in (row.ip_list||[]).slice(0,3)" :key="ip.id" size="small"
                  :type="ip.is_primary?'success':'info'">{{ ip.address }}</el-tag>
                <el-tag v-if="(row.ip_list||[]).length>3" size="small" type="info">
                  +{{ row.ip_list.length-3 }}
                </el-tag>
              </div>
              <span v-if="!(row.ip_list||[]).length" style="color:#C0C4CC;font-size:11px;">无IP</span>
            </template>
          </el-table-column>
          <el-table-column label="到期" width="105">
            <template #default="{row}">
              <span :class="isExpiring(row.expire_time)" style="font-size:11px;">
                {{ row.expire_time ? formatDate(row.expire_time) : '无期限' }}
              </span>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- ★ Tab 4: IP 资源 ★ -->
      <el-tab-pane name="ip">
        <template #label>
          IP资源 <el-badge :value="detail.ip_blocks?.length" :max="99" type="warning"/>
        </template>
        <div v-for="block in detail.ip_blocks" :key="block.id" class="ip-block-card">
          <div class="ipb-header">
            <el-tag type="primary" style="font-size:13px;padding:0 10px;">{{ block.cidr }}</el-tag>
            <el-tag :type="block.ip_version==='v6'?'warning':'primary'" size="small">
              {{ block.ip_version==='v6'?'IPv6':'IPv4' }}
            </el-tag>
            <el-tag v-if="block.ip_attribute" type="info" size="small">{{ block.ip_attribute_label || block.ip_attribute }}</el-tag>
            <el-tag v-if="block.network_type" size="small" :type="block.network_type === 'home_broadband' ? 'warning' : 'primary'">
              {{ block.network_type_label || block.network_type }}
            </el-tag>
            <el-tag v-if="block.asn_nature" size="small" effect="plain" type="info">
              {{ block.asn_nature_label || block.asn_nature }}
            </el-tag>
            <el-tag v-if="block.gateway" size="small" effect="plain">网关 {{ block.gateway }}</el-tag>
            <el-tag v-if="block.is_synced" size="small" type="success" effect="plain">同步</el-tag>
            <div style="flex:1;margin:0 12px;">
              <el-progress
                :percentage="calcIPPct(block)"
                :stroke-width="8"
                :color="getIPColor(block)"
              />
            </div>
            <div style="display:flex;gap:5px;flex-shrink:0;">
              <el-tag size="small" type="success">可用 {{ block.ip_stats?.available||0 }}</el-tag>
              <el-tag size="small" type="primary">已分配 {{ block.ip_stats?.assigned||0 }}</el-tag>
              <el-tag v-if="block.ip_stats?.system" size="small" type="warning">系统 {{ block.ip_stats.system }}</el-tag>
              <el-tag size="small" type="info">共 {{ block.ip_stats?.total||0 }}</el-tag>
            </div>
          </div>
          <div v-if="block.description" class="ipb-desc">{{ block.description }}</div>
        </div>
        <el-empty v-if="!detail.ip_blocks?.length" description="暂无IP地址块" :image-size="60"/>
      </el-tab-pane>

      <!-- ★ Tab 5: 网络接口 ★ -->
      <el-tab-pane name="iface">
        <template #label>
          网络接口 <el-badge :value="detail.interfaces?.length" :max="99"/>
        </template>
        <div style="display:flex;justify-content:flex-end;margin-bottom:10px;">
          <el-button type="primary" size="small" :icon="Plus" @click="openIfaceDialog()">新增接口</el-button>
        </div>
        <el-table :data="detail.interfaces" border stripe size="small" empty-text="暂无网络接口">
          <el-table-column prop="ifname" label="接口名" width="135">
            <template #default="{row}">
              {{ row.ifname }}
              <el-tag v-if="row.is_synced" size="small" type="success" effect="plain" style="margin-left:4px;">同步</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="interface_type_label" label="类型" width="90"/>
          <el-table-column prop="mac_address" label="MAC地址" width="150">
            <template #default="{row}">
              <el-text type="info" size="small">{{ row.mac_address || '-' }}</el-text>
            </template>
          </el-table-column>
          <el-table-column prop="speed_mbps" label="速率" width="90" align="right">
            <template #default="{row}">{{ row.speed_mbps ? row.speed_mbps+'M' : '-' }}</template>
          </el-table-column>
          <el-table-column prop="mtu" label="MTU" width="70" align="center"/>
          <el-table-column label="状态" width="80">
            <template #default="{row}">
              <el-tag :type="row.status==='up'?'success':'danger'" size="small">
                {{ row.status?.toUpperCase() }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="管理口" width="72" align="center">
            <template #default="{row}">
              <el-icon v-if="row.is_management" color="#67C23A"><Select/></el-icon>
              <span v-else style="color:#C0C4CC;">-</span>
            </template>
          </el-table-column>
          <el-table-column prop="vlan_id" label="VLAN" width="68" align="center">
            <template #default="{row}">{{ row.vlan_id || '-' }}</template>
          </el-table-column>
          <el-table-column prop="description" label="描述" min-width="120"/>
          <el-table-column label="操作" width="155" fixed="right">
            <template #default="{row}">
              <el-button link type="primary" size="small" @click="openIfaceDialog(row)">编辑</el-button>
              <el-button v-if="row.netplan_config?.spec" link type="warning" size="small"
                :loading="ifacePushingId===row.id" @click="handlePushIface(row)">下发</el-button>
              <el-button link type="danger" size="small" @click="handleDeleteIface(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- ★ Tab 6: 路由配置 ★ -->
      <el-tab-pane name="routes">
        <template #label>
          路由配置 <el-badge :value="detail.route_tables?.length" :max="99"/>
        </template>
        <div style="display:flex;justify-content:flex-end;margin-bottom:10px;gap:8px;">
          <el-button type="primary" size="small" :icon="Plus" @click="openRouteTableDialog()">新增路由表</el-button>
        </div>
        <div v-for="rt in detail.route_tables" :key="rt.id" class="route-table-card">
          <div class="rt-header">
            <el-tag type="primary" size="small">Table {{ rt.table_id }}</el-tag>
            <span class="rt-name">{{ rt.name || '(未命名)' }}</span>
            <el-tag v-if="rt.is_synced" size="small" type="success" effect="plain">同步</el-tag>
            <el-text type="info" size="small">{{ rt.description }}</el-text>
            <div style="margin-left:auto;display:flex;gap:6px;">
              <el-button link size="small" type="primary" @click="openRouteTableDialog(rt)">编辑</el-button>
              <el-button link size="small" type="success" @click="openStaticRouteDialog(rt)">新增路由</el-button>
              <el-button link size="small" type="danger" @click="handleDeleteRouteTable(rt)">删除表</el-button>
            </div>
          </div>
          <!-- 静态路由 -->
          <el-table :data="rt.static_routes" size="small" stripe style="margin-top:6px;" empty-text="无路由条目">
            <el-table-column prop="destination" label="目标网络" width="175"/>
            <el-table-column prop="gateway" label="网关" width="150">
              <template #default="{row}">{{ row.gateway || '-' }}</template>
            </el-table-column>
            <el-table-column prop="metric" label="Metric" width="75" align="center"/>
            <el-table-column label="状态" width="80">
              <template #default="{row}">
                <el-tag :type="row.is_active?'success':'info'" size="small">
                  {{ row.is_active?'生效':'停用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="description" label="描述" min-width="100"/>
            <el-table-column label="操作" width="100" fixed="right">
              <template #default="{row}">
              <el-button link type="primary" size="small" @click="openStaticRouteEditDialog(row, rt)">编辑</el-button>
              <el-button link type="danger" size="small" @click="handleDeleteStaticRoute(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
        <el-empty v-if="!detail.route_tables?.length" description="暂无路由表" :image-size="60"/>
      </el-tab-pane>

      <!-- ★ Tab 7: 策略路由 ★ -->
      <el-tab-pane name="policy">
        <template #label>
          策略路由 <el-badge :value="detail.policy_routes?.length" :max="99"/>
        </template>
        <div style="display:flex;justify-content:flex-end;margin-bottom:10px;">
          <el-button type="primary" size="small" :icon="Plus" @click="openPolicyDialog()">新增策略路由</el-button>
        </div>
        <el-table :data="detail.policy_routes" border stripe size="small" empty-text="暂无策略路由">
          <el-table-column prop="priority" label="优先级" width="95" align="center">
            <template #default="{row}">
              {{ row.priority }}
              <el-tag v-if="row.is_synced" size="small" type="success" effect="plain">同步</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="源地址" width="155">
            <template #default="{row}">
              <el-text type="primary">{{ row.src_prefix || 'any' }}</el-text>
            </template>
          </el-table-column>
          <el-table-column label="目标地址" width="155">
            <template #default="{row}">{{ row.dst_prefix || 'any' }}</template>
          </el-table-column>
          <el-table-column prop="fwmark" label="FWMark" width="90">
            <template #default="{row}">{{ row.fwmark || '-' }}</template>
          </el-table-column>
          <el-table-column label="动作" width="100">
            <template #default="{row}">
              <el-tag :type="actionTag[row.action]" size="small">{{ row.action }}</el-tag>
              <span v-if="row.lookup_table" style="font-size:11px;color:#606266;margin-left:4px;">
                → table {{ row.lookup_table }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="75">
            <template #default="{row}">
              <el-tag :type="row.is_active?'success':'info'" size="small">
                {{ row.is_active?'生效':'停用' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="description" label="描述" min-width="120"/>
          <el-table-column label="操作" width="100" fixed="right">
            <template #default="{row}">
              <el-button link type="primary" size="small" @click="openPolicyDialog(row)">编辑</el-button>
              <el-button link type="danger" size="small" @click="handleDeletePolicyRoute(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- ★ Tab 8: 操作日志 ★ -->
      <el-tab-pane name="logs" label="操作日志">
        <el-timeline style="padding:8px 0;">
          <el-timeline-item
            v-for="log in detail.operation_logs"
            :key="log.id"
            :timestamp="formatDate(log.created_at)"
            :type="logType(log.action_type)"
            placement="top"
          >
            <div class="log-item">
              <el-tag size="small" :type="logType(log.action_type)" style="margin-right:6px;">
                {{ log.action_type_label || log.action_type }}
              </el-tag>
              <span class="log-desc">{{ log.description }}</span>
              <el-text type="info" size="small" style="margin-left:8px;">
                {{ log.operator_name || '系统' }}
              </el-text>
            </div>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-if="!detail.operation_logs?.length" description="暂无操作日志" :image-size="60"/>
      </el-tab-pane>
    </el-tabs>
  </div>

  <!-- ── 状态变更弹窗 ── -->
  <el-dialog v-model="statusDialogVisible" title="变更节点状态" width="400px" destroy-on-close append-to-body>
    <el-form :model="statusForm" label-width="80px">
      <el-form-item label="新状态">
        <el-radio-group v-model="statusForm.status">
          <el-radio v-for="s in statusOptions" :key="s.value" :value="s.value">{{ s.label }}</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="原因">
        <el-input v-model="statusForm.reason" type="textarea" :rows="2" placeholder="变更原因"/>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="statusDialogVisible=false">取消</el-button>
      <el-button type="primary" :loading="statusChanging" @click="doStatusChange">确认</el-button>
    </template>
  </el-dialog>

  <!-- ── 网络接口弹窗（对齐 vpop InterfaceLiveDetail netplan 字段） ── -->
  <el-dialog v-model="ifaceDialogVisible" :title="editIface?'编辑接口':'新增接口'" width="640px" destroy-on-close append-to-body>
    <el-form :model="ifaceForm" label-width="110px" size="default">
      <el-divider content-position="left">基础信息</el-divider>
      <el-form-item label="接口名称" required>
        <el-input v-model="ifaceForm.ifname" placeholder="如 eth0, bond0, vlan100" :disabled="!!editIface?.is_synced"/>
      </el-form-item>
      <el-form-item label="接口类型">
        <el-select v-model="ifaceForm.interface_type" style="width:100%">
          <el-option v-for="t in ifaceTypeOptions" :key="t.value" :label="t.label" :value="t.value"/>
        </el-select>
      </el-form-item>
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="MTU">
            <el-input-number v-model="ifaceForm.mtu" :min="576" :max="9000" style="width:100%"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="速率(Mbps)">
            <el-input-number v-model="ifaceForm.speed_mbps" :min="0" style="width:100%"/>
          </el-form-item>
        </el-col>
      </el-row>
      <el-form-item v-if="ifaceForm.interface_type==='vlan'" label="VLAN ID">
        <el-input-number v-model="ifaceForm.vlan_id" :min="1" :max="4094" style="width:100%"/>
      </el-form-item>
      <el-form-item v-if="ifaceForm.interface_type==='vlan'" label="父接口(link)">
        <el-input v-model="ifaceForm.vlan_link" placeholder="如 eth0"/>
      </el-form-item>
      <el-form-item v-if="ifaceForm.interface_type==='bridge'" label="桥接端口">
        <el-input v-model="ifaceForm.bridge_ports" placeholder="逗号分隔，如 eth0 eth1"/>
      </el-form-item>
      <el-form-item label="MAC地址">
        <el-input v-model="ifaceForm.mac_address" placeholder="如 00:11:22:33:44:55"/>
      </el-form-item>
      <el-form-item label="描述">
        <el-input v-model="ifaceForm.description"/>
      </el-form-item>

      <el-divider content-position="left">Netplan 下发配置</el-divider>
      <el-form-item label="IP 地址(CIDR)">
        <el-input v-model="ifaceForm.addressesText" placeholder="如 10.0.0.1/24，多个用逗号分隔"/>
      </el-form-item>
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="IPv4 网关">
            <el-input v-model="ifaceForm.gateway4" placeholder="可选"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="DHCPv4">
            <el-switch v-model="ifaceForm.dhcp4"/>
          </el-form-item>
        </el-col>
      </el-row>
      <el-alert type="info" :closable="false" show-icon
        title="保存并下发将把 netplan 配置写入 POP 节点（apply-config），与 vpop 接口管理一致。" style="margin-bottom:8px"/>
    </el-form>
    <template #footer>
      <el-checkbox v-model="ifacePushToNode" style="margin-right:12px">保存并下发到节点</el-checkbox>
      <el-button @click="ifaceDialogVisible=false">取消</el-button>
      <el-button type="primary" :loading="ifaceSaving" @click="saveIface(false)">仅保存</el-button>
      <el-button type="warning" :loading="ifaceSaving" @click="saveIface(true)">保存并下发</el-button>
    </template>
  </el-dialog>

  <!-- ── 路由表弹窗 ── -->
  <el-dialog v-model="routeTableDialogVisible" :title="editRouteTable?'编辑路由表':'新增路由表'" width="460px" destroy-on-close append-to-body>
    <el-form :model="routeTableForm" label-width="100px">
      <el-form-item label="路由表ID" required>
        <el-input-number v-model="routeTableForm.table_id" :min="1" :max="252" style="width:100%"
          :placeholder="'Linux 路由表 ID (1-252)'"/>
      </el-form-item>
      <el-form-item label="名称">
        <el-input v-model="routeTableForm.name" placeholder="如 default, custom1"/>
      </el-form-item>
      <el-form-item label="描述">
        <el-input v-model="routeTableForm.description"/>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="routeTableDialogVisible=false">取消</el-button>
      <el-button type="primary" :loading="routeTableSaving" @click="saveRouteTable">确认</el-button>
    </template>
  </el-dialog>

  <!-- ── 静态路由弹窗 ── -->
  <el-dialog v-model="staticRouteDialogVisible" :title="editStaticRoute?'编辑静态路由':'新增静态路由'" width="480px" destroy-on-close append-to-body>
    <el-form :model="staticRouteForm" label-width="100px">
      <el-form-item label="路由表">
        <el-tag>Table {{ currentRouteTable?.table_id }} · {{ currentRouteTable?.name }}</el-tag>
      </el-form-item>
      <el-form-item label="目标网络" required>
        <el-input v-model="staticRouteForm.destination" placeholder="CIDR，如 0.0.0.0/0 或 192.168.1.0/24"/>
      </el-form-item>
      <el-form-item label="出接口">
        <el-select v-model="staticRouteForm.interface" clearable filterable placeholder="节点下发需要出接口" style="width:100%">
          <el-option
            v-for="iface in detail?.interfaces || []" :key="iface.id"
            :label="iface.ifname || `接口 ${iface.id}`" :value="iface.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="网关">
        <el-input v-model="staticRouteForm.gateway" placeholder="如 10.0.0.1（为空则仅指定接口）"/>
      </el-form-item>
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="Metric">
            <el-input-number v-model="staticRouteForm.metric" :min="0" style="width:100%"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="状态">
            <el-switch v-model="staticRouteForm.is_active" active-text="生效" inactive-text="停用"/>
          </el-form-item>
        </el-col>
      </el-row>
      <el-form-item label="描述">
        <el-input v-model="staticRouteForm.description"/>
      </el-form-item>
      <el-form-item label="同步到节点">
        <el-switch v-model="staticRouteForm.push_to_node"/>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="staticRouteDialogVisible=false">取消</el-button>
      <el-button type="primary" :loading="staticRouteSaving" @click="saveStaticRoute">确认</el-button>
    </template>
  </el-dialog>

  <!-- ── 策略路由弹窗 ── -->
  <el-dialog v-model="policyDialogVisible" :title="editPolicy?'编辑策略路由':'新增策略路由'" width="520px" destroy-on-close append-to-body>
    <el-form :model="policyForm" label-width="110px">
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="优先级" required>
            <el-input-number v-model="policyForm.priority" :min="0" :max="32766" style="width:100%"
              placeholder="数值越小越优先"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="动作">
            <el-select v-model="policyForm.action" style="width:100%">
              <el-option value="lookup" label="lookup（查表）"/>
              <el-option value="blackhole" label="blackhole（丢弃）"/>
              <el-option value="unreachable" label="unreachable"/>
            </el-select>
          </el-form-item>
        </el-col>
      </el-row>
      <el-form-item label="源地址前缀">
        <el-input v-model="policyForm.src_prefix" placeholder="CIDR 或空（any）"/>
      </el-form-item>
      <el-form-item label="目标地址前缀">
        <el-input v-model="policyForm.dst_prefix" placeholder="CIDR 或空"/>
      </el-form-item>
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="FWMark">
            <el-input v-model="policyForm.fwmark" placeholder="如 0x100"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="查找路由表">
            <el-select v-model="policyForm.lookup_table" clearable style="width:100%"
              placeholder="仅 lookup 时有效">
              <el-option
                v-for="rt in detail?.route_tables" :key="rt.id"
                :label="`Table ${rt.table_id}·${rt.name||''}`" :value="rt.id"
              />
            </el-select>
          </el-form-item>
        </el-col>
      </el-row>
      <el-form-item label="状态">
        <el-switch v-model="policyForm.is_active" active-text="生效" inactive-text="停用"/>
      </el-form-item>
      <el-form-item label="描述">
        <el-input v-model="policyForm.description"/>
      </el-form-item>
      <el-form-item label="同步到节点">
        <el-switch v-model="policyForm.push_to_node"/>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="policyDialogVisible=false">取消</el-button>
      <el-button type="primary" :loading="policySaving" @click="savePolicy">确认</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Select, Loading, Connection, Upload, ArrowLeft } from '@element-plus/icons-vue'
import {
  getPopPointDetail, changePopPointStatus, syncPopFromNode, pushPopToNode,
  resumePaidProvisioning,
  createInterface, updateInterface, deleteInterface, pushInterfaceToNode,
  createRouteTable, updateRouteTable, deleteRouteTable,
  createStaticRoute, updateStaticRoute, deleteStaticRoute,
  createPolicyRoute, updatePolicyRoute, deletePolicyRoute,
} from '@/api/resource'
import { buildIfaceNetplanConfig } from '@/composables/useConfirmPush'
import MonitoringPanel from '@/components/MonitoringPanel.vue'

const route = useRoute()
const router = useRouter()

// ── 路由参数 ──────────────────────────────────────────────────
const popId = computed(() => route.params.id)

// ── 内部状态 ──────────────────────────────────────────────────
const detail       = ref(null)
const loading      = ref(false)
const syncing      = ref(false)
const pushing      = ref(false)
const resumingProvisioning = ref(false)
const activeTab    = ref('overview')

function goBack() {
  router.push('/resource/pop')
}
function goEdit() {
  if (detail.value) router.push({ path: '/resource/pop', query: { edit: detail.value.id } })
}

// ── 加载 ──────────────────────────────────────────────────────
async function loadDetail() {
  if (!popId.value) return
  loading.value = true
  try {
    detail.value = await getPopPointDetail(popId.value)
  } catch (e) {
    ElMessage.error('加载失败：' + (e?.response?.data?.detail || e.message))
  } finally { loading.value = false }
}

onMounted(loadDetail)

// ── 从节点同步系统数据 ─────────────────────────────────────────
async function doSync() {
  if (!detail.value) return
  try {
    await ElMessageBox.confirm(
      '将从 PE 节点拉取接口、路由、策略路由、IP 等系统现场数据并覆盖同步到本系统，'
      + '此前由同步生成的陈旧记录会被自动清理（手工录入的数据不受影响）。是否继续？',
      '从节点同步', { type: 'warning', confirmButtonText: '开始同步' }
    )
  } catch { return }
  syncing.value = true
  try {
    const res = await syncPopFromNode(detail.value.id)
    if (res.ok) {
      ElMessage.success('节点数据同步完成')
    } else {
      const errs = Object.entries(res.errors || {}).map(([k, v]) => `${k}: ${v}`).join('；')
      ElMessage.warning('同步完成，部分区块失败：' + (errs || '详见结果'))
    }
    await loadDetail()
  } catch (e) {
    ElMessage.error('同步失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    syncing.value = false
  }
}

// ── 下发配置到节点（静态路由 / 策略路由） ──────────────────────
async function doPush() {
  if (!detail.value) return
  try {
    await ElMessageBox.confirm(
      '将把本系统中维护的静态路由、策略路由下发到 PE 节点并即时生效（ip route/rule replace），'
      + '已存在的条目会自动跳过。是否继续？',
      '下发到节点', { type: 'warning', confirmButtonText: '开始下发' }
    )
  } catch { return }
  pushing.value = true
  try {
    const res = await pushPopToNode(detail.value.id)
    const parts = Object.entries(res.summary || {}).map(([k, v]) => {
      const label = { static_routes: '静态路由', policy_rules: '策略路由' }[k] || k
      return `${label}：下发 ${v.pushed ?? 0}（新建 ${v.created ?? 0}）`
    })
    if (res.ok) {
      ElMessage.success('下发完成 — ' + (parts.join('；') || '无可下发项'))
    } else {
      const errs = Object.entries(res.errors || {}).map(([k, v]) => `${k}: ${v}`).join('；')
      ElMessage.warning('下发完成，部分失败：' + (errs || '详见结果'))
    }
    await loadDetail()
  } catch (e) {
    ElMessage.error('下发失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    pushing.value = false
  }
}

async function resumePaidOrders() {
  if (!detail.value) return
  try {
    await ElMessageBox.confirm(
      '将检查该 POP 中已付款且因库存不足等待交付的订单。资源已补齐的订单会通知管理员手动交付，不会自动开通。',
      '检查待交付订单',
      { type: 'warning', confirmButtonText: '发送提醒' },
    )
  } catch {
    return
  }
  resumingProvisioning.value = true
  try {
    const result = await resumePaidProvisioning(detail.value.id)
    const ready = result?.ready_for_manual || 0
    const stillInsufficient = result?.still_insufficient || 0
    ElMessage.success(`已检查 ${result?.checked || 0} 个订单，可手动交付 ${ready} 个` +
      (stillInsufficient ? `，仍待补齐资源 ${stillInsufficient} 个` : ''))
    await loadDetail()
  } catch (error) {
    ElMessage.error('重试交付失败：' + (error?.response?.data?.detail || error?.message || '未知错误'))
  } finally {
    resumingProvisioning.value = false
  }
}

// ── 计算属性 ──────────────────────────────────────────────────
const bwPct = computed(() => {
  if (!detail.value?.total_bandwidth_mbps) return 0
  return Math.round((detail.value.used_bandwidth_mbps || 0) / detail.value.total_bandwidth_mbps * 100)
})
const bwColor = computed(() => bwPct.value >= 90 ? '#F56C6C' : bwPct.value >= 70 ? '#E6A23C' : '#67C23A')
// 分段进度条：VPS占总带宽的百分比、Edge占总带宽的百分比
const vpsSegPct = computed(() => {
  if (!detail.value?.total_bandwidth_mbps) return 0
  return Math.min(100, Math.round((detail.value.vps_bandwidth_mbps || 0) / detail.value.total_bandwidth_mbps * 100))
})
const edgeSegPct = computed(() => {
  if (!detail.value?.total_bandwidth_mbps) return 0
  return Math.min(100 - vpsSegPct.value, Math.round((detail.value.edge_bandwidth_mbps || 0) / detail.value.total_bandwidth_mbps * 100))
})
const totalIPs = computed(() => (detail.value?.ip_blocks||[]).reduce((s,b)=>s+(b.ip_stats?.total||0),0))
const assignedIPs = computed(() => (detail.value?.ip_blocks||[]).reduce((s,b)=>s+(b.ip_stats?.assigned||0),0))

// ── 常量 ──────────────────────────────────────────────────────
const statusMap = {
  online: { label:'在线', type:'success' }, offline: { label:'离线', type:'danger' },
  maintenance: { label:'维护中', type:'warning' }, decommissioned: { label:'已下线', type:'info' }
}
const popTypeMap = {
  pop: { label:'POP', type:'primary' }, pe: { label:'PE', type:'warning' },
  vdc: { label:'VDC', type:'success' }, other: { label:'其他', type:'info' }
}
const deployMap = {
  undeployed: { label:'未部署', type:'info' }, deploying: { label:'部署中', type:'warning' },
  deployed: { label:'已部署', type:'success' }, failed: { label:'部署失败', type:'danger' }
}
function emitDeploy() {
  if (detail.value) router.push(`/resource/pop/${detail.value.id}/deploy`)
}
const statusOptions = [
  { value:'online', label:'在线' }, { value:'offline', label:'离线' },
  { value:'maintenance', label:'维护中' }, { value:'decommissioned', label:'已下线' }
]
const vpsStatusMap = {
  running:   { label:'运行中', type:'success' }, stopped:   { label:'已停止', type:'info'    },
  suspended: { label:'已暂停', type:'warning' }, creating:  { label:'创建中', type:'primary' },
  error:     { label:'故障',   type:'danger'  }
}
const edgeStatusMap = {
  active:  { label:'运行中', type:'success' }, stopped: { label:'已停止', type:'info'    },
  pending: { label:'待配置', type:'warning' }, error:   { label:'故障',   type:'danger'  }
}
const protocolMap = { gre:'GRE', wireguard:'WireGuard', socks5:'Socks5' }
const osLabels = {
  debian:'Debian', ubuntu:'Ubuntu', centos:'CentOS',
  rhel:'RHEL', windows:'Windows Server', other:'其他'
}
const actionTag = { lookup:'primary', blackhole:'danger', unreachable:'warning' }
const ifaceTypeOptions = [
  { value:'physical', label:'物理接口' }, { value:'vlan', label:'VLAN' },
  { value:'bond', label:'Bond/LACP' }, { value:'bridge', label:'Bridge' },
  { value:'loopback', label:'Loopback' }, { value:'tunnel', label:'Tunnel' }, { value:'dummy', label:'Dummy' }
]

// ── 工具 ──────────────────────────────────────────────────────
function formatDate(dt) {
  if (!dt) return '-'
  return new Date(dt).toLocaleString('zh-CN',{hour12:false}).replace(/\//g,'-').replace(',','')
}
function isExpiring(dt) {
  if (!dt) return ''
  const days = (new Date(dt) - Date.now()) / 86400000
  if (days < 0) return 'expire-red'
  if (days < 30) return 'expire-warn'
  return ''
}
function calcIPPct(block) {
  const t = block.ip_stats?.total || 0
  return t ? Math.round((block.ip_stats?.assigned||0)/t*100) : 0
}
function getIPColor(block) {
  const p = calcIPPct(block)
  return p>=90 ? '#F56C6C' : p>=70 ? '#E6A23C' : '#67C23A'
}
function logType(a) {
  if (['create'].includes(a)) return 'success'
  if (['delete','ip_release'].includes(a)) return 'danger'
  if (['status_change'].includes(a)) return 'warning'
  if (['ip_assign','bw_alloc'].includes(a)) return 'primary'
  return ''
}

// ── 状态变更 ──────────────────────────────────────────────────
const statusDialogVisible = ref(false)
const statusChanging = ref(false)
const statusForm = ref({ status: 'online', reason: '' })

function openStatusChange() {
  statusForm.value = { status: detail.value?.status || 'online', reason: '' }
  statusDialogVisible.value = true
}
async function doStatusChange() {
  statusChanging.value = true
  try {
    await changePopPointStatus(detail.value.id, statusForm.value)
    ElMessage.success('状态已更新')
    statusDialogVisible.value = false
    loadDetail()
  } catch (e) {
    ElMessage.error('更新失败：' + (e?.response?.data?.detail || e.message))
  } finally { statusChanging.value = false }
}

// ── 网络接口 CRUD ─────────────────────────────────────────────
const ifaceDialogVisible = ref(false)
const ifaceSaving = ref(false)
const ifacePushToNode = ref(true)
const ifacePushingId = ref(null)
const editIface = ref(null)
const defaultIfaceForm = () => ({
  ifname:'', interface_type:'physical', mac_address:'', mtu:1500, speed_mbps:null,
  status:'up', is_management:false, description:'', vlan_id:null,
  addressesText:'', gateway4:'', dhcp4:false, vlan_link:'', bridge_ports:'',
})

function parseIfaceFormFromRow(row) {
  const cfg = row?.netplan_config || {}
  const spec = cfg.spec || {}
  const addrs = Array.isArray(spec.addresses) ? spec.addresses.join(', ') : (spec.addresses || '')
  return {
    ...defaultIfaceForm(),
    ...row,
    addressesText: addrs,
    gateway4: spec.gateway4 || '',
    dhcp4: !!spec.dhcp4,
    vlan_link: spec.link || '',
    bridge_ports: Array.isArray(spec.interfaces) ? spec.interfaces.join(', ') : '',
  }
}

function openIfaceDialog(row=null) {
  editIface.value = row
  ifaceForm.value = row ? parseIfaceFormFromRow(row) : defaultIfaceForm()
  ifacePushToNode.value = true
  ifaceDialogVisible.value = true
}

const ifaceForm = ref(defaultIfaceForm())

async function saveIface(andPush = false) {
  if (!ifaceForm.value.ifname) { ElMessage.warning('请输入接口名称'); return }
  const netplan_config = buildIfaceNetplanConfig(ifaceForm.value)
  if (andPush && !netplan_config.spec.addresses?.length && !netplan_config.spec.dhcp4) {
    ElMessage.warning('下发到节点需填写 IP 地址(CIDR) 或启用 DHCPv4')
    return
  }
  ifaceSaving.value = true
  const push = andPush || ifacePushToNode.value
  try {
    const payload = {
      ifname: ifaceForm.value.ifname,
      interface_type: ifaceForm.value.interface_type,
      mac_address: ifaceForm.value.mac_address || '',
      mtu: ifaceForm.value.mtu,
      speed_mbps: ifaceForm.value.speed_mbps,
      status: ifaceForm.value.status,
      is_management: ifaceForm.value.is_management,
      description: ifaceForm.value.description || '',
      vlan_id: ifaceForm.value.vlan_id || 0,
      netplan_config,
      pop_point: detail.value.id,
    }
    if (editIface.value) await updateInterface(editIface.value.id, payload, push)
    else await createInterface(payload, push)
    ElMessage.success(push ? '已保存并下发到节点' : (editIface.value ? '已更新' : '接口已创建'))
    ifaceDialogVisible.value = false
    loadDetail()
  } catch (e) {
    ElMessage.error('保存失败：' + (e?.response?.data?.detail || JSON.stringify(e?.response?.data) || e.message))
  } finally { ifaceSaving.value = false }
}

async function handlePushIface(row) {
  ifacePushingId.value = row.id
  try {
    await pushInterfaceToNode(row.id)
    ElMessage.success(`接口 ${row.ifname} 已下发到节点`)
    loadDetail()
  } catch (e) {
    ElMessage.error('下发失败：' + (e?.response?.data?.detail || e.message))
  } finally { ifacePushingId.value = null }
}
async function handleDeleteIface(row) {
  // 默认同步删除节点上的对应接口（隧道走 desired-tunnels，普通接口走 remove-config）；
  // 可选「仅删除记录」只移除本地档案（下次同步可能再次拉回）。
  let deleteOnNode = true
  try {
    await ElMessageBox.confirm(
      `删除接口 "${row.ifname}" 将同时删除节点上对应的接口/隧道配置并即时生效。\n` +
      `点「删除(含节点)」联动删除节点接口；点「仅删除记录」只移除本地档案。`,
      '删除接口',
      {
        type: 'warning',
        confirmButtonText: '删除(含节点)',
        cancelButtonText: '仅删除记录',
        distinguishCancelAndClose: true,
      },
    )
    deleteOnNode = true
  } catch (action) {
    if (action === 'close') return  // 右上角关闭/ESC：取消
    deleteOnNode = false            // 点了「仅删除记录」
  }
  try {
    await deleteInterface(row.id, deleteOnNode)
    ElMessage.success(deleteOnNode ? '已删除接口并下发到节点' : '已删除本地接口记录')
    loadDetail()
  } catch (e) {
    ElMessage.error('删除失败：' + (e?.response?.data?.detail || e.message))
  }
}

// ── 路由表 CRUD ───────────────────────────────────────────────
const routeTableDialogVisible = ref(false)
const routeTableSaving = ref(false)
const editRouteTable = ref(null)
const routeTableForm = ref({ table_id:null, name:'', description:'' })

function openRouteTableDialog(row=null) {
  editRouteTable.value = row
  routeTableForm.value = row
    ? { table_id:row.table_id, name:row.name||'', description:row.description||'' }
    : { table_id:null, name:'', description:'' }
  routeTableDialogVisible.value = true
}
async function saveRouteTable() {
  if (!routeTableForm.value.table_id) { ElMessage.warning('请输入路由表ID'); return }
  routeTableSaving.value = true
  try {
    const payload = { ...routeTableForm.value, pop_point: detail.value.id, owner_type: 'pop' }
    if (editRouteTable.value) await updateRouteTable(editRouteTable.value.id, payload)
    else await createRouteTable(payload)
    ElMessage.success(editRouteTable.value ? '已更新' : '路由表已创建')
    routeTableDialogVisible.value = false
    loadDetail()
  } catch (e) {
    ElMessage.error('保存失败：' + (e?.response?.data?.detail || JSON.stringify(e?.response?.data) || e.message))
  } finally { routeTableSaving.value = false }
}
async function handleDeleteRouteTable(row) {
  await ElMessageBox.confirm(`确定删除路由表 Table ${row.table_id}（${row.name||''}）及其所有路由？`, '警告', { type:'warning' })
  await deleteRouteTable(row.id)
  ElMessage.success('已删除')
  loadDetail()
}

// ── 静态路由 CRUD ─────────────────────────────────────────────
const staticRouteDialogVisible = ref(false)
const staticRouteSaving = ref(false)
const editStaticRoute = ref(null)
const currentRouteTable = ref(null)
const staticRouteForm = ref({ destination:'', gateway:'', interface:null, metric:0, is_active:true, description:'', push_to_node:true })

function openStaticRouteDialog(rt) {
  currentRouteTable.value = rt
  editStaticRoute.value = null
  staticRouteForm.value = { destination:'', gateway:'', interface:null, metric:0, is_active:true, description:'', push_to_node:true }
  staticRouteDialogVisible.value = true
}
function openStaticRouteEditDialog(row, rt) {
  currentRouteTable.value = rt
  editStaticRoute.value = row
  staticRouteForm.value = { destination:row.destination, gateway:row.gateway||'', interface:row.interface||null, metric:row.metric||0, is_active:row.is_active, description:row.description||'', push_to_node:true }
  staticRouteDialogVisible.value = true
}
async function saveStaticRoute() {
  if (!staticRouteForm.value.destination) { ElMessage.warning('请输入目标网络'); return }
  staticRouteSaving.value = true
  try {
    const pushToNode = !!staticRouteForm.value.push_to_node
    const payload = { ...staticRouteForm.value, route_table: currentRouteTable.value.id }
    delete payload.push_to_node
    if (!payload.gateway) delete payload.gateway
    if (!payload.interface) payload.interface = null
    if (editStaticRoute.value) await updateStaticRoute(editStaticRoute.value.id, payload, pushToNode)
    else await createStaticRoute(payload, pushToNode)
    ElMessage.success(pushToNode ? (editStaticRoute.value ? '已更新并尝试下发节点' : '路由已添加并尝试下发节点') : (editStaticRoute.value ? '已更新' : '路由已添加'))
    staticRouteDialogVisible.value = false
    loadDetail()
  } catch (e) {
    ElMessage.error('保存失败：' + (e?.response?.data?.detail || JSON.stringify(e?.response?.data) || e.message))
  } finally { staticRouteSaving.value = false }
}
async function handleDeleteStaticRoute(row) {
  await ElMessageBox.confirm(`确定删除路由 ${row.destination} → ${row.gateway||'(接口)'}？`, '警告', { type:'warning' })
  await deleteStaticRoute(row.id, true)
  ElMessage.success('已删除')
  loadDetail()
}

// ── 策略路由 CRUD ─────────────────────────────────────────────
const policyDialogVisible = ref(false)
const policySaving = ref(false)
const editPolicy = ref(null)
const policyForm = ref({ priority:100, src_prefix:'', dst_prefix:'', fwmark:'', lookup_table:null, action:'lookup', is_active:true, description:'', push_to_node:true })

function openPolicyDialog(row=null) {
  editPolicy.value = row
  policyForm.value = row
    ? { priority:row.priority, src_prefix:row.src_prefix||'', dst_prefix:row.dst_prefix||'', fwmark:row.fwmark||'', lookup_table:row.lookup_table||null, action:row.action, is_active:row.is_active, description:row.description||'', push_to_node:true }
    : { priority:100, src_prefix:'', dst_prefix:'', fwmark:'', lookup_table:null, action:'lookup', is_active:true, description:'', push_to_node:true }
  policyDialogVisible.value = true
}
async function savePolicy() {
  policySaving.value = true
  try {
    const pushToNode = !!policyForm.value.push_to_node
    const payload = { ...policyForm.value, pop_point: detail.value.id }
    delete payload.push_to_node
    if (!payload.fwmark) delete payload.fwmark
    if (!payload.dst_prefix) delete payload.dst_prefix
    if (!payload.lookup_table) delete payload.lookup_table
    if (editPolicy.value) await updatePolicyRoute(editPolicy.value.id, payload, pushToNode)
    else await createPolicyRoute(payload, pushToNode)
    ElMessage.success(pushToNode ? (editPolicy.value ? '已更新并尝试下发节点' : '策略路由已创建并尝试下发节点') : (editPolicy.value ? '已更新' : '策略路由已创建'))
    policyDialogVisible.value = false
    loadDetail()
  } catch (e) {
    ElMessage.error('保存失败：' + (e?.response?.data?.detail || JSON.stringify(e?.response?.data) || e.message))
  } finally { policySaving.value = false }
}
async function handleDeletePolicyRoute(row) {
  await ElMessageBox.confirm(`确定删除策略路由 prio ${row.priority}？`, '警告', { type:'warning' })
  await deletePolicyRoute(row.id, true)
  ElMessage.success('已删除')
  loadDetail()
}
</script>

<style scoped>
/* ── 整页容器 ── */
.pop-detail-page { background:#fff; border-radius:8px; padding:16px 20px 24px; min-height:calc(100vh - 120px); }
.page-header     { margin-bottom:16px; padding-bottom:14px; border-bottom:1px solid #ebeef5; }
.dh-back         { margin-right:4px; }
.dh-loading      { display:flex; align-items:center; }

/* ── 头部 ── */
.drawer-header  { display:flex; align-items:center; gap:12px; width:100%; flex-wrap:wrap; }
.dh-left        { display:flex; align-items:center; gap:6px; }
.dh-code        { font-size:13px; color:#909399; }
.dh-name        { font-size:16px; font-weight:700; color:#1a1a2e; }
.dh-stats       { display:flex; gap:0; margin-left:16px; }
.ds-item        { text-align:center; padding:4px 12px; border-right:1px solid #ebeef5; }
.ds-item:last-child { border-right:none; }
.ds-num         { display:block; font-size:18px; font-weight:700; color:#303133; line-height:1.2; }
.ds-lbl         { display:block; font-size:11px; color:#909399; }
.dh-actions     { margin-left:auto; display:flex; gap:6px; }

/* ── Tabs ── */
.detail-tabs    { height:100%; }
:deep(.el-tabs__header) { margin-bottom:12px; }

/* ── 带宽卡片 ── */
.bw-card  { background:#f8f9ff; }
.bw-title { font-weight:600; font-size:13px; color:#606266; }
.bw-row   { display:flex; align-items:center; gap:12px; }
.bw-labels{ display:flex; align-items:center; font-size:13px; color:#303133; white-space:nowrap; }
.bw-used  { font-weight:600; color:#409EFF; }
.bw-sep   { margin:0 4px; color:#C0C4CC; }
/* 分段进度条 */
.bw-stack { flex:1; height:14px; background:#ebeef5; border-radius:7px; overflow:hidden; display:flex; }
.bw-seg   { height:100%; transition:width .3s; }
.vps-seg  { background:#409EFF; }
.edge-seg { background:#67C23A; }

/* ── 信息卡片 ── */
.info-card { height:100%; }
:deep(.info-card .el-card__header) { font-weight:600; font-size:13px; padding:10px 14px; }

/* ── IP块卡片 ── */
.ip-block-card  { background:#f8faff; border:1px solid #e4e7ed; border-radius:8px; padding:12px 14px; margin-bottom:10px; }
.ipb-header     { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.ipb-desc       { font-size:12px; color:#909399; margin-top:6px; }

/* ── 路由表卡片 ── */
.route-table-card { border:1px solid #e4e7ed; border-radius:8px; padding:12px 14px; margin-bottom:12px; }
.rt-header { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.rt-name   { font-weight:600; font-size:13px; color:#303133; }

/* ── 日志 ── */
.log-item  { display:flex; align-items:center; flex-wrap:wrap; }
.log-desc  { font-size:13px; color:#303133; }

/* ── 到期颜色 ── */
.expire-red  { color:#F56C6C !important; font-weight:600; }
.expire-warn { color:#E6A23C !important; }
</style>
